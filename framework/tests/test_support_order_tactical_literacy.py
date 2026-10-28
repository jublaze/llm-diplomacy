from __future__ import annotations

from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _state() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "game_id": "g_support",
        "year": 1901,
        "season": "spring",
        "phase": "movement",
        "units": [
            {"unit_id": "unit_0001", "owner": "germany", "type": "A", "province": "ruh", "coast": None, "status": "active"},
            {"unit_id": "unit_0002", "owner": "france", "type": "A", "province": "bur", "coast": None, "status": "active"},
        ],
        "supply_ownership": {"ruh": "germany", "bur": "france", "mun": "germany"},
        "private_messages": [],
        "global_messages": [],
    }


def test_canonical_primer_includes_factual_support_mechanics(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    context = AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g_support",
        phase_id="1901_spring_movement",
        agent_id="agent_germany",
        country_assignment="germany",
        visible_state=_state(),
    )
    prompt = builder.build_prompt(method="submit_orders", built_context=builder.build(context))
    assert "## Support Orders" in prompt
    assert "same power or a different power" in prompt
    assert "legality and support-cut rules" in prompt


def test_order_context_lists_board_derived_legal_support_actions(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    context = AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g_support",
        phase_id="1901_spring_movement",
        agent_id="agent_germany",
        country_assignment="germany",
        visible_state=_state(),
    )
    legal_context = builder.build(context)["legal_order_context"]
    ruh = next(row for row in legal_context["your_units"] if row["unit_ref"] == "unit_0001")
    assert {"type": "SUPPORT_HOLD", "payload": {"target": "bur"}} in ruh["legal_orders"]
    assert {
        "type": "SUPPORT_MOVE",
        "payload": {"supported_from": "bur", "supported_to": "mun"},
    } in ruh["legal_orders"]
    assert all(order["type"] != "SUPPORT_MOVE" or order["payload"]["supported_from"] != "ruh" for order in ruh["legal_orders"])


def test_legal_cross_power_support_is_accepted_and_effective() -> None:
    engine = DiplomacyRuleEngine()
    state = _state()
    orders = [
        {"order_id": "france_move", "unit_ref": "unit_0002", "power": "france", "type": "MOVE", "payload": {"from": "bur", "to": "mun"}},
        {"order_id": "germany_support", "unit_ref": "unit_0001", "power": "germany", "type": "SUPPORT_MOVE", "payload": {"from": "ruh", "supported_from": "bur", "supported_to": "mun"}},
    ]
    validated = engine.validate_orders(state, orders)
    assert all(row["validation"] == "accepted" for row in validated["validated_orders"])
    resolved = engine.resolve_phase(state, validated)
    assert next(row for row in resolved["movement_outcomes"] if row["unit_id"] == "unit_0002")["result"] == "moved"
    assert next(row for row in resolved["support_outcomes"] if row["unit_id"] == "unit_0001")["status"] == "effective"


def test_illegal_support_remains_rejected() -> None:
    engine = DiplomacyRuleEngine()
    state = _state()
    validated = engine.validate_orders(
        state,
        [{"order_id": "illegal_support", "unit_ref": "unit_0001", "power": "germany", "type": "SUPPORT_MOVE", "payload": {"from": "ruh", "supported_from": "bur", "supported_to": "mar"}}],
    )
    row = next(row for row in validated["validated_orders"] if row["unit_id"] == "unit_0001")
    assert row["validation"] == "substituted"
    assert row["reason"] == "illegal_support_move_reach"
