"""A bounded decision loop. Model outputs never bypass runtime validation."""

import time
from typing import Any, Callable

from jsonschema import Draft202012Validator

from holmes.decision.models import (
    ActionRecord, Candidate, DecisionState, Diagnosis, Evidence,
    InvestigationResult, TaskStatus, ToolOutput, ToolSpec,
)
from holmes.decision.providers import CONTROL_CHOICES, DecisionProvider, Reasoner
from holmes.decision.store import EvidenceStore
from holmes.decision.tools import ToolGateway


READ_EVIDENCE = ToolSpec(
    name="agent_read_evidence", source="decision-agent",
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
    ):
        self.reasoner = reasoner
        self.decision_provider = decision_provider
        self.tools = tools
        self.store = store
        self.on_event = on_event
        self.clock = clock

    def _save(
        self, state: DecisionState, kind: str, *, evidence: Evidence | None = None,
        diagnosis: Diagnosis | None = None, **details: Any,
    ) -> None:
        event = {"kind": kind, "task_id": state.task_id, **details}
        # Persistence is mandatory: do not continue an unaudited investigation.
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
        # Never fetch arbitrary remote $refs from a tool-provided schema.
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

    def _reason(self, state: DecisionState, catalog: dict[str, ToolSpec]) -> bool:
        if state.reasoning_calls >= state.budget.max_reasoning_calls:
            self._stop(state, "stopped", "reasoning_budget")
            return False
        state.reasoning_calls += 1
        try:
            result = self.reasoner.reason(state.model_copy(deep=True))
            result.memory.validate_references({e["id"] for e in state.evidence})
            ids = [c.id for c in result.candidates]
            if len(set(ids)) != len(ids):
                raise ValueError("Candidate IDs must be unique")
            attempted = {h.fingerprint for h in state.history if h.fingerprint}
            valid = []
            fingerprints: set[str] = set()
            for candidate in result.candidates:
                self._validate_candidate(candidate, catalog)
                if candidate.fingerprint in attempted or candidate.fingerprint in fingerprints:
                    continue
                fingerprints.add(candidate.fingerprint)
                candidate.tool_version = catalog[candidate.tool].version
                valid.append(candidate)
        except Exception as exc:
            state.candidates = []
            state.history.append(ActionRecord(choice="reason", status=f"invalid:{type(exc).__name__}",
                                              feedback=str(exc)[:2000]))
            self._save(state, "reasoning_rejected", error_type=type(exc).__name__)
            return True
        state.memory = result.memory
        state.candidates = valid
        state.history.append(ActionRecord(choice="reason", status="success"))
        self._save(state, "reasoning", candidates=len(valid))
        return True

    def _stop(self, state: DecisionState, status: TaskStatus, reason: str) -> None:
        state.status = status
        state.stop_reason = reason
        self._save(state, "stopped", reason=reason)

    def _execute(self, state: DecisionState, candidate: Candidate, confidence: float, deadline: float) -> None:
        # Re-discover after the decision: a remote MCP schema/tool may have changed.
        try:
            current = self._refresh(state)
            self._validate_candidate(candidate, current)
            if current[candidate.tool].version != candidate.tool_version:
                raise ValueError("Tool schema changed during the decision")
        except Exception as exc:
            state.candidates = []
            state.history.append(ActionRecord(choice=f"call:{candidate.id}", status="stale_tool"))
            self._save(state, "tool_invalidated", tool=candidate.tool, error_type=type(exc).__name__)
            return
        if candidate.fingerprint in {h.fingerprint for h in state.history if h.fingerprint}:
            state.candidates = []
            self._save(state, "duplicate_blocked", tool=candidate.tool)
            return
        if self.clock() >= deadline:
            self._stop(state, "stopped", "time_budget")
            return

        old_direction = state.direction
        state.direction = candidate.direction
        state.tool_calls += 1
        # Record intent before I/O. No automatic replay after a process crash.
        state.history.append(ActionRecord(
            choice=f"call:{candidate.id}", confidence=confidence,
            direction=candidate.direction, fingerprint=candidate.fingerprint, status="started",
        ))
        self._save(state, "tool_started", tool=candidate.tool, direction=candidate.direction,
                   previous_direction=old_direction)
        started = self.clock()
        try:
            if candidate.tool == READ_EVIDENCE.name:
                output = ToolOutput(status="success", data=self.store.read_evidence(
                    state.task_id, **candidate.arguments,
                ))
            else:
                output = self.tools.execute(candidate)
        except Exception as exc:
            output = ToolOutput(status="error", error=f"{type(exc).__name__}: {exc}")
        evidence = Evidence(
            task_id=state.task_id, tool=candidate.tool, source=current[candidate.tool].source,
            arguments=candidate.arguments, output=output,
            elapsed_seconds=max(0, self.clock() - started),
        )
        # A requested evidence page must reach the model intact. Applying the
        # normal 3K preview here would silently truncate the page a second time.
        preview_limit = 30000 if candidate.tool == READ_EVIDENCE.name else 3000
        summary = evidence.summary(max_chars=preview_limit)
        previous_hashes = {e["sha256"] for e in state.evidence}
        if output.status != "success" or summary["sha256"] in previous_hashes:
            state.no_progress += 1
        else:
            state.no_progress = 0
        state.evidence.append(summary)
        state.history[-1].status = output.status
        state.history[-1].evidence_id = evidence.id
        state.candidates = [c for c in state.candidates if c.fingerprint != candidate.fingerprint]
        self._save(state, "evidence", evidence=evidence, evidence_id=evidence.id,
                   tool=candidate.tool, status=output.status)
        if output.status == "approval_required":
            self._stop(state, "needs_input", "tool_requires_approval")

    def run(self, state: DecisionState) -> InvestigationResult:
        if state.status != "running" or state.revision or state.history:
            raise ValueError("run requires a new task; existing tasks can be inspected, not replayed")
        self.store.create(state)
        started = self.clock()
        while state.status == "running":
            if self.clock() - started >= state.budget.max_seconds:
                self._stop(state, "stopped", "time_budget")
                break
            if state.steps >= state.budget.max_steps:
                self._stop(state, "stopped", "step_budget")
                break
            if state.tool_calls >= state.budget.max_tool_calls:
                self._stop(state, "stopped", "tool_budget")
                break
            if state.no_progress >= state.budget.max_no_progress:
                self._stop(state, "stopped", "no_progress")
                break
            state.steps += 1
            try:
                catalog = self._refresh(state)
            except Exception as exc:
                self._stop(state, "failed", f"tool_catalog_error:{type(exc).__name__}")
                break
            if self.clock() - started >= state.budget.max_seconds:
                self._stop(state, "stopped", "time_budget")
                break
            previous_count = len(state.candidates)
            state.candidates = [
                c for c in state.candidates
                if c.tool in catalog and c.tool_version == catalog[c.tool].version
            ]
            if previous_count != len(state.candidates):
                self._save(state, "catalog_changed")
            if not state.reasoning_calls or not state.candidates:
                if not self._reason(state, catalog):
                    break
                # Invalid model JSON does not authorize a decision or execution.
                if state.history[-1].status.startswith("invalid:"):
                    continue
            if self.clock() - started >= state.budget.max_seconds:
                self._stop(state, "stopped", "time_budget")
                break
            try:
                decision = self.decision_provider.decide(state.model_copy(deep=True))
                allowed = set(CONTROL_CHOICES) | {f"call:{c.id}" for c in state.candidates}
                if decision.choice not in allowed:
                    raise ValueError("Decision selected an unavailable action")
            except Exception as exc:
                self._stop(state, "failed", f"decision_error:{type(exc).__name__}")
                break
            self._save(state, "decision", **decision.model_dump())
            if self.clock() - started >= state.budget.max_seconds:
                self._stop(state, "stopped", "time_budget")
                break
            if decision.confidence < state.budget.min_confidence:
                state.history.append(ActionRecord(choice=decision.choice, confidence=decision.confidence,
                                                  status="low_confidence"))
                state.candidates = []
                self._save(state, "low_confidence")
                continue
            if decision.choice == "reason":
                self._reason(state, catalog)
            elif decision.choice == "need_input":
                self._stop(state, "needs_input", "missing_information")
            elif decision.choice == "finish":
                observed = any(e["status"] == "success" and e["tool"] != READ_EVIDENCE.name
                               for e in state.evidence)
                self._stop(state, "completed" if observed else "needs_input",
                           "evidence_sufficient" if observed else "insufficient_evidence")
            else:
                candidate = next(c for c in state.candidates if f"call:{c.id}" == decision.choice)
                self._execute(state, candidate, decision.confidence, started + state.budget.max_seconds)

        try:
            diagnosis = self.reasoner.report(state.model_copy(deep=True))
            diagnosis.validate_references({e["id"] for e in state.evidence
                                           if e["status"] in {"success", "no_data"}
                                           and e["tool"] != READ_EVIDENCE.name})
        except Exception as exc:
            original_stop = state.stop_reason
            state.status = "failed"
            state.stop_reason = "report_validation_failed"
            diagnosis = Diagnosis(
                summary="Investigation ended; a validated diagnosis could not be generated.",
                limitations=[original_stop, f"Report rejected: {type(exc).__name__}"],
                verification_steps=["Inspect the persisted evidence and audit events before drawing conclusions."],
            )
        self._save(state, "report", diagnosis=diagnosis)
        return InvestigationResult(state=state, diagnosis=diagnosis)
