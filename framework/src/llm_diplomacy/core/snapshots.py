from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ids import new_id


@dataclass(frozen=True)
class SnapshotRecord:
    schema_version: str
    snapshot_id: str
    experiment_id: str
    game_id: str
    phase_id: str | None
    payload: dict[str, Any]
    created_at: str


class SnapshotStore:
    def __init__(self, snapshots_dir: str | Path, schema_version: str = "1.0") -> None:
        self.snapshots_dir = Path(snapshots_dir)
        self.schema_version = schema_version
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshots_dir / f"{snapshot_id}.json"

    def write(
        self,
        *,
        experiment_id: str,
        game_id: str,
        payload: dict[str, Any],
        phase_id: str | None = None,
        snapshot_id: str | None = None,
        overwrite: bool = False,
    ) -> SnapshotRecord:
        resolved_snapshot_id = snapshot_id or new_id("snap")
        path = self.snapshot_path(resolved_snapshot_id)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"Snapshot already exists and overwrite=False: {resolved_snapshot_id}"
            )

        record = SnapshotRecord(
            schema_version=self.schema_version,
            snapshot_id=resolved_snapshot_id,
            experiment_id=experiment_id,
            game_id=game_id,
            phase_id=phase_id,
            payload=payload,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        path.write_text(json.dumps(asdict(record), sort_keys=True, indent=2), encoding="utf-8")
        return record

    def load(self, snapshot_id: str) -> dict[str, Any]:
        return json.loads(self.snapshot_path(snapshot_id).read_text(encoding="utf-8"))
