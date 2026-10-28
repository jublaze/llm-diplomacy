from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.runner.experiment_runner import ExperimentRunner


@dataclass(frozen=True)
class ReplayComparisonResult:
    deterministic: bool
    differences: list[str]
    compared_files: int


def compare_rerun_determinism(config: ExperimentConfig) -> ReplayComparisonResult:
    with TemporaryDirectory() as run_a, TemporaryDirectory() as run_b:
        ExperimentRunner(config=config, base_dir=run_a).run()
        ExperimentRunner(config=config, base_dir=run_b).run()
        exp_a = Path(run_a) / "experiments" / config.experiment_id
        exp_b = Path(run_b) / "experiments" / config.experiment_id
        return compare_experiment_dirs(exp_a, exp_b)


def compare_experiment_dirs(root_a: str | Path, root_b: str | Path) -> ReplayComparisonResult:
    return _compare_dirs(Path(root_a), Path(root_b))


# Only generated artifact locations are relocatable. Evidence paths remain meaningful.
_LOCAL_PATH_FIELDS = {
    "leaderboard_overall_path", "leaderboard_by_country_path", "cost_summary_path",
    "output_artifact_path", "observatory_path", "reporter_summary_path",
}


def _compare_dirs(root_a: Path, root_b: Path) -> ReplayComparisonResult:
    diffs: list[str] = []
    normalized = []
    for side, root in (("a", root_a), ("b", root_b)):
        records = {}
        if not root.is_dir():
            diffs.append(f"invalid_root_{side}:{root}")
        else:
            files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl"})
            for path in files:
                relative = path.relative_to(root)
                try:
                    records[relative] = _normalize(path, root)
                except (OSError, ValueError, UnicodeError) as exc:
                    # Never include raw content from an invalid/private artifact.
                    location = f":line={exc.lineno}" if isinstance(exc, json.JSONDecodeError) else ""
                    diffs.append(f"invalid_artifact_{side}:{relative}{location}:{type(exc).__name__}")
            manifest = records.get(Path("manifest.json"))
            if not isinstance(manifest, dict) or not manifest.get("schema_version") or not manifest.get("experiment_id"):
                diffs.append(f"missing_or_invalid_manifest_{side}:manifest.json")
            if not any(value for path, value in records.items() if path != Path("manifest.json")):
                diffs.append(f"no_execution_artifacts_{side}")
        normalized.append(records)
    left, right = normalized
    for relative in sorted(left.keys() - right.keys()):
        diffs.append(f"missing_in_b:{relative}")
    for relative in sorted(right.keys() - left.keys()):
        diffs.append(f"missing_in_a:{relative}")
    shared = left.keys() & right.keys()
    for relative in sorted(shared):
        pointer = _first_difference(left[relative], right[relative])
        if pointer is not None:
            diffs.append(f"content_mismatch:{relative}#{pointer}")
    return ReplayComparisonResult(not diffs, diffs, len(shared))


def _first_difference(left: Any, right: Any, pointer: str = "") -> str | None:
    if type(left) is not type(right):
        return pointer
    if isinstance(left, dict):
        for key in sorted(left.keys() | right.keys()):
            child = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in left or key not in right:
                return child
            found = _first_difference(left[key], right[key], child)
            if found is not None:
                return found
    elif isinstance(left, list):
        for index, (a, b) in enumerate(zip(left, right)):
            found = _first_difference(a, b, f"{pointer}/{index}")
            if found is not None:
                return found
        if len(left) != len(right):
            return f"{pointer}/{min(len(left), len(right))}"
    elif left != right:
        return pointer
    return None


def _normalize(path: Path, root: Path) -> Any:
    if path.suffix == ".json":
        return _strip_nondeterministic(json.loads(path.read_text(encoding="utf-8")), root)
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(_strip_nondeterministic(json.loads(line), root))
            except json.JSONDecodeError as exc:
                raise json.JSONDecodeError("invalid JSONL", "\n" * (line_number - 1), line_number - 1) from exc
    return rows


def _strip_nondeterministic(value: Any, root: Path, *, envelope: bool = True) -> Any:
    if isinstance(value, dict):
        volatile = set()
        if envelope and "event_type" in value:
            volatile.update(("event_id", "timestamp"))
        if envelope and ("snapshot_id" in value or "message_id" in value):
            volatile.add("created_at")
        result = {}
        for key, item in value.items():
            if key in volatile:
                continue
            if key in _LOCAL_PATH_FIELDS and isinstance(item, str):
                path = Path(item)
                if path.is_absolute():
                    try:
                        item = path.resolve().relative_to(root.resolve()).as_posix()
                    except ValueError:
                        pass
            result[key] = _strip_nondeterministic(item, root, envelope=False)
        return result
    if isinstance(value, list):
        return [_strip_nondeterministic(item, root, envelope=False) for item in value]
    return value
