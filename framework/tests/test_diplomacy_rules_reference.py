from __future__ import annotations

from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.core.prompt_assets import DEFAULT_DIPLOMACY_PRIMER, DIPLOMACY_RULES_REFERENCE_VERSION
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _state() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "game_id": "g_rules_reference",
        "year": 1901,
        "season": "spring",
        "phase": "movement",
        "units": [
            {"unit_id": "unit_0001", "owner": "england", "type": "A", "province": "lon", "coast": None, "status": "active"},
            {"unit_id": "unit_0002", "owner": "england", "type": "F", "province": "eng", "coast": None, "status": "active"},
        ],
        "supply_ownership": {"lon": "england"},
        "private_messages": [],
        "global_messages": [],
    }


def _context() -> AgentContext:
    return AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g_rules_reference",
        phase_id="1901_spring_movement",
        agent_id="agent_england",
        country_assignment="england",
        visible_state=_state(),
    )


def _multifleet_state() -> dict[str, object]:
    state = _state()
    state["units"] = [
        {"unit_id": "unit_0001", "owner": "england", "type": "A", "province": "lon", "coast": None, "status": "active"},
        {"unit_id": "unit_0002", "owner": "england", "type": "F", "province": "eng", "coast": None, "status": "active"},
        {"unit_id": "unit_0003", "owner": "france", "type": "F", "province": "mao", "coast": None, "status": "active"},
        {"unit_id": "unit_0004", "owner": "russia", "type": "F", "province": "wes", "coast": None, "status": "active"},
        {"unit_id": "unit_0005", "owner": "england", "type": "F", "province": "ion", "coast": None, "status": "active"},
    ]
    return state


def _multifleet_context() -> AgentContext:
    return AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g_rules_reference",
        phase_id="1901_spring_movement",
        agent_id="agent_england",
        country_assignment="england",
        visible_state=_multifleet_state(),
    )


def test_complete_rules_reference_appears_in_order_prompt(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    prompt = builder.build_prompt(method="submit_orders", built_context=builder.build(_context()))

    for heading in (
        "# Diplomacy Rules Reference",
        "rules_reference_version: diplomacy_rules_reference_v5_convoyed_army_support_public_orders",
        "## Objective and Board",
        "## Year, Phases, and Authority",
        "## Movement and Adjudication",
        "## Support Orders",
        "## Convoys",
        "## Retreats",
        "## Supply Centers and Winter Adjustment",
        "## Diplomacy and Evidence",
    ):
        assert heading in prompt
    assert "engine-generated legal-order context override this prose" in prompt
    assert "# Legal Order Context" in prompt


def test_cross_power_support_negotiation_reference_and_fallback_are_neutral(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    prompt = builder.build_prompt(method="negotiate_tick", built_context=builder.build(_context()))
    required_phrases = (
        "Valid support strength combines across all supporting units, regardless of which power owns them.",
        "request, offer, or make a conditional proposal for support of a specific legal hold or move",
        "These proposals are non-binding communication",
        "revealing intentions, dependencies, or vulnerabilities",
        "exact currently legal support actions, origins, and destinations",
    )
    for phrase in required_phrases:
        assert phrase in prompt
    support_geometry = "A support order is legal only when the supporting unit could itself legally move into the supported destination. For support-to-hold, that destination is the unit’s current province. For support-to-move, it is the moving unit’s destination. Armies may support only into adjacent land provinces; fleets may support only into adjacent sea or coastal provinces. A fleet cannot support into an inland province, even if it is adjacent to the moving unit’s origin. For example, `F Alb S A Bul–Ser` is illegal because Serbia is inland and a fleet in Albania cannot enter it."
    assert support_geometry in prompt
    convoy_support = "When supporting an army that moves by convoy, name the convoyed army’s original province and destination—not a convoying fleet’s province. For example, to support `A Yor–Bel` via `F Nth`, use `A Hol S A Yor–Bel`; `A Hol S F Nth–Bel` supports a different move and does not aid the convoy."
    assert convoy_support in prompt
    assert DIPLOMACY_RULES_REFERENCE_VERSION == "diplomacy_rules_reference_v5_convoyed_army_support_public_orders"
    assert "rules_reference_version: diplomacy_rules_reference_v5_convoyed_army_support_public_orders" in DEFAULT_DIPLOMACY_PRIMER
    assert support_geometry in DEFAULT_DIPLOMACY_PRIMER
    assert convoy_support in DEFAULT_DIPLOMACY_PRIMER
    assert "valid support strength combines across powers" in DEFAULT_DIPLOMACY_PRIMER
    assert "request, offer, or condition support during negotiation" in DEFAULT_DIPLOMACY_PRIMER
    prohibited = (
        "support is the most important rule",
        "support is the only way to make progress",
        "always ask allies for support",
        "support allies whenever possible",
    )
    lowered = prompt.lower()
    assert not any(phrase in lowered for phrase in prohibited)


def test_order_context_exposes_engine_derived_convoy_actions(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    legal_context = builder.build(_context())["legal_order_context"]
    army = next(row for row in legal_context["your_units"] if row["unit_ref"] == "unit_0001")
    fleet = next(row for row in legal_context["your_units"] if row["unit_ref"] == "unit_0002")
    army_move = {"type": "MOVE", "payload": {"to": "bre", "via_convoy": True}}
    fleet_convoy = {"type": "CONVOY", "payload": {"from": "eng", "army_from": "lon", "army_to": "bre"}}
    assert army_move in army["legal_orders"]
    assert fleet_convoy in fleet["legal_orders"]
    assert any("CONVOY entries" in note for note in legal_context["catalogue_notes"])

    engine = DiplomacyRuleEngine()
    validated = engine.validate_orders(
        _state(),
        [
            {"order_id": "army", "unit_ref": "unit_0001", "power": "england", **army_move},
            {"order_id": "fleet", "unit_ref": "unit_0002", "power": "england", **fleet_convoy},
        ],
    )
    assert all(row["validation"] == "accepted" for row in validated["validated_orders"])


def test_multifleet_route_is_exposed_validated_and_resolved(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    legal_context = builder.build(_multifleet_context())["legal_order_context"]
    route = next(
        row
        for row in legal_context["convoy_route_options"]
        if row["army_from"] == "lon" and row["army_to"] == "tun"
    )
    assert route["fleet_provinces"] == ["eng", "mao", "wes"]
    assert route["fleet_unit_ids"] == ["unit_0002", "unit_0003", "unit_0004"]
    assert route["requires_other_powers"] is True
    assert "ion" not in route["fleet_provinces"]

    eng = next(row for row in legal_context["your_units"] if row["unit_ref"] == "unit_0002")
    ion = next(row for row in legal_context["your_units"] if row["unit_ref"] == "unit_0005")
    convoy_payload = {"army_from": "lon", "army_to": "tun"}
    assert {"type": "CONVOY", "payload": {"from": "eng", **convoy_payload}} in eng["legal_orders"]
    assert not any(
        order["type"] == "CONVOY" and order["payload"].get("army_from") == "lon" and order["payload"].get("army_to") == "tun"
        for order in ion["legal_orders"]
    )

    orders = [
        {"order_id": "army", "unit_ref": "unit_0001", "power": "england", "type": "MOVE", "payload": {"from": "lon", "to": "tun", "via_convoy": True}},
        {"order_id": "eng", "unit_ref": "unit_0002", "power": "england", "type": "CONVOY", "payload": {"from": "eng", **convoy_payload}},
        {"order_id": "mao", "unit_ref": "unit_0003", "power": "france", "type": "CONVOY", "payload": {"from": "mao", **convoy_payload}},
        {"order_id": "wes", "unit_ref": "unit_0004", "power": "russia", "type": "CONVOY", "payload": {"from": "wes", **convoy_payload}},
    ]
    engine = DiplomacyRuleEngine()
    validated = engine.validate_orders(_multifleet_state(), orders)
    validation_by_unit = {row["unit_id"]: row["validation"] for row in validated["validated_orders"]}
    assert all(validation_by_unit[unit_id] == "accepted" for unit_id in ("unit_0001", "unit_0002", "unit_0003", "unit_0004"))
    resolved = engine.resolve_phase(_multifleet_state(), validated)
    assert next(row for row in resolved["movement_outcomes"] if row["unit_id"] == "unit_0001")["result"] == "moved"
    assert resolved["convoy_outcomes"] == [
        {"schema_version": "1.0", "army_unit_id": "unit_0001", "status": "possible", "routes": [["eng", "mao", "wes"]]},
    ]


def test_missing_or_dislodged_intermediate_fleet_breaks_sole_multifleet_route() -> None:
    engine = DiplomacyRuleEngine()
    convoy_payload = {"army_from": "lon", "army_to": "tun"}
    missing_intermediate = [
        {"order_id": "army", "unit_ref": "unit_0001", "power": "england", "type": "MOVE", "payload": {"from": "lon", "to": "tun", "via_convoy": True}},
        {"order_id": "eng", "unit_ref": "unit_0002", "power": "england", "type": "CONVOY", "payload": {"from": "eng", **convoy_payload}},
        {"order_id": "wes", "unit_ref": "unit_0004", "power": "russia", "type": "CONVOY", "payload": {"from": "wes", **convoy_payload}},
    ]
    validated = engine.validate_orders(_multifleet_state(), missing_intermediate)
    resolved = engine.resolve_phase(_multifleet_state(), validated)
    assert next(row for row in resolved["convoy_outcomes"] if row["army_unit_id"] == "unit_0001")["status"] == "impossible"

    state = _multifleet_state()
    state["units"] = [
        *state["units"],
        {"unit_id": "unit_0006", "owner": "turkey", "type": "F", "province": "spa", "coast": "spa_nc", "status": "active"},
        {"unit_id": "unit_0007", "owner": "turkey", "type": "F", "province": "bre", "coast": None, "status": "active"},
    ]
    dislodge_orders = [
        {"order_id": "army", "unit_ref": "unit_0001", "power": "england", "type": "MOVE", "payload": {"from": "lon", "to": "tun", "via_convoy": True}},
        {"order_id": "eng", "unit_ref": "unit_0002", "power": "england", "type": "CONVOY", "payload": {"from": "eng", **convoy_payload}},
        {"order_id": "mao", "unit_ref": "unit_0003", "power": "france", "type": "CONVOY", "payload": {"from": "mao", **convoy_payload}},
        {"order_id": "wes", "unit_ref": "unit_0004", "power": "russia", "type": "CONVOY", "payload": {"from": "wes", **convoy_payload}},
        {"order_id": "spa_attack", "unit_ref": "unit_0006", "power": "turkey", "type": "MOVE", "payload": {"from": "spa", "to": "mao"}},
        {"order_id": "bre_support", "unit_ref": "unit_0007", "power": "turkey", "type": "SUPPORT_MOVE", "payload": {"from": "bre", "supported_from": "spa", "supported_to": "mao"}},
    ]
    validated = engine.validate_orders(state, dislodge_orders)
    resolved = engine.resolve_phase(state, validated)
    assert next(row for row in resolved["convoy_outcomes"] if row["army_unit_id"] == "unit_0001")["status"] == "impossible"
    assert next(row for row in resolved["movement_outcomes"] if row["unit_id"] == "unit_0003")["result"] == "dislodged"
