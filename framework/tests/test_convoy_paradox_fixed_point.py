from __future__ import annotations

import json

from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _state(units: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "map_id": "standard",
        "year": 1901,
        "season": "spring",
        "phase": "movement",
        "units": units,
        "supply_ownership": {},
        "home_centers": {},
    }


def _unit(*, unit_id: str, owner: str, unit_type: str, province: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "owner": owner,
        "type": unit_type,
        "province": province,
        "coast": None,
        "status": "active",
    }


def _order(
    *,
    order_id: str,
    unit_ref: str,
    power: str,
    order_type: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "order_id": order_id,
        "unit_ref": unit_ref,
        "power": power,
        "type": order_type,
        "payload": payload,
    }


def test_convoyed_army_cannot_preserve_its_route_by_cutting_support() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
            _unit(unit_id="u_fra_f_mao", owner="france", unit_type="F", province="mao"),
            _unit(unit_id="u_fra_f_bel", owner="france", unit_type="F", province="bel"),
        ]
    )
    submitted = [
        _order(
            order_id="o_lon_bel_via_convoy",
            unit_ref="u_eng_a_lon",
            power="england",
            order_type="MOVE",
            payload={"from": "lon", "to": "bel", "via_convoy": True},
        ),
        _order(
            order_id="o_eng_convoy_lon_bel",
            unit_ref="u_eng_f_eng",
            power="england",
            order_type="CONVOY",
            payload={"from": "eng", "army_from": "lon", "army_to": "bel"},
        ),
        _order(
            order_id="o_mao_eng",
            unit_ref="u_fra_f_mao",
            power="france",
            order_type="MOVE",
            payload={"from": "mao", "to": "eng"},
        ),
        _order(
            order_id="o_bel_support_mao_eng",
            unit_ref="u_fra_f_bel",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "bel", "supported_from": "mao", "supported_to": "eng"},
        ),
    ]
    resolved = engine.resolve_phase(state, engine.validate_orders(state, submitted))
    movements = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    convoy = {row["army_unit_id"]: row["status"] for row in resolved["convoy_outcomes"]}
    assert movements["u_eng_f_eng"] == "dislodged"
    assert convoy["u_eng_a_lon"] == "impossible"
    assert movements["u_eng_a_lon"] == "held"


def test_multi_route_convoy_only_needs_one_independent_route() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
            _unit(unit_id="u_eng_f_nth", owner="england", unit_type="F", province="nth"),
            _unit(unit_id="u_fra_f_mao", owner="france", unit_type="F", province="mao"),
            _unit(unit_id="u_fra_f_bel", owner="france", unit_type="F", province="bel"),
        ]
    )
    submitted = [
        _order(
            order_id="o_lon_bel_via_convoy",
            unit_ref="u_eng_a_lon",
            power="england",
            order_type="MOVE",
            payload={"from": "lon", "to": "bel", "via_convoy": True},
        ),
        _order(
            order_id="o_eng_convoy_lon_bel",
            unit_ref="u_eng_f_eng",
            power="england",
            order_type="CONVOY",
            payload={"from": "eng", "army_from": "lon", "army_to": "bel"},
        ),
        _order(
            order_id="o_nth_convoy_lon_bel",
            unit_ref="u_eng_f_nth",
            power="england",
            order_type="CONVOY",
            payload={"from": "nth", "army_from": "lon", "army_to": "bel"},
        ),
        _order(
            order_id="o_mao_eng",
            unit_ref="u_fra_f_mao",
            power="france",
            order_type="MOVE",
            payload={"from": "mao", "to": "eng"},
        ),
        _order(
            order_id="o_bel_support_mao_eng",
            unit_ref="u_fra_f_bel",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "bel", "supported_from": "mao", "supported_to": "eng"},
        ),
    ]
    resolved = engine.resolve_phase(state, engine.validate_orders(state, submitted))
    convoy = {row["army_unit_id"]: row["status"] for row in resolved["convoy_outcomes"]}
    assert convoy["u_eng_a_lon"] == "possible"
    routes = {row["army_unit_id"]: row["routes"] for row in resolved["convoy_outcomes"]}
    assert routes["u_eng_a_lon"] == [["nth"]]


def test_convoy_fixed_point_is_deterministic_across_input_order() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
            _unit(unit_id="u_fra_f_mao", owner="france", unit_type="F", province="mao"),
            _unit(unit_id="u_fra_a_bre", owner="france", unit_type="A", province="bre"),
        ]
    )
    submitted = [
        _order(
            order_id="o_lon_bre_via_convoy",
            unit_ref="u_eng_a_lon",
            power="england",
            order_type="MOVE",
            payload={"from": "lon", "to": "bre", "via_convoy": True},
        ),
        _order(
            order_id="o_eng_convoy_lon_bre",
            unit_ref="u_eng_f_eng",
            power="england",
            order_type="CONVOY",
            payload={"from": "eng", "army_from": "lon", "army_to": "bre"},
        ),
        _order(
            order_id="o_mao_eng",
            unit_ref="u_fra_f_mao",
            power="france",
            order_type="MOVE",
            payload={"from": "mao", "to": "eng"},
        ),
        _order(
            order_id="o_bre_support_mao_eng",
            unit_ref="u_fra_a_bre",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "bre", "supported_from": "mao", "supported_to": "eng"},
        ),
    ]
    resolved_a = engine.resolve_phase(state, engine.validate_orders(state, submitted))
    resolved_b = engine.resolve_phase(state, engine.validate_orders(state, list(reversed(submitted))))
    assert json.dumps(resolved_a, sort_keys=True) == json.dumps(resolved_b, sort_keys=True)


class _OscillatingConvoyEngine(DiplomacyRuleEngine):
    def __init__(self) -> None:
        super().__init__()
        self._toggle = False

    def _compute_convoy_possibility(self, resolved, excluded_convoy_fleets):  # type: ignore[override]
        self._toggle = not self._toggle
        if self._toggle:
            return super()._compute_convoy_possibility(resolved, excluded_convoy_fleets)
        base_possible, base_routes = super()._compute_convoy_possibility(resolved, excluded_convoy_fleets)
        return ({k: (not v) for k, v in base_possible.items()}, base_routes)


def test_non_convergence_fallback_branch_is_stable() -> None:
    engine = _OscillatingConvoyEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
        ]
    )
    submitted = [
        _order(
            order_id="o_lon_bre_via_convoy",
            unit_ref="u_eng_a_lon",
            power="england",
            order_type="MOVE",
            payload={"from": "lon", "to": "bre", "via_convoy": True},
        ),
        _order(
            order_id="o_eng_convoy_lon_bre",
            unit_ref="u_eng_f_eng",
            power="england",
            order_type="CONVOY",
            payload={"from": "eng", "army_from": "lon", "army_to": "bre"},
        ),
    ]
    resolved_a = engine.resolve_phase(state, engine.validate_orders(state, submitted))
    # Fresh engine to guarantee deterministic output despite no convergence branch.
    engine_b = _OscillatingConvoyEngine()
    resolved_b = engine_b.resolve_phase(state, engine_b.validate_orders(state, submitted))
    assert resolved_a["schema_version"] == "1.0"
    assert json.dumps(resolved_a, sort_keys=True) == json.dumps(resolved_b, sort_keys=True)
