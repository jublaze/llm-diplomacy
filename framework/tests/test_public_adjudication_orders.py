from __future__ import annotations

import json

from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.runner.scripted_experiment_runner import ScriptedExperimentRunner
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _movement_state() -> dict[str, object]:
    return {
        "schema_version": "1.0", "game_id": "g_public", "year": 1906, "season": "spring", "phase": "movement",
        "units": [
            {"unit_id": "eng_a_yor", "owner": "england", "type": "A", "province": "yor", "coast": None, "status": "active"},
            {"unit_id": "eng_f_nth", "owner": "england", "type": "F", "province": "nth", "coast": None, "status": "active"},
            {"unit_id": "ger_a_hol", "owner": "germany", "type": "A", "province": "hol", "coast": None, "status": "active"},
        ],
        "supply_ownership": {"yor": "england", "hol": "germany", "bel": None},
        "private_messages": [], "global_messages": [],
    }


def _public_resolution() -> tuple[dict[str, object], dict[str, object]]:
    engine = DiplomacyRuleEngine()
    state = _movement_state()
    orders = [
        {"order_id": "eng_convoy", "unit_ref": "eng_f_nth", "power": "england", "type": "CONVOY", "payload": {"from": "nth", "army_from": "yor", "army_to": "bel"}},
        {"order_id": "eng_move", "unit_ref": "eng_a_yor", "power": "england", "type": "MOVE", "payload": {"from": "yor", "to": "bel", "via_convoy": True}},
        {"order_id": "ger_wrong_support", "unit_ref": "ger_a_hol", "power": "germany", "type": "SUPPORT_MOVE", "payload": {"from": "hol", "supported_from": "nth", "supported_to": "bel"}},
    ]
    validated = engine.validate_orders(state, orders)
    resolution = engine.resolve_phase(state, validated)
    runner = object.__new__(ScriptedExperimentRunner)
    summary = runner._board_resolution_summary(
        phase_id="1906_spring_movement",
        input_state=state, output_state=resolution["output_state"], resolution=resolution,
        submitted_orders=orders, validated_orders=validated,
    )
    return resolution["output_state"], summary


def test_public_final_orders_are_visible_only_after_adjudication_and_show_convoy_support_mismatch(tmp_path) -> None:
    before = _movement_state()
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    pre_context = AgentContext(schema_version="1.0", experiment_id="exp", game_id="g_public", phase_id="1906_spring_movement", agent_id="agent_germany", country_assignment="germany", visible_state=before)
    assert builder.build(pre_context)["authoritative_board_update"]["available"] is False

    output_state, summary = _public_resolution()
    assert summary["phase_id"] == "1906_spring_movement"
    output_state["latest_public_resolution"] = summary
    expected = {row["notation"] for row in summary["public_final_orders"]}
    assert "F nth C A yor–bel" in expected
    assert "A yor–bel VIA CONVOY" in expected
    assert "A hol S F nth–bel" in expected
    assert all("retry" not in json.dumps(row).lower() and "provider" not in json.dumps(row).lower() for row in summary["public_final_orders"])

    for country in ("england", "germany"):
        context = AgentContext(schema_version="1.0", experiment_id="exp", game_id="g_public", phase_id="1906_spring_movement", agent_id=f"agent_{country}", country_assignment=country, visible_state=output_state)
        update = builder.build(context)["authoritative_board_update"]
        assert update["public_final_orders"] == summary["public_final_orders"]
    germany_context = AgentContext(schema_version="1.0", experiment_id="exp", game_id="g_public", phase_id="1906_spring_movement", agent_id="agent_germany", country_assignment="germany", visible_state=output_state)
    germany_prompt = builder.build_prompt(method="reflect", built_context=builder.build(germany_context))
    assert "A hol S F nth\\u2013bel" in germany_prompt
    assert "Outcome-review target: the just-adjudicated Spring 1906 Movement phase (`1906_spring_movement`). Review this adjudication and its public final orders, not an earlier phase." in germany_prompt


def test_prior_public_resolution_phase_id_survives_next_negotiation_and_orders_without_current_order_leakage(tmp_path) -> None:
    output_state, summary = _public_resolution()
    output_state.update({"year": 1906, "season": "fall", "phase": "movement", "latest_public_resolution": summary, "submitted_orders": [{"secret_current_order": "must_not_appear"}]})
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    context = AgentContext(schema_version="1.0", experiment_id="exp", game_id="g_public", phase_id="1906_fall_movement", agent_id="agent_england", country_assignment="england", visible_state=output_state)
    built = builder.build(context)
    assert built["authoritative_board_update"]["phase_id"] == "1906_spring_movement"
    for method in ("negotiate_tick", "submit_orders"):
        prompt = builder.build_prompt(method=method, built_context=built)
        assert "1906_spring_movement" in prompt
        assert "must_not_appear" not in prompt


def test_legacy_public_resolution_without_phase_id_is_readable_without_inventing_identity(tmp_path) -> None:
    state = _movement_state()
    state["latest_public_resolution"] = {"phase": "movement", "public_final_orders": []}
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    context = AgentContext(schema_version="1.0", experiment_id="exp", game_id="g_public", phase_id="1907_spring_movement", agent_id="agent_england", country_assignment="england", visible_state=state)
    prompt = builder.build_prompt(method="reflect", built_context=builder.build(context))
    assert "canonical phase_id is unavailable in this legacy context" in prompt
    assert "1907_spring_movement`). Review this adjudication" not in prompt


def test_default_effective_hold_is_public_without_internal_diagnostics() -> None:
    engine = DiplomacyRuleEngine()
    state = _movement_state()
    validated = engine.validate_orders(state, [])
    resolution = engine.resolve_phase(state, validated)
    runner = object.__new__(ScriptedExperimentRunner)
    summary = runner._board_resolution_summary(phase_id="1906_spring_movement", input_state=state, output_state=resolution["output_state"], resolution=resolution, submitted_orders=[], validated_orders=validated)
    assert all(row["effective_status"] == "defaulted" for row in summary["public_final_orders"])
    assert all(row["order_type"] == "HOLD" for row in summary["public_final_orders"])
    assert "provider" not in json.dumps(summary).lower() and "retry" not in json.dumps(summary).lower()
