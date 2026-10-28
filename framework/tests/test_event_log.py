from __future__ import annotations

from llm_diplomacy.core.events import EventLog


def test_event_log_appends_jsonl_entries(tmp_path) -> None:
    log = EventLog(tmp_path / "events.jsonl", schema_version="1.0")

    first = log.append(
        event_type="phase_started",
        experiment_id="exp_001",
        game_id="game_001",
        phase_id="1901_spring_negotiation",
        payload={"tick": 1},
    )
    second = log.append(
        event_type="phase_completed",
        experiment_id="exp_001",
        game_id="game_001",
        phase_id="1901_spring_negotiation",
        payload={"tick": 1, "status": "ok"},
    )

    rows = log.read_all()
    assert len(rows) == 2
    assert rows[0]["event_id"] == first.event_id
    assert rows[1]["event_id"] == second.event_id
    assert rows[0]["schema_version"] == "1.0"
    assert rows[1]["event_type"] == "phase_completed"
