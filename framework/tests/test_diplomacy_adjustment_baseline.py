from __future__ import annotations

from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _unit(
    *,
    unit_id: str,
    owner: str,
    unit_type: str,
    province: str,
    status: str = "active",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "owner": owner,
        "type": unit_type,
        "province": province,
        "coast": None,
        "status": status,
        "must_retreat": False,
        "dislodged_from": None,
    }


def _state(units: list[dict[str, object]], supply_ownership: dict[str, str | None]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "map_id": "standard",
        "year": 1901,
        "season": "winter",
        "phase": "adjustment",
        "units": units,
        "supply_ownership": supply_ownership,
        "home_centers": {},
    }


def test_adjustment_build_in_owned_home_center() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [_unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par")],
        {"par": "france", "mar": "france", "bre": "france"},
    )
    orders = [
        {
            "schema_version": "1.0",
            "order_id": "o_build_mar",
            "power": "france",
            "type": "BUILD",
            "payload": {"unit_type": "A", "province": "mar", "coast": None},
        }
    ]
    validated = engine.validate_orders(state, orders)
    resolved = engine.resolve_phase(state, validated)
    built = [row for row in resolved["adjustment_outcomes"] if row["result"] == "built"]
    assert len(built) == 1
    new_unit = next(u for u in resolved["output_state"]["units"] if u["unit_id"] == built[0]["unit_id"])
    assert new_unit["unit_id"].startswith("unit_")
    assert new_unit["province"] == "mar" and new_unit["status"] == "active"
    assert new_unit["metadata"] == {"origin_province": "mar", "created_year": 1901, "created_phase": "1901_winter_adjustment"}


def test_adjustment_illegal_build_rejected() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [_unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par")],
        {"par": "france", "mar": "france", "bre": "france"},
    )
    orders = [
        {
            "schema_version": "1.0",
            "order_id": "o_build_bel",
            "power": "france",
            "type": "BUILD",
            "payload": {"unit_type": "A", "province": "bel", "coast": None},
        }
    ]
    validated = engine.validate_orders(state, orders)
    resolved = engine.resolve_phase(state, validated)
    assert any(row["result"] == "rejected" for row in resolved["adjustment_outcomes"])


def test_adjustment_disband_order_applied() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par"),
            _unit(unit_id="u_fra_a_mar", owner="france", unit_type="A", province="mar"),
        ],
        {"par": "france"},
    )
    orders = [
        {
            "schema_version": "1.0",
            "order_id": "o_disband_mar",
            "power": "france",
            "type": "DISBAND",
            "payload": {"unit_ref": "u_fra_a_mar"},
        }
    ]
    validated = engine.validate_orders(state, orders)
    resolved = engine.resolve_phase(state, validated)
    units = {u["unit_id"]: u for u in resolved["output_state"]["units"]}
    assert units["u_fra_a_mar"]["status"] == "destroyed"


def test_adjustment_auto_disband_when_missing_orders() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(unit_id="u_fra_a_par", owner="france", unit_type="A", province="par"),
            _unit(unit_id="u_fra_a_mar", owner="france", unit_type="A", province="mar"),
        ],
        {"par": "france"},
    )
    validated = engine.validate_orders(state, [])
    resolved = engine.resolve_phase(state, validated)
    auto = [row for row in resolved["adjustment_outcomes"] if row["result"] == "auto_disbanded"]
    assert len(auto) == 1
