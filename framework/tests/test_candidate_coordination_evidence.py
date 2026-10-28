from pathlib import Path

from llm_diplomacy.observatory.api import GameObservatoryData
from llm_diplomacy.observatory.generator import _candidate_coordination_evidence


def test_candidate_coordination_links_only_strictly_later_canonical_resolutions(tmp_path: Path) -> None:
    data = GameObservatoryData(
        game_dir=tmp_path, phases=[], events=[], assignments_by_power={}, phase_submitted_orders={},
        phase_reflections={}, snapshots=[], runtime_outputs_by_agent={}, global_messages=[
            {"phase_id": "1901_spring_negotiation", "from_agent_id": "a", "body": "Please support the move."},
            {"phase_id": "1901_fall_negotiation", "from_agent_id": "a", "body": "Coordinate defensive cooperation."},
            {"phase_id": "1901_winter_negotiation", "from_agent_id": "a", "body": "A concrete request."},
            {"phase_id": "1902_fall_negotiation", "from_agent_id": "a", "body": "Support request."},
        ], private_messages=[], phase_resolutions={
            "1901_spring_movement": {}, "1901_fall_movement": {}, "1901_winter_adjustment": {},
        },
    )
    rows = _candidate_coordination_evidence(data)["rows"]
    assert [row["followed_by_public_action"]["phase_id"] if row["followed_by_public_action"] else None for row in rows] == [
        "1901_spring_movement", "1901_fall_movement", "1901_winter_adjustment", None
    ]
    assert rows[-1]["no_later_public_action_in_scope"] is True
    for row in rows[:-1]:
        rank = row["followed_by_public_action"]["event_rank"]
        assert tuple(rank["public_action"]) > tuple(rank["communication"])
