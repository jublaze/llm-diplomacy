from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .event_contracts import validate_core_event
from .schema_versions import RAW_LAYOUT_VERSION, raw_version_error


@dataclass(frozen=True)
class ArtifactValidationError:
    category: str
    path: Path
    line_no: int | None
    detail: str

    def __str__(self) -> str:
        suffix = f":{self.line_no}" if self.line_no is not None else ""
        return f"{self.category}:{self.path}{suffix}:{self.detail}"


@dataclass(frozen=True)
class ArtifactValidationReport:
    root_dir: Path
    files_checked: int
    errors: list[ArtifactValidationError]

    @property
    def ok(self) -> bool:
        return not self.errors


class ArtifactValidator:
    """Validates schema/version and minimal required envelope fields for artifacts."""

    def __init__(self, schema_version: str = RAW_LAYOUT_VERSION) -> None:
        self.schema_version = schema_version

    def validate_experiment_dir(self, experiment_root: str | Path) -> ArtifactValidationReport:
        root = Path(experiment_root)
        errors: list[ArtifactValidationError] = []
        files_checked = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in {".json", ".jsonl"}:
                continue
            files_checked += 1
            if path.suffix == ".json":
                self._validate_json_file(path, errors)
            else:
                self._validate_jsonl_file(path, errors)
        return ArtifactValidationReport(root_dir=root, files_checked=files_checked, errors=errors)

    def _validate_json_file(self, path: Path, errors: list[ArtifactValidationError]) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self._add_error(errors, "invalid_json", path, None, str(exc))
            return
        if isinstance(payload, dict):
            self._check_schema(payload, path, errors)
            self._check_shape_by_file(path, payload, errors)
        else:
            self._add_error(errors, "json_root_not_object", path, None, "root must be object")

    def _validate_jsonl_file(self, path: Path, errors: list[ArtifactValidationError]) -> None:
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                self._add_error(errors, "invalid_jsonl", path, idx, str(exc))
                continue
            if not isinstance(row, dict):
                self._add_error(errors, "jsonl_row_not_object", path, idx, "row must be object")
                continue
            self._check_schema(row, path, errors, line_no=idx)
            for detail in validate_core_event(row, path.name):
                self._add_error(errors, "invalid_event_payload", path, idx, detail)
            if path.name == "events.jsonl":
                self._require_fields(
                    row,
                    ["event_id", "event_type", "experiment_id", "game_id", "payload", "timestamp"],
                    path,
                    errors,
                    idx,
                )
            if path.name in {"global.jsonl", "inbox.jsonl"}:
                self._require_fields(
                    row,
                    [
                        "message_id",
                        "experiment_id",
                        "game_id",
                        "phase_id",
                        "tick",
                        "from_agent_id",
                        "to",
                        "body",
                        "created_at",
                    ],
                    path,
                    errors,
                    idx,
                )
            if path.name.endswith("per_tick.jsonl") or path.name.endswith("per_phase.jsonl"):
                self._require_fields(
                    row,
                    ["experiment_id", "game_id", "agent_id", "usage"],
                    path,
                    errors,
                    idx,
                )

    def _check_shape_by_file(
        self, path: Path, payload: dict[str, Any], errors: list[ArtifactValidationError]
    ) -> None:
        filename = path.name
        if filename.endswith(".snapshot.json") or path.parent.name == "snapshots":
            self._require_fields(
                payload,
                ["snapshot_id", "experiment_id", "game_id", "phase_id", "payload", "created_at"],
                path,
                errors,
            )
        if path.parent.name == "orders" or filename in {"submitted_orders.json", "validated_orders.json"}:
            self._require_fields(payload, ["phase_id", "orders"], path, errors)
        if path.parent.name == "resolution" or filename in {"adjudication.json", "resolution.json"}:
            self._require_fields(
                payload,
                ["phase", "validated_orders", "movement_outcomes", "output_state"],
                path,
                errors,
            )
        if filename == "manifest.json":
            if "agent_id" in payload and "games_played" in payload:
                self._require_fields(payload, ["agent_id", "games_played"], path, errors)
            elif "agent_id" in payload and "runtime_kind" in payload:
                self._require_fields(payload, ["agent_id", "runtime_kind"], path, errors)
            else:
                self._require_fields(payload, ["experiment_id", "games", "agents", "seed"], path, errors)
        elif filename == "run_summary.json":
            self._require_fields(
                payload,
                [
                    "experiment_id",
                    "games_requested",
                    "games_completed",
                    "leaderboard_overall_path",
                    "leaderboard_by_country_path",
                    "cost_summary_path",
                ],
                path,
                errors,
            )
        elif filename == "overall.json":
            self._require_fields(payload, ["games_count", "wins_by_agent"], path, errors)
        elif filename == "by_country.json":
            self._require_fields(payload, ["countries"], path, errors)
        elif filename == "cost_summary.json":
            self._require_fields(payload, ["records_count", "by_agent"], path, errors)
        elif filename in {"leaderboard.json"}:
            self._require_fields(payload, ["games_count"], path, errors)
        elif filename in {"order_artifact.json"}:
            self._require_fields(payload, ["phase_id", "orders"], path, errors)
        elif filename in {"resolution_artifact.json"}:
            self._require_fields(
                payload,
                ["phase", "validated_orders", "movement_outcomes", "output_state"],
                path,
                errors,
            )
        elif filename in {"cost_log.json"}:
            self._require_fields(payload, ["experiment_id", "game_id", "agent_id", "usage"], path, errors)

    def _check_schema(
        self,
        payload: dict[str, Any],
        path: Path,
        errors: list[ArtifactValidationError],
        line_no: int | None = None,
    ) -> None:
        detail = raw_version_error(payload, self.schema_version)
        if detail:
            self._add_error(
                errors,
                "schema_version_mismatch",
                path,
                line_no,
                detail,
            )

    def _require_fields(
        self,
        payload: dict[str, Any],
        fields: list[str],
        path: Path,
        errors: list[ArtifactValidationError],
        line_no: int | None = None,
    ) -> None:
        missing = [field for field in fields if field not in payload]
        if missing:
            self._add_error(errors, "missing_fields", path, line_no, f"{missing}")

    def _add_error(
        self,
        errors: list[ArtifactValidationError],
        category: str,
        path: Path,
        line_no: int | None,
        detail: str,
    ) -> None:
        errors.append(
            ArtifactValidationError(
                category=category,
                path=path,
                line_no=line_no,
                detail=detail,
            )
        )
