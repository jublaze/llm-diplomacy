from __future__ import annotations

import json

import pytest

from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.gm.game_master import GameMaster
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


def _unit(
    *,
    unit_id: str,
    owner: str,
    unit_type: str,
    province: str,
    coast: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "owner": owner,
        "type": unit_type,
        "province": province,
        "coast": coast,
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


def test_bounce_on_equal_strength() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par"),
            _unit(unit_id="u_fra_a_mar", owner="france", unit_type="A", province="mar"),
        ]
    )
    submitted = [
        _order(
            order_id="o_par_bur",
            unit_ref="u_fra_a_par",
            power="france",
            order_type="MOVE",
            payload={"from": "par", "to": "bur"},
        ),
        _order(
            order_id="o_mar_bur",
            unit_ref="u_fra_a_mar",
            power="france",
            order_type="MOVE",
            payload={"from": "mar", "to": "bur"},
        ),
    ]

    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    assert outcomes["u_fra_a_par"] == "held"
    assert outcomes["u_fra_a_mar"] == "held"


def test_friendly_move_chain_vacates_occupied_destinations() -> None:
    engine = DiplomacyRuleEngine()
    state = _state([
        _unit(unit_id="a", owner="austria", unit_type="A", province="tri"),
        _unit(unit_id="b", owner="austria", unit_type="A", province="vie"),
    ])
    orders = [
        _order(order_id="a_to_vie", unit_ref="a", power="austria", order_type="MOVE", payload={"from": "tri", "to": "vie"}),
        _order(order_id="b_to_bud", unit_ref="b", power="austria", order_type="MOVE", payload={"from": "vie", "to": "bud"}),
    ]
    result = engine.resolve_phase(state, engine.validate_orders(state, orders))
    moved = {row["unit_id"] for row in result["movement_outcomes"] if row["result"] == "moved"}
    assert moved == {"a", "b"}


def test_friendly_three_unit_rotation_succeeds() -> None:
    engine = DiplomacyRuleEngine()
    state = _state([
        _unit(unit_id="tri", owner="austria", unit_type="A", province="tri"),
        _unit(unit_id="vie", owner="austria", unit_type="A", province="vie"),
        _unit(unit_id="bud", owner="austria", unit_type="A", province="bud"),
    ])
    orders = [
        _order(order_id="tri_vie", unit_ref="tri", power="austria", order_type="MOVE", payload={"from": "tri", "to": "vie"}),
        _order(order_id="vie_bud", unit_ref="vie", power="austria", order_type="MOVE", payload={"from": "vie", "to": "bud"}),
        _order(order_id="bud_tri", unit_ref="bud", power="austria", order_type="MOVE", payload={"from": "bud", "to": "tri"}),
    ]
    result = engine.resolve_phase(state, engine.validate_orders(state, orders))
    moved = {row["unit_id"] for row in result["movement_outcomes"] if row["result"] == "moved"}
    assert moved == {"tri", "vie", "bud"}


def test_contested_destination_blocks_a_rotation() -> None:
    engine = DiplomacyRuleEngine()
    state = _state([
        _unit(unit_id="tri", owner="austria", unit_type="A", province="tri"),
        _unit(unit_id="vie", owner="austria", unit_type="A", province="vie"),
        _unit(unit_id="bud", owner="austria", unit_type="A", province="bud"),
        _unit(unit_id="tyr", owner="turkey", unit_type="A", province="tyr"),
    ])
    orders = [
        _order(order_id="tri_vie", unit_ref="tri", power="austria", order_type="MOVE", payload={"from": "tri", "to": "vie"}),
        _order(order_id="vie_bud", unit_ref="vie", power="austria", order_type="MOVE", payload={"from": "vie", "to": "bud"}),
        _order(order_id="bud_tri", unit_ref="bud", power="austria", order_type="MOVE", payload={"from": "bud", "to": "tri"}),
        _order(order_id="tyr_vie", unit_ref="tyr", power="turkey", order_type="MOVE", payload={"from": "tyr", "to": "vie"}),
    ]
    result = engine.resolve_phase(state, engine.validate_orders(state, orders))
    moved = {row["unit_id"] for row in result["movement_outcomes"] if row["result"] == "moved"}
    assert moved == set()


def test_convoy_permits_swap_with_an_adjacent_direct_move() -> None:
    engine = DiplomacyRuleEngine()
    state = _state([
        _unit(unit_id="eng_a_nwy", owner="england", unit_type="A", province="nwy"),
        _unit(unit_id="eng_f_ska", owner="england", unit_type="F", province="ska"),
        _unit(unit_id="rus_a_swe", owner="russia", unit_type="A", province="swe"),
    ])
    orders = [
        _order(order_id="nwy_swe", unit_ref="eng_a_nwy", power="england", order_type="MOVE", payload={"from": "nwy", "to": "swe", "via_convoy": True}),
        _order(order_id="ska_convoy", unit_ref="eng_f_ska", power="england", order_type="CONVOY", payload={"from": "ska", "army_from": "nwy", "army_to": "swe"}),
        _order(order_id="swe_nwy", unit_ref="rus_a_swe", power="russia", order_type="MOVE", payload={"from": "swe", "to": "nwy"}),
    ]
    result = engine.resolve_phase(state, engine.validate_orders(state, orders))
    moved = {row["unit_id"] for row in result["movement_outcomes"] if row["result"] == "moved"}
    assert moved == {"eng_a_nwy", "rus_a_swe"}


def test_supported_attack_dislodges() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_ger_a_mun", owner="germany", unit_type="A", province="mun"),
            _unit(unit_id="u_ger_a_ruh", owner="germany", unit_type="A", province="ruh"),
            _unit(unit_id="u_fra_a_bur", owner="france", unit_type="A", province="bur"),
        ]
    )
    submitted = [
        _order(
            order_id="o_mun_bur",
            unit_ref="u_ger_a_mun",
            power="germany",
            order_type="MOVE",
            payload={"from": "mun", "to": "bur"},
        ),
        _order(
            order_id="o_ruh_s_mun_bur",
            unit_ref="u_ger_a_ruh",
            power="germany",
            order_type="SUPPORT_MOVE",
            payload={"from": "ruh", "supported_from": "mun", "supported_to": "bur"},
        ),
        _order(
            order_id="o_bur_hold",
            unit_ref="u_fra_a_bur",
            power="france",
            order_type="HOLD",
            payload={"at": "bur"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    unit_rows = {row["unit_id"]: row for row in resolved["output_state"]["units"]}
    assert unit_rows["u_ger_a_mun"]["province"] == "bur"
    assert unit_rows["u_fra_a_bur"]["status"] == "dislodged"


def test_support_cut_by_third_party_attack() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_ger_a_mun", owner="germany", unit_type="A", province="mun"),
            _unit(unit_id="u_ger_a_ruh", owner="germany", unit_type="A", province="ruh"),
            _unit(unit_id="u_fra_a_bur", owner="france", unit_type="A", province="bur"),
            _unit(unit_id="u_ita_a_bel", owner="italy", unit_type="A", province="bel"),
        ]
    )
    submitted = [
        _order(
            order_id="o_mun_bur",
            unit_ref="u_ger_a_mun",
            power="germany",
            order_type="MOVE",
            payload={"from": "mun", "to": "bur"},
        ),
        _order(
            order_id="o_ruh_support_mun_bur",
            unit_ref="u_ger_a_ruh",
            power="germany",
            order_type="SUPPORT_MOVE",
            payload={"from": "ruh", "supported_from": "mun", "supported_to": "bur"},
        ),
        _order(
            order_id="o_bur_hold",
            unit_ref="u_fra_a_bur",
            power="france",
            order_type="HOLD",
            payload={"at": "bur"},
        ),
        _order(
            order_id="o_bel_ruh",
            unit_ref="u_ita_a_bel",
            power="italy",
            order_type="MOVE",
            payload={"from": "bel", "to": "ruh"},
        ),
    ]

    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    support = {row["unit_id"]: row["status"] for row in resolved["support_outcomes"]}
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    assert support["u_ger_a_ruh"] == "cut"
    assert outcomes["u_ger_a_mun"] == "held"


def test_support_not_cut_by_target_province_attack() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_ger_a_bur", owner="germany", unit_type="A", province="bur"),
            _unit(unit_id="u_ger_a_pic", owner="germany", unit_type="A", province="pic"),
            _unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par"),
        ]
    )
    submitted = [
        _order(
            order_id="o_bur_par",
            unit_ref="u_ger_a_bur",
            power="germany",
            order_type="MOVE",
            payload={"from": "bur", "to": "par"},
        ),
        _order(
            order_id="o_pic_support_bur_par",
            unit_ref="u_ger_a_pic",
            power="germany",
            order_type="SUPPORT_MOVE",
            payload={"from": "pic", "supported_from": "bur", "supported_to": "par"},
        ),
        _order(
            order_id="o_par_pic",
            unit_ref="u_fra_a_par",
            power="france",
            order_type="MOVE",
            payload={"from": "par", "to": "pic"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    support = {row["unit_id"]: row["status"] for row in resolved["support_outcomes"]}
    assert support["u_ger_a_pic"] == "effective"


def test_illegal_move_substitutes_to_hold() -> None:
    engine = DiplomacyRuleEngine()
    state = _state([_unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par")])
    submitted = [
        _order(
            order_id="o_par_eng",
            unit_ref="u_fra_a_par",
            power="france",
            order_type="MOVE",
            payload={"from": "par", "to": "eng"},
        )
    ]

    validated = engine.validate_orders(state, submitted)
    row = validated["validated_orders"][0]
    assert row["validation"] == "substituted"
    assert row["effective_order"]["type"] == "HOLD"


def test_self_dislodgement_is_blocked() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_ger_a_mun", owner="germany", unit_type="A", province="mun"),
            _unit(unit_id="u_ger_a_bur", owner="germany", unit_type="A", province="bur"),
            _unit(unit_id="u_ger_a_ruh", owner="germany", unit_type="A", province="ruh"),
        ]
    )
    submitted = [
        _order(
            order_id="o_mun_bur",
            unit_ref="u_ger_a_mun",
            power="germany",
            order_type="MOVE",
            payload={"from": "mun", "to": "bur"},
        ),
        _order(
            order_id="o_ruh_support_mun_bur",
            unit_ref="u_ger_a_ruh",
            power="germany",
            order_type="SUPPORT_MOVE",
            payload={"from": "ruh", "supported_from": "mun", "supported_to": "bur"},
        ),
        _order(
            order_id="o_bur_hold",
            unit_ref="u_ger_a_bur",
            power="germany",
            order_type="HOLD",
            payload={"at": "bur"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    assert outcomes["u_ger_a_mun"] == "held"
    assert outcomes["u_ger_a_bur"] == "held"


def test_resolution_is_deterministic() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par"),
            _unit(unit_id="u_ger_a_bur", owner="germany", unit_type="A", province="bur"),
            _unit(unit_id="u_ger_a_mun", owner="germany", unit_type="A", province="mun"),
        ]
    )
    submitted = [
        _order(
            order_id="o_1",
            unit_ref="u_ger_a_mun",
            power="germany",
            order_type="MOVE",
            payload={"from": "mun", "to": "bur"},
        ),
        _order(
            order_id="o_2",
            unit_ref="u_ger_a_bur",
            power="germany",
            order_type="HOLD",
            payload={"at": "bur"},
        ),
        _order(
            order_id="o_3",
            unit_ref="u_fra_a_par",
            power="france",
            order_type="MOVE",
            payload={"from": "par", "to": "bur"},
        ),
    ]
    validated_a = engine.validate_orders(state, submitted)
    validated_b = engine.validate_orders(state, list(reversed(submitted)))
    resolved_a = engine.resolve_phase(state, validated_a)
    resolved_b = engine.resolve_phase(state, validated_b)
    assert json.dumps(resolved_a, sort_keys=True) == json.dumps(resolved_b, sort_keys=True)


def test_dislodged_supporter_does_not_contribute() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_ger_a_mun", owner="germany", unit_type="A", province="mun"),
            _unit(unit_id="u_ger_a_ruh", owner="germany", unit_type="A", province="ruh"),
            _unit(unit_id="u_fra_a_bur", owner="france", unit_type="A", province="bur"),
            _unit(unit_id="u_fra_a_bel", owner="france", unit_type="A", province="bel"),
            _unit(unit_id="u_ita_a_mar", owner="italy", unit_type="A", province="mar"),
        ]
    )
    submitted = [
        _order(
            order_id="o_mun_bur",
            unit_ref="u_ger_a_mun",
            power="germany",
            order_type="MOVE",
            payload={"from": "mun", "to": "bur"},
        ),
        _order(
            order_id="o_ruh_support_mun_bur",
            unit_ref="u_ger_a_ruh",
            power="germany",
            order_type="SUPPORT_MOVE",
            payload={"from": "ruh", "supported_from": "mun", "supported_to": "bur"},
        ),
        _order(
            order_id="o_bur_ruh",
            unit_ref="u_fra_a_bur",
            power="france",
            order_type="MOVE",
            payload={"from": "bur", "to": "ruh"},
        ),
        _order(
            order_id="o_bel_support_bur_ruh",
            unit_ref="u_fra_a_bel",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "bel", "supported_from": "bur", "supported_to": "ruh"},
        ),
        _order(
            order_id="o_mar_bur",
            unit_ref="u_ita_a_mar",
            power="italy",
            order_type="MOVE",
            payload={"from": "mar", "to": "bur"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    support = {row["unit_id"]: row["status"] for row in resolved["support_outcomes"]}

    assert support["u_ger_a_ruh"] == "dislodged"
    assert outcomes["u_ger_a_mun"] == "held"
    assert outcomes["u_ger_a_ruh"] == "dislodged"
    assert outcomes["u_ger_a_mun"] == "held"


def test_support_move_requires_exact_tuple_match() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_ger_a_mun", owner="germany", unit_type="A", province="mun"),
            _unit(unit_id="u_ger_a_ruh", owner="germany", unit_type="A", province="ruh"),
            _unit(unit_id="u_fra_a_bur", owner="france", unit_type="A", province="bur"),
        ]
    )
    submitted = [
        _order(
            order_id="o_mun_bur",
            unit_ref="u_ger_a_mun",
            power="germany",
            order_type="MOVE",
            payload={"from": "mun", "to": "bur"},
        ),
        _order(
            order_id="o_ruh_support_wrong_tuple",
            unit_ref="u_ger_a_ruh",
            power="germany",
            order_type="SUPPORT_MOVE",
            payload={"from": "ruh", "supported_from": "ber", "supported_to": "bur"},
        ),
        _order(
            order_id="o_bur_hold",
            unit_ref="u_fra_a_bur",
            power="france",
            order_type="HOLD",
            payload={"at": "bur"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    support = {row["unit_id"]: row["status"] for row in resolved["support_outcomes"]}
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    assert support["u_ger_a_ruh"] == "invalid"
    assert outcomes["u_ger_a_mun"] == "held"


def test_basic_convoy_success() -> None:
    engine = DiplomacyRuleEngine()
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
    validated = engine.validate_orders(state, submitted)
    assert validated["validated_orders"][1]["effective_order"]["type"] == "CONVOY"
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    convoy = {row["army_unit_id"]: row["status"] for row in resolved["convoy_outcomes"]}
    assert outcomes["u_eng_a_lon"] == "moved"
    assert convoy["u_eng_a_lon"] == "possible"


def test_dislodged_convoy_fleet_breaks_route() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
            _unit(unit_id="u_fra_f_mao", owner="france", unit_type="F", province="mao"),
            _unit(unit_id="u_fra_f_iri", owner="france", unit_type="F", province="iri"),
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
            order_id="o_iri_support_mao_eng",
            unit_ref="u_fra_f_iri",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "iri", "supported_from": "mao", "supported_to": "eng"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    convoy = {row["army_unit_id"]: row["status"] for row in resolved["convoy_outcomes"]}
    assert outcomes["u_eng_f_eng"] == "dislodged"
    assert outcomes["u_eng_a_lon"] == "held"
    assert convoy["u_eng_a_lon"] == "impossible"


def test_multi_route_convoy_survives_one_route_break() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
            _unit(unit_id="u_eng_f_nth", owner="england", unit_type="F", province="nth"),
            _unit(unit_id="u_fra_f_mao", owner="france", unit_type="F", province="mao"),
            _unit(unit_id="u_fra_f_iri", owner="france", unit_type="F", province="iri"),
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
            order_id="o_iri_support_mao_eng",
            unit_ref="u_fra_f_iri",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "iri", "supported_from": "mao", "supported_to": "eng"},
        ),
    ]
    validated = engine.validate_orders(state, submitted)
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["movement_outcomes"]}
    convoy = {row["army_unit_id"]: row["status"] for row in resolved["convoy_outcomes"]}
    assert outcomes["u_eng_f_eng"] == "dislodged"
    assert outcomes["u_eng_a_lon"] == "moved"
    assert convoy["u_eng_a_lon"] == "possible"


def test_convoy_recompute_is_deterministic() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_eng_a_lon", owner="england", unit_type="A", province="lon"),
            _unit(unit_id="u_eng_f_eng", owner="england", unit_type="F", province="eng"),
            _unit(unit_id="u_fra_f_mao", owner="france", unit_type="F", province="mao"),
            _unit(unit_id="u_fra_f_iri", owner="france", unit_type="F", province="iri"),
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
            order_id="o_iri_support_mao_eng",
            unit_ref="u_fra_f_iri",
            power="france",
            order_type="SUPPORT_MOVE",
            payload={"from": "iri", "supported_from": "mao", "supported_to": "eng"},
        ),
    ]
    validated_a = engine.validate_orders(state, submitted)
    resolved_a = engine.resolve_phase(state, validated_a)
    validated_b = engine.validate_orders(state, list(reversed(submitted)))
    resolved_b = engine.resolve_phase(state, validated_b)
    assert json.dumps(resolved_a, sort_keys=True) == json.dumps(resolved_b, sort_keys=True)


def test_game_master_integration_smoke(tmp_path) -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par"),
            _unit(unit_id="u_ger_a_bur", owner="germany", unit_type="A", province="bur"),
        ]
    )
    orders = [
        _order(
            order_id="o_par_bur",
            unit_ref="u_fra_a_par",
            power="france",
            order_type="MOVE",
            payload={"from": "par", "to": "bur"},
        ),
        _order(
            order_id="o_bur_hold",
            unit_ref="u_ger_a_bur",
            power="germany",
            order_type="HOLD",
            payload={"at": "bur"},
        ),
    ]

    cfg = ExperimentConfig(
        schema_version="1.0",
        experiment_id="exp_drop3",
        games=1,
        agents=[],
        rule_engine={"name": "diplomacy"},
        negotiation={},
        seed=1,
        raw={},
    )
    gm = GameMaster(config=cfg, base_dir=tmp_path, game_id="game_drop3")
    gm.initialize_game_state(state)
    gm.start_phase("1901_spring_movement")
    validated = engine.validate_orders(state, orders)
    resolved = engine.resolve_phase(state, validated)
    snap = gm.commit_snapshot("1901_spring_movement", resolved["output_state"])
    event = gm.complete_phase(
        "1901_spring_movement",
        outcome={"schema_version": "1.0", "snapshot_id": snap.snapshot_id},
    )

    assert snap.phase_id == "1901_spring_movement"
    assert event.event_type == "phase_completed"


@pytest.mark.parametrize("reverse", [False, True])
def test_supported_friendly_swap_never_duplicates_active_occupancy(reverse):
    engine = DiplomacyRuleEngine()
    state = _state([
        _unit(unit_id="tri", owner="austria", unit_type="A", province="tri"),
        _unit(unit_id="vie", owner="austria", unit_type="A", province="vie"),
        _unit(unit_id="bud", owner="turkey", unit_type="A", province="bud"),
    ])
    orders = [
        _order(order_id="1", unit_ref="tri", power="austria", order_type="MOVE", payload={"to":"vie"}),
        _order(order_id="2", unit_ref="vie", power="austria", order_type="MOVE", payload={"to":"tri"}),
        _order(order_id="3", unit_ref="bud", power="turkey", order_type="SUPPORT_MOVE", payload={"supported_from":"tri", "supported_to":"vie"}),
    ]
    if reverse:
        state["units"].reverse()
        orders.reverse()
    resolved = engine.resolve_phase(state, engine.validate_orders(state, orders))
    assert all(row["result"] == "held" for row in resolved["movement_outcomes"])
    assert len({u["province"] for u in resolved["output_state"]["units"]}) == 3
