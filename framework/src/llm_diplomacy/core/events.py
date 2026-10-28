from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ids import new_id


@dataclass(frozen=True)
class EventRecord:
    schema_version: str
    event_id: str
    event_type: str
    experiment_id: str
    game_id: str
    phase_id: str | None
    timestamp: str
    payload: dict[str, Any]


class EventLog:
    def __init__(self, path: str | Path, schema_version: str = "1.0") -> None:
        self.path = Path(path)
        self.schema_version = schema_version
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(
        self,
        *,
        event_type: str,
        experiment_id: str,
        game_id: str,
        payload: dict[str, Any],
        phase_id: str | None = None,
    ) -> EventRecord:
        record = EventRecord(
            schema_version=self.schema_version,
            event_id=new_id("evt"),
            event_type=event_type,
            experiment_id=experiment_id,
            game_id=game_id,
            phase_id=phase_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True))
            handle.write("\n")
        return record

    def read_all(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
