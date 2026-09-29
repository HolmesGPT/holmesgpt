"""Explicitly simulated offline example; no Jev or LLM calls are made here."""

from holmes.decision.models import (
    Candidate, Claim, Decision, DecisionState, Diagnosis, ReasoningResult,
    TaskMemory, ToolOutput, ToolSpec,
)


class DemoTools:
    observations = {
        "service_logs": {"service": "checkout", "errors": [
            "SQLTransientConnectionException: Connection is not available, request timed out after 3000ms"
        ]},
        "service_metrics": {"service": "checkout", "db_pool_active": 10,
                            "db_pool_max": 10, "db_pool_pending": 47, "cpu_ratio": 0.19},
        "service_config": {"service": "checkout", "db_pool_max": 10,
                           "previous_db_pool_max": 50, "changed_at": "2026-09-28T08:00:00Z"},
        "service_instances": {"service": "checkout", "ready": 3, "desired": 3, "restarts": 0},
    }

    def catalog(self, refresh: bool = True) -> list[ToolSpec]:
        return [ToolSpec(
            name=name, description=f"Simulated {name} observation for checkout",
            source="offline-fixture",
            parameters={"type": "object", "properties": {"service": {"const": "checkout"}},
                        "required": ["service"], "additionalProperties": False},
        ) for name in self.observations]

    def execute(self, candidate: Candidate) -> ToolOutput:
        return ToolOutput(status="success", data=self.observations[candidate.tool])


class DemoReasoner:
    def reason(self, state: DecisionState) -> ReasoningResult:
        observed = {e["tool"] for e in state.evidence}
        facts = [Claim(text=f"Collected {e['tool']} observation", evidence_ids=[e["id"]])
                 for e in state.evidence]
        return ReasoningResult(
            memory=TaskMemory(facts=facts),
            candidates=[Candidate(
                id=name, tool=name, arguments={"service": "checkout"},
                purpose=f"Inspect {name}", direction="database" if "config" in name else "service-health",
            ) for name in DemoTools.observations if name not in observed],
        )

    def report(self, state: DecisionState) -> Diagnosis:
        ids = [e["id"] for e in state.evidence if e["status"] == "success"]
        return Diagnosis(
            summary="演示：checkout 超时与数据库连接池耗尽一致，配置中的连接数由 50 降为 10。",
            causes=[Claim(text="候选原因：连接池上限降低导致连接等待。", evidence_ids=ids)] if ids else [],
            verification_steps=["在测试环境恢复连接池配置，比较等待数和接口延迟。"],
            limitations=["这是确定性模拟数据；未调用 Jev、LLM 或生产数据源，未证明生产根因。"],
        )


class DemoDecisionProvider:
    def decide(self, state: DecisionState) -> Decision:
        choice = f"call:{state.candidates[0].id}" if state.candidates else "finish"
        return Decision(choice=choice, confidence=1)
