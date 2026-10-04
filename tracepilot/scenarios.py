"""Deterministic runtime scenarios; these are not model-quality benchmarks."""

import json
from typing import Any

from tracepilot.models import Candidate, Claim, Decision, DecisionState, DiagnosisDraft, InvestigationScope, ReasoningResult, TaskMemory, ToolOutput, ToolSpec


SCENARIOS = ("db-pool", "slow-dependency", "no-data", "denied")


def demo_scope() -> InvestigationScope:
    return InvestigationScope(service="checkout", namespace="demo", start="2026-09-28T08:00:00Z", end="2026-09-28T08:15:00Z")


class ScenarioTools:
    def __init__(self, scenario: str):
        if scenario not in SCENARIOS:
            raise ValueError(f"Choose one of: {', '.join(SCENARIOS)}")
        self.scenario = scenario
        self.calls: list[Candidate] = []

    def catalog(self, refresh: bool = True) -> list[ToolSpec]:
        descriptions = {"service_instances": "Instance readiness and restarts", "service_traces": "Slow spans and originating instance",
                        "service_logs": "Scoped error logs", "service_metrics": "CPU and connection-pool metrics",
                        "service_config": "Non-secret configuration change history"}
        return [ToolSpec(name=name, description=text + " (simulated)", source="offline-scenario", retry_safe=True, parameters={
            "type": "object", "additionalProperties": False,
            "properties": {"service": {"const": "checkout"}, "namespace": {"const": "demo"},
                           "start": {"type": "string"}, "end": {"type": "string"}, "pod": {"type": "string"}},
            "required": ["service", "namespace", "start", "end"],
        }) for name, text in descriptions.items()]

    def execute(self, candidate: Candidate) -> ToolOutput:
        self.calls.append(candidate.model_copy(deep=True))
        if self.scenario == "denied" and candidate.tool == "service_traces":
            return ToolOutput(status="approval_required", error="Trace access requires operator approval")
        if self.scenario == "no-data":
            return ToolOutput(status="no_data", data={"simulated": True, "items": []})
        pool = self.scenario == "db-pool"
        data = {
            "service_instances": {"ready": 3, "desired": 3, "restarts": 0},
            "service_traces": {"trace_id": "fixture-01", "pod": "checkout-a", "dependency": "postgres" if pool else "inventory", "duration_ms": 3080},
            "service_logs": {"pod": "checkout-a", "trace_id": "fixture-01", "error": "connection_pool_timeout" if pool else "upstream_deadline_exceeded"},
            "service_metrics": {"pod": "checkout-a", "cpu_ratio": 0.19, "db_pool_pending": 47 if pool else 0, "db_pool_max": 10 if pool else 50},
            "service_config": {"db_pool_max": 10 if pool else 50, "previous_db_pool_max": 50, "changed_at": "2026-09-28T08:00:00Z"},
        }
        return ToolOutput(status="success", data={"simulated": True, **data[candidate.tool]})


def observations(state: DecisionState) -> dict[str, tuple[str, dict[str, Any]]]:
    result = {}
    for evidence in state.evidence:
        if evidence["status"] == "success" and not evidence["truncated"]:
            data = json.loads(evidence["preview"]).get("data")
            if isinstance(data, dict):
                result[evidence["tool"]] = (evidence["id"], data)
    return result


class ScenarioReasoner:
    """Scripted test double branching only on already-observed values."""

    @staticmethod
    def memory(state: DecisionState) -> TaskMemory:
        return TaskMemory(facts=[Claim(text=f"{name}: {json.dumps(data, ensure_ascii=False)}", evidence_ids=[eid])
                                 for name, (eid, data) in observations(state).items()])

    def reason(self, state: DecisionState) -> ReasoningResult:
        known = observations(state)
        attempted = {e["tool"] for e in state.evidence}
        trace = known.get("service_traces", ("", {}))[1]
        metrics = known.get("service_metrics", ("", {}))[1]
        args = (state.scope or demo_scope()).model_dump(mode="json")
        candidates = []

        def propose(name: str, direction: str, pod: str | None = None) -> None:
            if name not in attempted:
                candidates.append(Candidate(id=name, tool=name, arguments={**args, **({"pod": pod} if pod else {})},
                                            purpose=f"Collect {name} within incident scope", direction=direction))

        if not attempted:
            propose("service_instances", "instance-health")
            propose("service_traces", "request-path")
        elif trace:
            direction = "database" if trace.get("dependency") == "postgres" else "dependency"
            propose("service_logs", direction, trace.get("pod"))
            propose("service_metrics", direction, trace.get("pod"))
            if metrics.get("db_pool_pending", 0) > 0:
                propose("service_config", "configuration-change")
        else:
            propose("service_logs", "missing-evidence")
        return ReasoningResult(memory=self.memory(state), candidates=candidates)

    def report(self, state: DecisionState) -> DiagnosisDraft:
        known = observations(state)
        trace = known.get("service_traces", ("", {}))[1]
        logs = known.get("service_logs", ("", {}))[1]
        metrics = known.get("service_metrics", ("", {}))[1]
        config = known.get("service_config", ("", {}))[1]
        causes = []
        if logs.get("error") == "connection_pool_timeout" and metrics.get("db_pool_pending", 0) > 0 and config.get("db_pool_max", 0) < config.get("previous_db_pool_max", 0):
            causes.append(Claim(text="连接池上限降低可能导致 checkout-a 连接等待和请求超时。",
                                evidence_ids=[known[n][0] for n in ("service_traces", "service_logs", "service_metrics", "service_config")]))
        elif trace.get("dependency") == "inventory" and logs.get("error") == "upstream_deadline_exceeded":
            causes.append(Claim(text="checkout-a 到 inventory 的下游调用延迟可能导致请求超时。",
                                evidence_ids=[known["service_traces"][0], known["service_logs"][0]]))
        return DiagnosisDraft(memory=self.memory(state), causes=causes,
                              verification_steps=["在测试环境复现，对照同一时间窗口的日志、指标和调用链。"],
                              limitations=["确定性模拟；未调用真实模型，不能作为诊断准确率或生产性能数据。",
                                           "候选原因需要验证；下游内部问题需要单独授权调查范围。"])


class ScenarioDecisionProvider:
    def decide(self, state: DecisionState, actions: dict[str, Any]) -> Decision:
        choice = next((key for key in actions if key.startswith("call:")), None)
        if choice is None:
            last_plan = max((i for i, h in enumerate(state.history) if h.choice == "reason"), default=-1)
            changed = any(h.evidence_id for h in state.history[last_plan + 1:])
            choice = "reason" if changed and "reason" in actions else "finish"
        return Decision(choice=choice, confidence=1)
