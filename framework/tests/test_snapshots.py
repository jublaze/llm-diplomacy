from __future__ import annotations

import pytest

from llm_diplomacy.core.snapshots import SnapshotStore


def test_snapshot_write_and_load(tmp_path) -> None:
    store = SnapshotStore(tmp_path, schema_version="1.0")

    record = store.write(
        experiment_id="exp_001",
        game_id="game_001",
        phase_id="1901_spring_movement",
        snapshot_id="phase_001",
        payload={"board": {"units": []}},
    )
    loaded = store.load(record.snapshot_id)

    assert loaded["schema_version"] == "1.0"
    assert loaded["snapshot_id"] == "phase_001"
    assert loaded["phase_id"] == "1901_spring_movement"


def test_snapshot_no_overwrite_by_default(tmp_path) -> None:
    store = SnapshotStore(tmp_path, schema_version="1.0")
    store.write(
        experiment_id="exp_001",
        game_id="game_001",
        snapshot_id="dup",
        payload={"a": 1},
    )

    with pytest.raises(FileExistsError):
        store.write(
            experiment_id="exp_001",
            game_id="game_001",
            snapshot_id="dup",
            payload={"a": 2},
        )
