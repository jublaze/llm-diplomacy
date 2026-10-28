from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ExperimentLogger:
    """Append-only experiment trace logger (text + JSONL)."""

    def __init__(
        self,
        *,
        schema_version: str,
        experiment_id: str,
        experiment_root: str | Path,
    ) -> None:
        self.schema_version = schema_version
        self.experiment_id = experiment_id
        self.experiment_root = Path(experiment_root)
        self.logs_dir = self.experiment_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.text_path = self.logs_dir / "experiment.log"
        self.jsonl_path = self.logs_dir / "experiment_events.jsonl"
        self._archive_existing_logs()

    def event(self, event_type: str, **fields: Any) -> None:
        ts = _utc_now_iso()
        log_lines = fields.get("log_lines")
        if not isinstance(log_lines, list):
            log_lines = []
        row = {
            "schema_version": self.schema_version,
            "timestamp": ts,
            "event_type": event_type,
            "experiment_id": self.experiment_id,
            **fields,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")

        details = " ".join(
            _render_key_value(key, value)
            for key, value in sorted(fields.items())
            if key != "log_lines"
        )
        line = f"[{ts}] {event_type.upper()}"
        if details:
            line = f"{line} {details}"
        with self.text_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            for detail_line in log_lines:
                if not isinstance(detail_line, str):
                    continue
                clean = detail_line.strip()
                if not clean:
                    continue
                handle.write(f"  {clean}\n")

    def relpath(self, path: str | Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.experiment_root.resolve())).replace("\\", "/")
        except Exception:
            return str(path).replace("\\", "/")

    def _archive_existing_logs(self) -> None:
        if not self.text_path.exists() and not self.jsonl_path.exists():
            return
        archived_dir = self.logs_dir / "archived"
        archived_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%fZ")
        if self.text_path.exists():
            self.text_path.replace(archived_dir / f"{stamp}_experiment.log")
        if self.jsonl_path.exists():
            self.jsonl_path.replace(archived_dir / f"{stamp}_experiment_events.jsonl")


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _render_key_value(key: str, value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return f"{key}={value}"
    if isinstance(value, list):
        rendered = ",".join(str(item) for item in value[:8])
        if len(value) > 8:
            rendered += ",..."
        return f"{key}=[{rendered}]"
    if isinstance(value, dict):
        compact = ",".join(f"{k}:{value[k]}" for k in sorted(value.keys())[:8])
        if len(value) > 8:
            compact += ",..."
        return f"{key}={{" + compact + "}}"
    return f"{key}={str(value)}"
