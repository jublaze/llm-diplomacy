from __future__ import annotations

from llm_diplomacy.agents.contracts import AgentContext, CleanupResult, OrderSet, ReflectionResult, TickResult
from llm_diplomacy.agents.runtime import AgentRuntime


class StubRuntime(AgentRuntime):
    def negotiate_tick(self, context: AgentContext) -> TickResult:
        return TickResult(schema_version="1.0", ok=True, messages=[{"to": {"type": "global"}, "body": "x"}], usage={"tokens": 1})

    def submit_orders(self, context: AgentContext) -> OrderSet:
        return OrderSet(schema_version="1.0", ok=False, orders=[], usage={"tokens": 2})

    def reflect(self, context: AgentContext) -> ReflectionResult:
        return ReflectionResult(schema_version="1.0", ok=True, reflection={"note": "done"}, usage={"tokens": 3})

    def game_end_cleanup(self, context: AgentContext) -> CleanupResult:
        return CleanupResult(schema_version="1.0", ok=True, summary={"final": "ok"}, usage={"tokens": 4})


def test_runtime_contract_status_and_usage_envelopes() -> None:
    runtime = StubRuntime()
    ctx = AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g1",
        phase_id="1901_spring_negotiation",
        agent_id="a1",
        country_assignment="france",
        visible_state={},
    )
    tick = runtime.negotiate_tick(ctx)
    orders = runtime.submit_orders(ctx)
    reflection = runtime.reflect(ctx)
    cleanup = runtime.game_end_cleanup(ctx)

    assert tick.schema_version == "1.0" and isinstance(tick.usage, dict)
    assert orders.schema_version == "1.0" and isinstance(orders.usage, dict)
    assert reflection.schema_version == "1.0" and isinstance(reflection.usage, dict)
    assert cleanup.schema_version == "1.0" and isinstance(cleanup.usage, dict)
