from __future__ import annotations

import json
import random
from typing import Any

from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _random_state(engine: DiplomacyRuleEngine, rng: random.Random, index: int) -> dict[str, Any]:
    powers = list(engine.map_def.powers)
    provinces = list(engine.map_def.provinces)
    occupiable = [p for p in provinces if p.occupiable_by]
    unit_count = rng.randint(4, 10)
    chosen = rng.sample(occupiable, k=unit_count)
    units: list[dict[str, Any]] = []
    for i, province in enumerate(chosen):
        unit_type = rng.choice(list(province.occupiable_by))
        coast = None
        if unit_type == "F" and province.coasts:
            coast = rng.choice(list(province.coasts))
        units.append(
            {
                "schema_version": "1.0",
                "unit_id": f"u_{index}_{i:02d}",
                "owner": rng.choice(powers),
                "type": unit_type,
                "province": province.id,
                "coast": coast,
                "status": "active",
                "must_retreat": False,
                "dislodged_from": None,
            }
        )
    return {
        "schema_version": "1.0",
        "map_id": "standard",
        "year": 1901 + (index % 3),
        "season": "spring" if index % 2 == 0 else "fall",
        "phase": "movement",
        "units": units,
        "supply_ownership": {},
        "home_centers": {},
    }


def _random_orders(engine: DiplomacyRuleEngine, state: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    provinces = list(engine.province_by_id.keys())
    orders: list[dict[str, Any]] = []
    for unit in state["units"]:
        uid = str(unit["unit_id"])
        unit_type = str(unit["type"])
        province = str(unit["province"])
        kind = rng.choice(["HOLD", "MOVE", "MOVE", "MOVE", "SUPPORT_HOLD"])
        if kind == "HOLD":
            payload: dict[str, Any] = {"at": province}
        elif kind == "SUPPORT_HOLD":
            target = rng.choice(state["units"])["province"]
            payload = {"from": province, "target": target}
        else:
            if rng.random() < 0.65:
                if unit_type == "A":
                    destinations = list(engine.province_by_id[province].army_adjacency)
                    dest = rng.choice(destinations) if destinations else province
                    via_convoy = bool(rng.random() < 0.15)
                    payload = {"from": province, "to": dest, "via_convoy": via_convoy}
                else:
                    adjacency = sorted(engine._fleet_adjacency(unit.get("coast") or province))
                    if adjacency:
                        dest = rng.choice(adjacency)
                    else:
                        dest = province
                    payload = {"from": province, "to": dest}
            else:
                payload = {"from": province, "to": rng.choice(provinces)}
        orders.append(
            {
                "schema_version": "1.0",
                "order_id": f"o_{uid}",
                "unit_ref": uid,
                "power": unit["owner"],
                "type": kind,
                "payload": payload,
            }
        )
    rng.shuffle(orders)
    return orders


def test_fuzz_movement_resolution_invariants_and_determinism() -> None:
    engine = DiplomacyRuleEngine()
    rng = random.Random(123456)

    for case_index in range(30):
        state = _random_state(engine, rng, case_index)
        orders = _random_orders(engine, state, rng)
        validated_a = engine.validate_orders(state, orders)
        resolved_a = engine.resolve_phase(state, validated_a)
        validated_b = engine.validate_orders(state, list(reversed(orders)))
        resolved_b = engine.resolve_phase(state, validated_b)

        if json.dumps(resolved_a, sort_keys=True) != json.dumps(resolved_b, sort_keys=True):
            raise AssertionError(
                f"determinism_mismatch case={case_index} state={json.dumps(state, sort_keys=True)}"
            )

        assert resolved_a["schema_version"] == "1.0"
        assert resolved_a["output_state"]["schema_version"] == "1.0"
        for row in resolved_a["movement_outcomes"]:
            assert row["schema_version"] == "1.0"
        for row in validated_a["validated_orders"]:
            assert row["schema_version"] == "1.0"

        active_units = [u for u in resolved_a["output_state"]["units"] if u.get("status") == "active"]
        occupied = [u["province"] for u in active_units]
        if len(occupied) != len(set(occupied)):
            raise AssertionError(f"duplicate_occupancy case={case_index} occupied={occupied}")

        province_ids = set(engine.province_by_id.keys())
        coast_ids = set(engine.coast_by_id.keys())
        power_ids = set(engine.map_def.powers)
        for unit in resolved_a["output_state"]["units"]:
            assert unit["province"] in province_ids
            coast = unit.get("coast")
            if coast is not None:
                assert coast in coast_ids
            if unit.get("status") == "destroyed":
                assert unit.get("must_retreat") is False

        for owner in resolved_a["output_state"].get("supply_ownership", {}).values():
            assert owner in power_ids
