import json
from unittest.mock import patch

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.agents.llm_provider import FakeLLMProvider
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine
from llm_diplomacy.runner.recovery import ExperimentRecovery


def _unit(ident, owner, kind, province, status="active"):
    return dict(unit_id=ident, owner=owner, type=kind, province=province, coast=None, status=status)


def _context(supporter=None, fleets=None):
    return AgentContext(
        schema_version="1.0", experiment_id="synthetic", game_id="convoy_support",
        phase_id="1901_fall_movement", agent_id="germany", country_assignment="germany",
        visible_state={
            "schema_version": "1.0", "game_id": "convoy_support", "year": 1901,
            "season": "fall", "phase": "movement", "supply_ownership": {},
            "private_messages": [], "global_messages": [],
            "units": [supporter or _unit("supporter", "germany", "A", "ruh"),
                      _unit("army", "england", "A", "yor"),
                      _unit("rival", "france", "A", "pic"),
                      *(fleets if fleets is not None else [_unit("convoy", "england", "F", "nth")])],
        },
    )


def _support(destination="bel", origin="yor"):
    return {"type": "SUPPORT_MOVE", "payload": {"supported_from": origin, "supported_to": destination}}


def _options(tmp_path, context):
    legal = PromptContextBuilder(folio_root=tmp_path / "folio").build(context)["legal_order_context"]
    return next(u["legal_orders"] for u in legal["your_units"] if u["unit_ref"] == "supporter")


@pytest.mark.parametrize("kind,province,available", [("A", "ruh", True), ("F", "hol", True),
                                                    ("A", "kie", False), ("F", "hel", False)])
def test_convoy_support_respects_supporter_reach(tmp_path, kind, province, available):
    context = _context(supporter=_unit("supporter", "germany", kind, province))
    orders = _options(tmp_path, context)
    assert (_support() in orders) is available
    if available:
        assert _support(origin="nth") in orders


@pytest.mark.parametrize("fleets", [[], [_unit("convoy", "england", "F", "hol")],
                                   [_unit("convoy", "england", "F", "mao")],
                                   [_unit("convoy", "england", "F", "nth", "destroyed")],
                                   [_unit("convoy", "england", "F", "nth", "dislodged")]])
def test_unavailable_convoy_does_not_remove_direct_support(tmp_path, fleets):
    orders = _options(tmp_path, _context(fleets=fleets))
    assert _support() not in orders
    assert _support(origin="pic") in orders


@pytest.mark.parametrize("status", ["destroyed", "dislodged"])
def test_inactive_army_is_not_offered_convoy_support(tmp_path, status):
    context = _context()
    context.visible_state["units"][1]["status"] = status
    assert _support() not in _options(tmp_path, context)


@pytest.mark.parametrize("connected", [True, False])
def test_support_for_multifleet_cross_power_convoy(tmp_path, connected):
    context = _context(supporter=_unit("supporter", "germany", "F", "ion"), fleets=[
        _unit("first", "england", "F", "eng"), _unit("last", "france", "F", "wes"),
        *([_unit("middle", "russia", "F", "mao")] if connected else []),
    ])
    context.visible_state["units"][1]["province"] = "lon"
    assert (_support("tun", "lon") in _options(tmp_path, context)) is connected


def test_adjacent_move_with_convoy_route_has_one_support_signature(tmp_path):
    context = _context(supporter=_unit("supporter", "germany", "F", "eng"))
    assert _options(tmp_path, context).count(_support("lon")) == 1


@pytest.mark.parametrize("province,available", [("mar", True), ("gas", True), ("bre", False)])
def test_fleet_support_for_convoy_to_split_coast_province(tmp_path, province, available):
    context = _context(supporter=_unit("supporter", "germany", "F", province), fleets=[
        _unit("first", "england", "F", "eng"), _unit("last", "england", "F", "mao"),
    ])
    context.visible_state["units"][1]["province"] = "lon"
    assert (_support("spa", "lon") in _options(tmp_path, context)) is available


def test_fleet_targets_keep_coast_specific_direct_support(tmp_path):
    context = _context(supporter=_unit("supporter", "germany", "F", "mao"), fleets=[])
    context.visible_state["units"] = [context.visible_state["units"][0], _unit("target", "france", "F", "gas")]
    destinations = {order["payload"]["supported_to"] for order in _options(tmp_path, context)
                    if order["type"] == "SUPPORT_MOVE" and order["payload"]["supported_from"] == "gas"}
    assert destinations == {"bre", "spa_nc"}


def test_yorkshire_convoy_support_passes_runtime_and_changes_contested_adjudication(tmp_path):
    context = _context()
    provider = FakeLLMProvider(responses_by_method={"submit_orders": json.dumps({
        "orders": [{"unit_ref": "supporter", **_support()}],
        "order_rationale": {"goal_supported": "Support the agreed landing",
                            "why_this_advances_strategy": "Help England enter Belgium."},
    })})
    recovery = ExperimentRecovery(tmp_path / "recovery", identity={"id": "convoy-support"}, resume=False)
    recovery.prepare()
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=tmp_path / "runtime",
                                     provider=provider, halt_on_issue=True,
                                     reflection_protocol="legacy_json_v1", recovery=recovery)
    runtime.register_agent(agent_id="germany")
    with patch.object(provider, "generate", wraps=provider.generate) as generate:
        result = runtime.submit_orders(context)
    assert generate.call_count == 1
    assert len(result.orders) == 1
    assert result.orders[0]["type"] == "SUPPORT_MOVE"
    assert result.orders[0]["payload"]["supported_from"] == "yor"
    assert result.explanation["_recovery"]["order_validation_issue"] is None
    orders = result.orders + [
        {"order_id": "landing", "unit_ref": "army", "power": "england", "type": "MOVE",
         "payload": {"to": "bel", "via_convoy": True}},
        {"order_id": "convoy", "unit_ref": "convoy", "power": "england", "type": "CONVOY",
         "payload": {"from": "nth", "army_from": "yor", "army_to": "bel"}},
        {"order_id": "rival", "unit_ref": "rival", "power": "france", "type": "MOVE",
         "payload": {"to": "bel"}},
    ]
    engine = DiplomacyRuleEngine()
    validated = engine.validate_orders(context.visible_state, orders)
    assert all(row["validation"] == "accepted" for row in validated["validated_orders"])
    resolved = engine.resolve_phase(context.visible_state, validated)
    assert next(row for row in resolved["movement_outcomes"] if row["unit_id"] == "army")["result"] == "moved"
    assert next(row for row in resolved["support_outcomes"] if row["unit_id"] == "supporter")["status"] == "effective"
