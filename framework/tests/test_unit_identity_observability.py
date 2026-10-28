from __future__ import annotations

from pathlib import Path

from llm_diplomacy.observatory.api import GameObservatoryData, summarize_board_evolution
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def test_new_game_initial_units_use_opaque_ids_with_metadata() -> None:
    state = DiplomacyRuleEngine().initial_state({})
    units = state["units"]
    assert units
    assert all(str(unit["unit_id"]).startswith("unit_") for unit in units)
    assert all("germany" not in str(unit["unit_id"]) and "mun" not in str(unit["unit_id"]) for unit in units)
    munich = next(unit for unit in units if unit["owner"] == "germany" and unit["province"] == "mun")
    assert munich["metadata"] == {
        "origin_province": "mun",
        "created_year": 1901,
        "created_phase": "1901_spring_movement",
    }


def test_phase_local_move_reporting_uses_current_location_not_legacy_identity(tmp_path: Path) -> None:
    engine = DiplomacyRuleEngine()
    state = {
        "schema_version": "1.0",
        "year": 1901,
        "season": "spring",
        "phase": "movement",
        "units": [
            {
                "unit_id": "unit_0042",
                "owner": "germany",
                "type": "A",
                "province": "mun",
                "coast": None,
                "status": "active",
                "metadata": {"origin_province": "mun", "created_year": 1901, "created_phase": "1901_spring_movement"},
            }
        ],
        "supply_ownership": {"mun": "germany", "ruh": None, "hol": None},
    }
    spring_orders = [{"order_id": "spring", "unit_ref": "unit_0042", "power": "germany", "type": "MOVE", "payload": {"from": "mun", "to": "ruh"}}]
    spring = engine.resolve_phase(state, engine.validate_orders(state, spring_orders))
    fall_state = spring["output_state"]
    fall_state.update({"year": 1901, "season": "fall", "phase": "movement"})
    fall_orders = [{"order_id": "fall", "unit_ref": "unit_0042", "power": "germany", "type": "MOVE", "payload": {"from": "ruh", "to": "hol"}}]
    fall = engine.resolve_phase(fall_state, engine.validate_orders(fall_state, fall_orders))

    data = GameObservatoryData(
        game_dir=tmp_path / "game_001",
        phases=["1901_spring_movement", "1901_fall_movement"],
        events=[], global_messages=[], private_messages=[], phase_submitted_orders={},
        phase_resolutions={"1901_spring_movement": spring, "1901_fall_movement": fall},
        phase_reflections={}, snapshots=[{"payload": state}], assignments_by_power={"germany": "agent_germany"}, runtime_outputs_by_agent={},
    )
    deltas = summarize_board_evolution(data)
    assert deltas[0]["moves"] == [{"power": "germany", "unit_id": "unit_0042", "from": "mun", "to": "ruh", "result": "moved"}]
    assert deltas[1]["moves"] == [{"power": "germany", "unit_id": "unit_0042", "from": "ruh", "to": "hol", "result": "moved"}]
    assert fall["output_state"]["units"][0]["metadata"]["origin_province"] == "mun"


def test_legacy_unit_ids_remain_usable_in_direct_historical_states() -> None:
    engine = DiplomacyRuleEngine()
    state = {
        "schema_version": "1.0", "year": 1901, "season": "spring", "phase": "movement",
        "units": [{"unit_id": "u_ger_a_mun", "owner": "germany", "type": "A", "province": "mun", "coast": None, "status": "active"}],
        "supply_ownership": {"mun": "germany", "ruh": None},
    }
    validated = engine.validate_orders(state, [{"order_id": "legacy", "unit_ref": "u_ger_a_mun", "power": "germany", "type": "MOVE", "payload": {"from": "mun", "to": "ruh"}}])
    assert validated["validated_orders"][0]["validation"] == "accepted"
