from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CostRecord:
    schema_version: str
    experiment_id: str
    game_id: str
    phase_id: str | None
    agent_id: str
    usage: dict[str, Any]


class CostTracker:
    """Objective usage accounting."""

    def __init__(self, schema_version: str = "1.0") -> None:
        self.schema_version = schema_version
        self._records: list[CostRecord] = []

    def record_usage(
        self,
        *,
        experiment_id: str,
        game_id: str,
        agent_id: str,
        usage: dict[str, Any],
        phase_id: str | None = None,
    ) -> CostRecord:
        record = CostRecord(
            schema_version=self.schema_version,
            experiment_id=experiment_id,
            game_id=game_id,
            phase_id=phase_id,
            agent_id=agent_id,
            usage=usage,
        )
        self._records.append(record)
        return record

    def records(self) -> list[CostRecord]:
        return list(self._records)

    def summary(self) -> dict[str, Any]:
        by_agent: dict[str, dict[str, float | None]] = {}
        for record in self._records:
            row = by_agent.setdefault(record.agent_id, {"tokens": 0.0, "estimated_cost": 0.0, "calls": 0.0})
            row["calls"] += 1
            for field in ("tokens", "estimated_cost"):
                value = record.usage.get(field)
                if not isinstance(value, (int, float)):
                    row[field] = None
                elif row[field] is not None:
                    row[field] += float(value)
        return {
            "schema_version": self.schema_version,
            "records_count": len(self._records),
            "by_agent": dict(sorted(by_agent.items())),
        }

    def write_summary(self, path: str | Path) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self.summary(), sort_keys=True, indent=2), encoding="utf-8")
        return out_path
