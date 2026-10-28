from __future__ import annotations

from pathlib import Path

from llm_diplomacy.observatory.api import GameObservatoryData, summarize_board_evolution


def _synthetic_game_data(tmp_path: Path) -> GameObservatoryData:
    initial_state = {
        "schema_version": "1.0",
        "units": [
            {"unit_id": "u_aus_a_bud", "owner": "austria", "type": "A", "province": "bud", "status": "active"},
            {"unit_id": "u_aus_a_vie", "owner": "austria", "type": "A", "province": "vie", "status": "active"},
        ],
        "supply_ownership": {"bud": "austria", "vie": "austria", "ser": None},
    }
    spring_output = {
        "schema_version": "1.0",
        "units": [
            {"unit_id": "u_aus_a_bud", "owner": "austria", "type": "A", "province": "ser", "status": "active"},
            {"unit_id": "u_aus_a_vie", "owner": "austria", "type": "A", "province": "vie", "status": "active"},
        ],
        "supply_ownership": {"bud": "austria", "vie": "austria", "ser": None},
    }
    fall_output = {
        "schema_version": "1.0",
        "units": [
            {"unit_id": "u_aus_a_bud", "owner": "austria", "type": "A", "province": "ser", "status": "active"},
            {"unit_id": "u_aus_a_vie", "owner": "austria", "type": "A", "province": "vie", "status": "active"},
        ],
        "supply_ownership": {"bud": "austria", "vie": "austria", "ser": "austria"},
    }
    return GameObservatoryData(
        game_dir=tmp_path,
        phases=["1901_spring_movement", "1901_fall_retreat"],
        events=[],
        global_messages=[],
        private_messages=[],
        phase_submitted_orders={},
        phase_resolutions={
            "1901_spring_movement": {
                "output_state": spring_output,
                "movement_outcomes": [
                    {
                        "schema_version": "1.0",
                        "unit_id": "u_aus_a_bud",
                        "result": "moved",
                        "from": "bud",
                        "to": "ser",
                    }
                ],
                "adjustment_outcomes": [],
            },
            "1901_fall_retreat": {
                "output_state": fall_output,
                "movement_outcomes": [],
                "adjustment_outcomes": [],
            },
        },
        phase_reflections={},
        snapshots=[
            {
                "schema_version": "1.0",
                "snapshot_id": "initial",
                "payload": initial_state,
                "phase_id": None,
            }
        ],
        assignments_by_power={"austria": "agent_a"},
        runtime_outputs_by_agent={},
    )


def test_board_evolution_initial_setup_not_counted_as_gain_and_fall_owner_change_detected(tmp_path: Path) -> None:
    data = _synthetic_game_data(tmp_path)
    deltas = summarize_board_evolution(data)
    assert len(deltas) == 2

    spring = deltas[0]
    assert spring["powers"]["austria"]["units_gained"] == []
    assert spring["powers"]["austria"]["centers_gained"] == []
    assert spring["center_changes"] == []
    assert spring["moves"][0]["from"] == "bud"
    assert spring["moves"][0]["to"] == "ser"

    fall = deltas[1]
    assert fall["center_changes"] == [{"province": "ser", "from_owner": None, "to_owner": "austria"}]
    assert fall["powers"]["austria"]["centers_gained"] == ["ser"]
