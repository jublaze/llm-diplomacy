from __future__ import annotations

from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _unit(
    *,
    unit_id: str,
    owner: str,
    unit_type: str,
    province: str,
    status: str = "active",
    must_retreat: bool = False,
    dislodged_from: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "owner": owner,
        "type": unit_type,
        "province": province,
        "coast": None,
        "status": status,
        "must_retreat": must_retreat,
        "dislodged_from": dislodged_from,
    }


def _state(units: list[dict[str, object]], standoff: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "map_id": "standard",
        "year": 1901,
        "season": "spring",
        "phase": "retreat",
        "units": units,
        "standoff_provinces": standoff or [],
        "supply_ownership": {},
        "home_centers": {},
    }


def _retreat_order(unit_ref: str, to: str, order_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "order_id": order_id,
        "unit_ref": unit_ref,
        "type": "RETREAT",
        "payload": {"to": to},
    }


def test_legal_retreat_succeeds() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(
                unit_id="u_fra_a_bur",
                owner="france",
                unit_type="A",
                province="bur",
                status="dislodged",
                must_retreat=True,
                dislodged_from="mun",
            )
        ]
    )
    validated = engine.validate_orders(state, [_retreat_order("u_fra_a_bur", "pic", "o1")])
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["retreat_outcomes"]}
    units = {u["unit_id"]: u for u in resolved["output_state"]["units"]}
    assert outcomes["u_fra_a_bur"] == "retreated"
    assert units["u_fra_a_bur"]["province"] == "pic"
    assert units["u_fra_a_bur"]["status"] == "active"


def test_missing_retreat_destroys_unit() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(
                unit_id="u_fra_a_bur",
                owner="france",
                unit_type="A",
                province="bur",
                status="dislodged",
                must_retreat=True,
                dislodged_from="mun",
            )
        ]
    )
    validated = engine.validate_orders(state, [])
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["retreat_outcomes"]}
    assert outcomes["u_fra_a_bur"] == "destroyed"


def test_retreat_to_attackers_origin_is_destroyed() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(
                unit_id="u_fra_a_bur",
                owner="france",
                unit_type="A",
                province="bur",
                status="dislodged",
                must_retreat=True,
                dislodged_from="mun",
            )
        ]
    )
    validated = engine.validate_orders(state, [_retreat_order("u_fra_a_bur", "mun", "o1")])
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["retreat_outcomes"]}
    assert outcomes["u_fra_a_bur"] == "destroyed"


def test_retreat_collision_destroys_all_colliding_units() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(
                unit_id="u_a",
                owner="france",
                unit_type="A",
                province="bur",
                status="dislodged",
                must_retreat=True,
                dislodged_from="mun",
            ),
            _unit(
                unit_id="u_b",
                owner="germany",
                unit_type="A",
                province="mar",
                status="dislodged",
                must_retreat=True,
                dislodged_from="pie",
            ),
        ]
    )
    validated = engine.validate_orders(
        state,
        [_retreat_order("u_a", "gas", "o1"), _retreat_order("u_b", "gas", "o2")],
    )
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["retreat_outcomes"]}
    assert outcomes["u_a"] == "destroyed"
    assert outcomes["u_b"] == "destroyed"


def test_retreat_to_occupied_or_standoff_is_destroyed() -> None:
    engine = DiplomacyRuleEngine()
    state = _state(
        [
            _unit(
                unit_id="u_fra_a_bur",
                owner="france",
                unit_type="A",
                province="bur",
                status="dislodged",
                must_retreat=True,
                dislodged_from="mun",
            ),
            _unit(unit_id="u_eng_a_pic", owner="england", unit_type="A", province="pic", status="active"),
        ],
        standoff=["gas"],
    )
    validated = engine.validate_orders(state, [_retreat_order("u_fra_a_bur", "pic", "o1")])
    resolved = engine.resolve_phase(state, validated)
    outcomes = {row["unit_id"]: row["result"] for row in resolved["retreat_outcomes"]}
    assert outcomes["u_fra_a_bur"] == "destroyed"

    validated_2 = engine.validate_orders(state, [_retreat_order("u_fra_a_bur", "gas", "o2")])
    resolved_2 = engine.resolve_phase(state, validated_2)
    outcomes_2 = {row["unit_id"]: row["result"] for row in resolved_2["retreat_outcomes"]}
    assert outcomes_2["u_fra_a_bur"] == "destroyed"


def test_foreign_power_cannot_order_retreat():
    engine = DiplomacyRuleEngine()
    state = _state([_unit(unit_id="france", owner="france", unit_type="A", province="bur", status="dislodged", must_retreat=True)])
    order = {**_retreat_order("france", "pic", "foreign"), "power": "germany"}
    validated = engine.validate_orders(state, [order])
    assert validated["validated_orders"][0]["validation"] == "rejected"
    assert validated["validated_orders"][0]["reason"] == "wrong_power"
