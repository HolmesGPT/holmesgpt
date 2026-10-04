"""A bounded decision loop. Model outputs never bypass runtime validation."""

import copy
import time
from typing import Any, Callable
from uuid import uuid4

from jsonschema import Draft202012Validator
from tenacity import RetryCallState, Retrying, retry_if_result, wait_random_exponential

from tracepilot.errors import TRANSIENT_TOOL_ERRORS
from tracepilot.models import (
    ActionRecord, Candidate, DecisionState, Diagnosis, DiagnosisDraft, Evidence,
    InvestigationResult, TerminalStatus, ToolOutput, ToolSpec,
)
from tracepilot.policy import available_actions, can_attempt, can_reason
from tracepilot.providers import DecisionProvider, Reasoner
from tracepilot.reporting import render_diagnosis
from tracepilot.store import EvidenceStore
from tracepilot.gateway import ToolGateway, tool_error_output
from tracepilot.validation import validator


READ_EVIDENCE = ToolSpec(
    name="agent_read_evidence", source="decision-agent", retry_safe=True,
    description="Read a page of a previously collected raw tool result in this task by evidence ID.",
    parameters={
        "type": "object", "additionalProperties": False,
        "properties": {
            "evidence_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12000},
        },
        "required": ["evidence_id", "offset", "limit"],
    },
)


class DecisionAgent:
    def __init__(
        self, reasoner: Reasoner, decision_provider: DecisionProvider,
        tools: ToolGateway, store: EvidenceStore,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        retry_wait: Callable[[RetryCallState], float] | None = None,
    ):
        self.reasoner = reasoner
        self.decision_provider = decision_provider
        self.tools = tools
        self.store = store
        self.on_event = on_event
        self.clock = clock
        self.sleep = sleep
        self.retry_wait = retry_wait or wait_random_exponential(multiplier=0.5, max=4)

    def _save(
        self, state: DecisionState, kind: str, *, evidence: Evidence | None = None,
        diagnosis: Diagnosis | None = None, **details: Any,
    ) -> None:
        event = {"kind": kind, "task_id": state.task_id, **details}
        self.store.checkpoint(state, event=event, evidence=evidence, diagnosis=diagnosis)
        if self.on_event:
            self.on_event(event)

    def _refresh(self, state: DecisionState) -> dict[str, ToolSpec]:
        catalog = self.tools.catalog(refresh=True)
        names = [tool.name for tool in catalog]
        if len(names) != len(set(names)) or READ_EVIDENCE.name in names:
            raise ValueError("Tool names must be unique; agent_read_evidence is reserved")
        if len(catalog) > 100:
            raise ValueError("Too many tools; narrow the session with --allow-tool")
        state.available_tools = [*catalog, READ_EVIDENCE]
        return {tool.name: tool for tool in state.available_tools}

    @staticmethod
    def _validate_candidate(candidate: Candidate, tools: dict[str, ToolSpec]) -> None:
        spec = tools.get(candidate.tool)
        if spec is None:
            raise ValueError(f"Unavailable tool: {candidate.tool}")

        def check_refs(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"$ref", "$dynamicRef"} and isinstance(child, str) and not child.startswith("#"):
                        raise ValueError("External schema references are unsupported")
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)

        check_refs(spec.parameters)
        Draft202012Validator.check_schema(spec.parameters)
        Draft202012Validator(spec.parameters).validate(candidate.arguments)

    @staticmethod
    def _observation_ids(state: DecisionState) -> set[str]:
        return {e["id"] for e in state.evidence
                if e["status"] in {"success", "no_data"} and e["tool"] != READ_EVIDENCE.name}

    def _reason(self, state: DecisionState, catalog: dict[str, ToolSpec]) -> None:
        if not can_reason(state):
            raise ValueError("Reasoning is not an available action")
        state.reasoning_calls += 1
        previous = {candidate.fingerprint for candidate in state.candidates}
        try:
            result = self.reasoner.reason(state.model_copy(deep=True))
            result.memory.validate_references(self._observation_ids(state))
            ids = [candidate.id for candidate in result.candidates]
            if len(set(ids)) != len(ids):
                raise ValueError("Candidate IDs must be unique")
            valid = []
            fingerprints: set[str] = set()
            for candidate in result.candidates:
                self._validate_candidate(candidate, catalog)
                candidate.tool_version = catalog[candidate.tool].version
                if candidate.fingerprint in fingerprints or not can_attempt(state, candidate, catalog[candidate.tool]):
                    continue
                fingerprints.add(candidate.fingerprint)
                valid.append(candidate)
        except Exception as exc:
            # Keep any existing valid plan; malformed replacement output grants no new actions.
            state.no_progress += 1
            state.history.append(ActionRecord(choice="reason", status=f"invalid:{type(exc).__name__}",
                                              feedback=str(exc)[:2000]))
            self._save(state, "reasoning_rejected", error_type=type(exc).__name__)
            return
        state.memory = result.memory
        state.candidates = valid
        state.no_progress = 0 if fingerprints - previous else state.no_progress + 1
        state.history.append(ActionRecord(choice="reason", status="success"))
        self._save(state, "reasoning", candidates=len(valid), stalled_plans=state.no_progress)

    def _stop(self, state: DecisionState, status: TerminalStatus, reason: str) -> None:
        state.pending_status = status
        state.stop_reason = reason
        self._save(state, "stop_requested", requested_status=status, reason=reason)

    def _attempt(
        self, state: DecisionState, candidate: Candidate, confidence: float, deadline: float,
    ) -> ToolOutput | None:
        # Revalidate every attempt, including retries after backoff.
        if self.clock() >= deadline:
            self._stop(state, "stopped", "time_budget")
            return None
        try:
            current = self._refresh(state)
            self._validate_candidate(candidate, current)
            if current[candidate.tool].version != candidate.tool_version:
                raise ValueError("Tool schema or retry policy changed during the decision")
        except Exception as exc:
            state.candidates = [item for item in state.candidates if item.tool != candidate.tool]
            state.history.append(ActionRecord(choice=f"call:{candidate.id}", status="stale_tool"))
            self._save(state, "tool_invalidated", tool=candidate.tool, error_type=type(exc).__name__)
            return None
        if not can_attempt(state, candidate, current[candidate.tool]):
            self._save(state, "call_blocked", tool=candidate.tool)
            return None
        if self.clock() >= deadline:
            self._stop(state, "stopped", "time_budget")
            return None

        old_direction = state.direction
        state.direction = candidate.direction
        state.tool_calls += 1
        action = ActionRecord(
            choice=f"call:{candidate.id}", confidence=confidence, direction=candidate.direction,
            fingerprint=candidate.fingerprint, status="started", attempt_id=str(uuid4()),
            attempt_no=1 + sum(item.fingerprint == candidate.fingerprint for item in state.history),
        )
        state.history.append(action)
        # Mandatory intent checkpoint before I/O; database errors never enter a tool retry.
        self._save(state, "tool_started", tool=candidate.tool, direction=candidate.direction,
                   previous_direction=old_direction, attempt_id=action.attempt_id, attempt_no=action.attempt_no)
        started = self.clock()
        try:
            if candidate.tool == READ_EVIDENCE.name:
                output = ToolOutput(status="success", data=self.store.read_evidence(state.task_id, **candidate.arguments))
            elif current[candidate.tool].approval_required:
                output = ToolOutput(status="approval_required", error="Operator approval is required")
            else:
                output = self.tools.execute(candidate)
                output_schema = current[candidate.tool].output_schema
                if output.status == "success" and output_schema is not None:
                    try:
                        validator(output_schema).validate(output.data)
                    except Exception:
                        output = ToolOutput(status="error", error="Tool result failed its output schema", error_kind="unknown")
        except Exception as exc:
            output = tool_error_output(exc)
        evidence = Evidence(
            task_id=state.task_id, tool=candidate.tool, source=current[candidate.tool].source,
            arguments=candidate.arguments, output=output, elapsed_seconds=max(0, self.clock() - started),
            attempt_id=action.attempt_id, attempt_no=action.attempt_no,
        )
        preview_limit = 30000 if candidate.tool == READ_EVIDENCE.name else 3000
        state.evidence.append(evidence.summary(max_chars=preview_limit))
        action.status = output.status
        action.error_kind = output.error_kind
        action.evidence_id = evidence.id
        # A completed query is coverage even when it found no matches. Different
        # sources returning the same payload must not erase each other's progress.
        if output.status in {"success", "no_data"}:
            state.no_progress = 0
        self._save(state, "evidence", evidence=evidence, evidence_id=evidence.id,
                   tool=candidate.tool, status=output.status, attempt_id=action.attempt_id,
                   attempt_no=action.attempt_no, error_kind=output.error_kind)
        if output.status == "approval_required":
            self._stop(state, "needs_input", "tool_requires_approval")
        return output

    def _execute(self, state: DecisionState, candidate: Candidate, confidence: float, deadline: float) -> None:
        spec = next(tool for tool in state.available_tools if tool.name == candidate.tool)
        remaining = state.budget.max_tool_attempts - sum(
            item.fingerprint == candidate.fingerprint for item in state.history
        )

        def retryable(output: ToolOutput | None) -> bool:
            return (output is not None and output.status == "error" and spec.retry_safe
                    and output.error_kind in TRANSIENT_TOOL_ERRORS and state.pending_status is None)

        def wait(retry_state: RetryCallState) -> float:
            assert retry_state.outcome is not None
            output = retry_state.outcome.result()
            return max(self.retry_wait(retry_state), output.retry_after_seconds or 0)

        def stop(retry_state: RetryCallState) -> bool:
            return (retry_state.attempt_number >= remaining
                    or state.tool_calls >= state.budget.max_tool_calls
                    or self.clock() + retry_state.upcoming_sleep >= deadline)

        def before_sleep(retry_state: RetryCallState) -> None:
            self._save(state, "tool_retry_scheduled", tool=candidate.tool,
                       fingerprint=candidate.fingerprint, wait_seconds=retry_state.upcoming_sleep)

        def exhausted(retry_state: RetryCallState) -> ToolOutput | None:
            assert retry_state.outcome is not None
            return retry_state.outcome.result()

        retryer = Retrying(
            retry=retry_if_result(retryable), wait=wait, stop=stop, sleep=self.sleep,
            before_sleep=before_sleep, retry_error_callback=exhausted,
        )
        output = retryer(self._attempt, state, candidate, confidence, deadline)
        if retryable(output):
            # Do not let a new candidate ID reset attempts or bypass Retry-After
            # when another retry could not fit inside the remaining budget.
            last = next(item for item in reversed(state.history) if item.fingerprint == candidate.fingerprint)
            last.retry_exhausted = True
            self._save(state, "tool_retry_exhausted", tool=candidate.tool, fingerprint=candidate.fingerprint)
        state.candidates = [item for item in state.candidates if item.fingerprint != candidate.fingerprint]

    def run(self, state: DecisionState) -> InvestigationResult:
        if (state.status != "running" or state.revision or state.history or state.evidence
                or state.candidates or state.reasoning_calls or state.tool_calls or state.pending_status):
            raise ValueError("run requires a new task; existing tasks can be inspected, not replayed")
        self.store.create(state)
        deadline = self.clock() + state.budget.max_seconds
        while state.pending_status is None:
            if self.clock() >= deadline:
                self._stop(state, "stopped", "time_budget")
                break
            if state.steps >= state.budget.max_steps:
                self._stop(state, "stopped", "step_budget")
                break
            state.steps += 1
            try:
                catalog = self._refresh(state)
            except Exception as exc:
                self._stop(state, "failed", f"tool_catalog_error:{type(exc).__name__}")
                break
            if self.clock() >= deadline:
                self._stop(state, "stopped", "time_budget")
                break
            previous_count = len(state.candidates)
            state.candidates = [candidate for candidate in state.candidates
                                if candidate.tool in catalog and can_attempt(state, candidate, catalog[candidate.tool])]
            if previous_count != len(state.candidates):
                self._save(state, "candidates_filtered")
            # Bootstrap once; an empty plan thereafter still permits finish/need_input.
            if not state.reasoning_calls:
                self._reason(state, catalog)
            if self.clock() >= deadline:
                self._stop(state, "stopped", "time_budget")
                break
            actions = available_actions(state)
            try:
                decision = self.decision_provider.decide(state.model_copy(deep=True), copy.deepcopy(actions))
                if decision.choice not in actions:
                    raise ValueError("Decision selected an unavailable action")
            except Exception as exc:
                self._stop(state, "failed", f"decision_error:{type(exc).__name__}")
                break
            self._save(state, "decision", allowed_actions=list(actions), **decision.model_dump())
            if self.clock() >= deadline:
                self._stop(state, "stopped", "time_budget")
                break
            if decision.confidence < state.budget.min_confidence:
                state.history.append(ActionRecord(choice=decision.choice, confidence=decision.confidence,
                                                  status="low_confidence"))
                self._save(state, "low_confidence")
                if can_reason(state):
                    self._reason(state, catalog)
                else:
                    self._stop(state, "stopped", "low_confidence")
                continue
            if decision.choice == "reason":
                self._reason(state, catalog)
            elif decision.choice == "need_input":
                self._stop(state, "needs_input", "missing_information")
            elif decision.choice == "finish":
                self._stop(state, "completed", "decision_finish")
            else:
                candidate = next(item for item in state.candidates if f"call:{item.id}" == decision.choice)
                self._execute(state, candidate, decision.confidence, deadline)

        try:
            if not self.store.verify(state.task_id)["valid"]:
                raise ValueError("Persisted evidence failed integrity checks")
            draft = DiagnosisDraft.model_validate(self.reasoner.report(state.model_copy(deep=True)))
            diagnosis = render_diagnosis(draft, state, self._observation_ids(state))
            state.memory = draft.memory
        except Exception as exc:
            original_stop = state.stop_reason
            state.pending_status = "failed"
            state.stop_reason = "report_validation_failed"
            diagnosis = Diagnosis(
                summary="Investigation ended; a validated diagnosis could not be generated.",
                limitations=[original_stop, f"Report rejected: {type(exc).__name__}"],
                verification_steps=["Inspect the persisted evidence and audit events before drawing conclusions."],
                schema_version=2, outcome="invalid_report",
            )
        assert state.pending_status is not None
        state.status = state.pending_status
        state.pending_status = None
        self._save(state, "report", diagnosis=diagnosis)
        return InvestigationResult(state=state, diagnosis=diagnosis)
