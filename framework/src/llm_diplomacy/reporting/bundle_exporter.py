"""Export completed experiments to the Diplomacy Viewer v1 factual bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


from llm_diplomacy.compliance.schema_versions import RAW_LAYOUT_VERSION, raw_version_error
REPORT_SCHEMA_VERSION = "diplomacy_report_bundle_v1"
AUDIENCE = "omniscient_postgame"
PHASE_PATTERN = re.compile(r"^(?P<year>\d{4})_(?P<season>spring|fall|winter)_(?P<kind>movement|retreat|adjustment)$")
PHASE_RANK = {
    ("spring", "movement"): 0,
    ("spring", "retreat"): 1,
    ("fall", "movement"): 2,
    ("fall", "retreat"): 3,
    ("winter", "adjustment"): 4,
}
BUNDLED_SCHEMA_PATH = Path(__file__).resolve().parent / "contracts" / "diplomacy_report_bundle_v1.schema.json"


class ExportInputError(ValueError):
    """A completed experiment cannot be exported without inventing data."""


@dataclass(frozen=True)
class ExperimentInspection:
    root: Path
    experiment_id: str
    game_ids: tuple[str, ...]
    manifest: Mapping[str, Any]
    population_manifest: Mapping[str, Any]
    run_summary: Mapping[str, Any]
    cost_summary: Mapping[str, Any]


def _relative_label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _require_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file():
        raise ExportInputError(
            f"required completed-experiment artifact is missing: {relative} "
            f"(raw layout {RAW_LAYOUT_VERSION})"
        )
    return path


def _require_directory(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_dir():
        raise ExportInputError(
            f"required completed-experiment directory is missing: {relative} "
            f"(raw layout {RAW_LAYOUT_VERSION})"
        )
    return path


def _load_json(path: Path, root: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExportInputError(f"invalid JSON artifact {_relative_label(path, root)}: {error}") from error


def _require_raw_version(payload: Any, label: str) -> None:
    detail = raw_version_error(payload)
    if detail:
        raise ExportInputError(f"{label}: {detail}")


def _load_raw_json(path: Path, root: Path) -> dict[str, Any]:
    payload = _load_json(path, root)
    _require_raw_version(payload, _relative_label(path, root))
    return payload


def _json_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _resolve_schema_ref(root_schema: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        raise ExportInputError(f"viewer v1 JSON Schema uses unsupported external reference {reference!r}")
    value: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ExportInputError(f"viewer v1 JSON Schema has unresolved reference {reference!r}")
        value = value[part]
    if not isinstance(value, dict):
        raise ExportInputError(f"viewer v1 JSON Schema reference {reference!r} is not an object")
    return value


def _validate_schema_node(instance: Any, schema: Mapping[str, Any], root_schema: Mapping[str, Any], path: str) -> None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        _validate_schema_node(instance, _resolve_schema_ref(root_schema, reference), root_schema, path)
    if "const" in schema and instance != schema["const"]:
        raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: value is not in the allowed enum")
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(allowed, list) or not any(_json_type_matches(instance, item) for item in allowed):
            raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: expected type {expected_type!r}")
    if isinstance(instance, str) and isinstance(schema.get("minLength"), int) and len(instance) < schema["minLength"]:
        raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: string is too short")
    if isinstance(instance, list):
        if isinstance(schema.get("minItems"), int) and len(instance) < schema["minItems"]:
            raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: array has too few items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                _validate_schema_node(item, item_schema, root_schema, f"{path}/{index}")
    if isinstance(instance, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for field in required:
                if field not in instance:
                    raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: missing required field {field!r}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for field, child_schema in properties.items():
                if field in instance and isinstance(child_schema, dict):
                    _validate_schema_node(instance[field], child_schema, root_schema, f"{path}/{field}")
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            known = set(properties) if isinstance(properties, dict) else set()
            for field, value in instance.items():
                if field not in known:
                    _validate_schema_node(value, additional, root_schema, f"{path}/{field}")
        elif additional is False:
            known = set(properties) if isinstance(properties, dict) else set()
            unexpected = sorted(set(instance).difference(known))
            if unexpected:
                raise ExportInputError(f"viewer v1 JSON Schema violation at {path}: unexpected field {unexpected[0]!r}")


def validate_report_schema(bundle: Mapping[str, Any], schema_path: str | Path = BUNDLED_SCHEMA_PATH) -> None:
    path = Path(schema_path).resolve()
    schema = _load_json(path, path.parent)
    if not isinstance(schema, dict):
        raise ExportInputError(f"viewer v1 JSON Schema must be an object: {path}")
    _validate_schema_node(bundle, schema, schema, "$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_games(experiment_root: str | Path) -> tuple[str, ...]:
    root = Path(experiment_root).resolve()
    games_root = _require_directory(root, "games")
    game_ids = tuple(sorted(path.name for path in games_root.iterdir() if path.is_dir() and not path.name.startswith(".")))
    if not game_ids:
        raise ExportInputError("completed experiment has no game directories under games/")
    return game_ids


def inspect_experiment(experiment_root: str | Path) -> ExperimentInspection:
    root = Path(experiment_root).resolve()
    if not root.is_dir():
        raise ExportInputError(f"experiment root does not exist or is not a directory: {root}")
    manifest = _load_json(_require_file(root, "manifest.json"), root)
    population = _load_json(_require_file(root, "population_manifest.json"), root)
    summary = _load_json(_require_file(root, "run_summary.json"), root)
    cost = _load_json(_require_file(root, "metrics/cost_summary.json"), root)
    for label, payload in (("manifest.json", manifest), ("population_manifest.json", population),
                           ("run_summary.json", summary), ("metrics/cost_summary.json", cost)):
        _require_raw_version(payload, label)
    experiment_id = manifest.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ExportInputError("manifest.json must contain a non-empty experiment_id")
    if population.get("experiment_id") != experiment_id or summary.get("experiment_id") != experiment_id:
        raise ExportInputError("experiment_id differs across manifest.json, population_manifest.json, and run_summary.json")
    if summary.get("status") in {"failed", "stopped", "incomplete"}:
        raise ExportInputError("run_summary.json records an incomplete or stopped experiment")
    game_ids = discover_games(root)
    completed = summary.get("games_completed")
    if not isinstance(completed, int) or completed < len(game_ids):
        raise ExportInputError(
            f"run_summary.json records {completed!r} completed games but {len(game_ids)} game directories were discovered"
        )
    for game_id in game_ids:
        _game_result(root / "games" / game_id, root)
    return ExperimentInspection(root, experiment_id, game_ids, manifest, population, summary, cost)


def _phase_parts(phase_id: str) -> tuple[int, str, str]:
    match = PHASE_PATTERN.fullmatch(phase_id)
    if not match:
        raise ExportInputError(f"unsupported phase id {phase_id!r}; expected YYYY_season_kind")
    return int(match.group("year")), match.group("season"), match.group("kind")


def _phase_key(phase_id: str) -> tuple[int, int, str]:
    year, season, kind = _phase_parts(phase_id)
    try:
        rank = PHASE_RANK[(season, kind)]
    except KeyError as error:
        raise ExportInputError(f"unsupported phase ordering for {phase_id!r}") from error
    return year, rank, phase_id


def _board(snapshot: Mapping[str, Any], label: str) -> dict[str, Any]:
    payload = snapshot.get("payload")
    if not isinstance(payload, dict):
        raise ExportInputError(f"{label} must contain an object payload")
    missing = [key for key in ("supply_ownership", "units") if key not in payload]
    if missing:
        raise ExportInputError(f"{label} payload is missing {', '.join(missing)}")
    return {
        "supply_ownership": payload["supply_ownership"],
        "units": payload["units"],
        "standoff_provinces": payload.get("standoff_provinces", []),
    }


def _source(
    index: dict[str, dict[str, Any]],
    path: Path,
    experiment_root: Path,
    kind: str,
    visibility: str,
    pointer: str | None = None,
) -> str:
    if not path.is_file():
        raise ExportInputError(f"evidence artifact is missing: {_relative_label(path, experiment_root)}")
    try:
        relative = path.resolve().relative_to(experiment_root).as_posix()
    except ValueError as error:
        raise ExportInputError(f"evidence artifact escapes experiment root: {path}") from error
    identity = relative + (pointer or "")
    source_id = f"source_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    value = {
        "source_id": source_id,
        "artifact_path": relative,
        "sha256": _sha256(path),
        "visibility": visibility,
        "kind": kind,
    }
    if pointer:
        value["json_pointer"] = pointer
    existing = index.get(source_id)
    if existing is not None and existing != value:
        raise ExportInputError(f"source ID collision for {relative}")
    index[source_id] = value
    return source_id


def _normalized_outcome(
    raw: Mapping[str, Any],
    final_year: int,
    scheduled_max_year: int,
    final_phase: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    countries = raw.get("country_results", {})
    if not isinstance(countries, dict):
        countries = {}
    leaders = sorted(
        (
            {"agent_id": value["agent_id"], "power": power, "supply_centers": value["owned_centers"]}
            for power, value in countries.items()
            if isinstance(value, dict)
            and isinstance(value.get("agent_id"), str)
            and isinstance(value.get("owned_centers"), int)
            and not isinstance(value.get("owned_centers"), bool)
        ),
        key=lambda item: (-item["supply_centers"], item["power"]),
    )
    leader = leaders[0] if leaders else None
    recorded_result = raw.get("result")
    solo = None
    if recorded_result == "solo_victory":
        claimed = raw.get("solo_victor")
        final_phase_id = raw.get("final_phase_id")
        board = final_phase.get("board_after") if isinstance(final_phase, Mapping) else None
        ownership = board.get("supply_ownership") if isinstance(board, Mapping) else None
        if not isinstance(claimed, Mapping) or not isinstance(ownership, Mapping):
            raise ExportInputError("solo victory lacks final Fall phase and board ownership evidence")
        power = claimed.get("power")
        agent_id = claimed.get("agent_id")
        centers = claimed.get("owned_centers")
        phase_id = final_phase.get("phase_id")
        board_after = final_phase.get("board_after") if isinstance(final_phase, Mapping) else None
        units = board_after.get("units", []) if isinstance(board_after, Mapping) else []
        adjudication = final_phase.get("adjudication") if isinstance(final_phase, Mapping) else None
        resolved = adjudication.get("output_state") if isinstance(adjudication, Mapping) else None
        resolved_ownership = resolved.get("supply_ownership") if isinstance(resolved, Mapping) else None
        resolved_units = resolved.get("units") if isinstance(resolved, Mapping) else None
        owners = list(ownership.values())
        owners_are_valid = all(owner is None or isinstance(owner, str) for owner in owners)
        owner_counts: dict[str, int] = {}
        if owners_are_valid:
            for owner in owners:
                if owner is None:
                    continue
                owner_counts[owner] = owner_counts.get(owner, 0) + 1
        board_winners = sorted(owner for owner, count in owner_counts.items() if count >= 18)
        country_counts_match = isinstance(countries, dict) and all(
            isinstance(power, str)
            and isinstance(value, Mapping)
            and isinstance(value.get("agent_id"), str)
            and isinstance(value.get("owned_centers"), int)
            and not isinstance(value.get("owned_centers"), bool)
            and value.get("owned_centers") == owner_counts.get(power, 0)
            for power, value in countries.items()
        ) and set(owner_counts).issubset(countries)
        if (
            raw.get("partial") or raw.get("result") != "solo_victory"
            or not isinstance(power, str) or not isinstance(agent_id, str)
            or not owners_are_valid
            or not isinstance(units, list)
            or not isinstance(resolved, Mapping)
            or not isinstance(resolved_ownership, Mapping)
            or dict(resolved_ownership) != dict(ownership)
            or not isinstance(resolved_units, list)
            or resolved_units != units
            or not isinstance(centers, int) or isinstance(centers, bool) or centers < 18
            or phase_id != final_phase_id
            or not isinstance(phase_id, str) or phase_id not in {f"{final_year}_fall_movement", f"{final_year}_fall_retreat"}
            or not phase_id.endswith(("_fall_movement", "_fall_retreat"))
            or board_winners != [power]
            or sum(owner == power for owner in ownership.values()) != centers
            or any(isinstance(unit, Mapping) and (unit.get("must_retreat") or unit.get("status") == "dislodged") for unit in units)
            or not country_counts_match
            or not isinstance(countries.get(power), Mapping)
            or countries[power].get("agent_id") != agent_id
            or countries[power].get("owned_centers") != centers
            or countries[power].get("result") != "win"
            or raw.get("winners") != [agent_id]
            or sum(isinstance(row, Mapping) and row.get("result") == "win" for row in countries.values()) != 1
        ):
            raise ExportInputError("solo victory metadata conflicts with the final Fall board or game result")
        solo = {"agent_id": agent_id, "power": power, "supply_centers": centers}
    if raw.get("partial") or recorded_result in {"stopped", "failed", "incomplete"}:
        termination = {"kind": "other_recorded_reason", "label": "Incomplete or stopped game", "final_year": final_year}
        solo = None
    elif recorded_result == "draw":
        termination = {"kind": "draw", "label": "Recorded draw", "final_year": final_year}
    elif solo:
        termination = {
            "kind": "solo_victory",
            "label": f"Solo victory by {solo['power'].title()} ({solo['supply_centers']} supply centers)",
            "final_year": final_year,
            "final_phase_id": raw["final_phase_id"],
        }
    elif recorded_result == "solo_victory":
        termination = {
            "kind": "other_recorded_reason",
            "label": "Recorded solo-victory result conflicts with final center counts",
            "final_year": final_year,
        }
    elif recorded_result == "completed" and not raw.get("partial") and final_year >= scheduled_max_year:
        termination = {
            "kind": "scheduled_max_year",
            "label": f"Leader at the scheduled {scheduled_max_year} cutoff",
            "final_year": final_year,
        }
    else:
        termination = {
            "kind": "other_recorded_reason",
            "label": "Recorded end reason is not sufficient to classify a solo victory or draw",
            "final_year": final_year,
        }
    return {"termination": termination, "leaders": [leader] if leader else [], "solo_victor": solo}


def _game_result(game_root: Path, experiment_root: Path) -> tuple[dict[str, Any], Path, int]:
    event_path = _require_file(experiment_root, f"games/{game_root.name}/logs/events.jsonl")
    result: dict[str, Any] | None = None
    result_line_number = 0
    for line_number, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ExportInputError(f"invalid JSONL at {_relative_label(event_path, experiment_root)} line {line_number}: {error}") from error
        _require_raw_version(event, f"{_relative_label(event_path, experiment_root)} line {line_number}")
        candidate = event.get("payload", {}).get("result") if event.get("phase_id") == "game_end" else None
        if isinstance(candidate, dict) and isinstance(candidate.get("country_results"), dict):
            result = candidate
            result_line_number = line_number
    if result is None:
        raise ExportInputError(f"games/{game_root.name}/logs/events.jsonl has no completed game_end result")
    if result.get("partial") or result.get("result") not in {"completed", "solo_victory", "draw"}:
        raise ExportInputError(f"games/{game_root.name} is stopped, incomplete, or has unknown completion evidence")
    return result, event_path, result_line_number


def _snapshots(game_root: Path, experiment_root: Path) -> tuple[dict[str, tuple[Path, dict[str, Any]]], dict[str, Any]]:
    snapshots_root = _require_directory(experiment_root, f"games/{game_root.name}/state/snapshots")
    snapshots: dict[str, tuple[Path, dict[str, Any]]] = {}
    initial: dict[str, Any] | None = None
    for path in sorted(snapshots_root.glob("*.json")):
        key = path.stem.removesuffix("_state")
        payload = _load_raw_json(path, experiment_root)
        if key == "initial":
            initial = payload
        elif PHASE_PATTERN.fullmatch(key):
            snapshots[key] = (path, payload)
    if initial is None:
        raise ExportInputError(f"games/{game_root.name}/state/snapshots must contain initial.json or initial_state.json")
    if not snapshots:
        raise ExportInputError(f"games/{game_root.name}/state/snapshots contains no completed phase snapshots")
    return snapshots, initial


def _load_messages(
    game_root: Path,
    experiment_root: Path,
    evidence: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    scoped_messages: dict[tuple[str, str, int], dict[str, Any]] = {}
    by_phase: dict[str, list[str]] = defaultdict(list)

    def add(message: dict[str, Any]) -> None:
        message_id = message["message_id"]
        scope = (message_id, message["phase_id"], message["chronological_position"])
        existing = scoped_messages.get(scope)
        if existing is not None:
            comparable_existing = {key: value for key, value in existing.items() if key != "source_ref"}
            comparable_message = {key: value for key, value in message.items() if key != "source_ref"}
            if comparable_existing != comparable_message:
                raise ExportInputError(f"conflicting duplicate message_id {message_id!r}")
            return
        scoped_messages[scope] = message

    private_root = game_root / "messages" / "private"
    for path in sorted(private_root.glob("*/*.jsonl")) if private_root.is_dir() else ():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                _require_raw_version(raw, f"{_relative_label(path, experiment_root)} line {line_number}")
                recipient = raw["to"]["to_agent_id"]
                sender = raw["from_agent_id"]
                source_ref = _source(evidence, path, experiment_root, "message", "private", f"/line/{line_number}")
                add(
                    {
                        "message_id": raw["message_id"],
                        "phase_id": raw["phase_id"],
                        "chronological_position": raw.get("tick", 0),
                        "sender_agent_id": sender,
                        "recipient_agent_ids": [recipient],
                        "channel": "private",
                        "thread_id": "__".join(sorted((sender, recipient))),
                        "text": raw["body"],
                        "visibility": "private",
                        "source_ref": source_ref,
                    }
                )
            except (KeyError, TypeError, json.JSONDecodeError) as error:
                raise ExportInputError(
                    f"invalid private message at {_relative_label(path, experiment_root)} line {line_number}: {error}"
                ) from error
    global_path = game_root / "messages" / "global" / "global.jsonl"
    if global_path.is_file():
        for line_number, line in enumerate(global_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                _require_raw_version(raw, f"{_relative_label(global_path, experiment_root)} line {line_number}")
                source_ref = _source(evidence, global_path, experiment_root, "message", "public", f"/line/{line_number}")
                add(
                    {
                        "message_id": raw["message_id"],
                        "phase_id": raw["phase_id"],
                        "chronological_position": raw.get("tick", 0),
                        "sender_agent_id": raw["from_agent_id"],
                        "recipient_agent_ids": [],
                        "channel": "global",
                        "thread_id": "global",
                        "text": raw["body"],
                        "visibility": "public",
                        "source_ref": source_ref,
                    }
                )
            except (KeyError, TypeError, json.JSONDecodeError) as error:
                raise ExportInputError(
                    f"invalid global message at {_relative_label(global_path, experiment_root)} line {line_number}: {error}"
                ) from error
    id_counts: dict[str, int] = defaultdict(int)
    for raw_id, _, _ in scoped_messages:
        id_counts[raw_id] += 1
    messages: dict[str, dict[str, Any]] = {}
    for (raw_id, phase_id, tick), message in scoped_messages.items():
        message_id = raw_id if id_counts[raw_id] == 1 else f"{raw_id}:phase:{phase_id}:tick:{tick}"
        if message_id in messages or (message_id != raw_id and message_id in id_counts):
            raise ExportInputError(f"qualified message_id collision {message_id!r}")
        message["message_id"] = message_id
        messages[message_id] = message
        by_phase[phase_id].append(message_id)
    for phase_ids in by_phase.values():
        phase_ids.sort(key=lambda message_id: (messages[message_id]["chronological_position"], message_id))
    return messages, by_phase


def _build_roster(
    game_root: Path,
    experiment_root: Path,
    population_manifest: Mapping[str, Any],
    game_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    population = {
        item.get("agent_id"): item
        for item in population_manifest.get("agents", [])
        if isinstance(item, dict) and isinstance(item.get("agent_id"), str)
    }
    assignments: dict[str, list[str]] = defaultdict(list)
    for power, value in game_result.get("country_results", {}).items():
        if isinstance(value, dict) and isinstance(value.get("agent_id"), str):
            assignments[value["agent_id"]].append(power)
    agents_root = _require_directory(experiment_root, f"games/{game_root.name}/agents")
    roster: list[dict[str, Any]] = []
    for agent_root in sorted((path for path in agents_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        agent_id = agent_root.name
        powers = sorted(set(assignments.get(agent_id, ())))
        if not powers:
            raise ExportInputError(f"completed result has no power assignment for agent {agent_id!r} in {game_root.name}")
        if agent_id not in population:
            raise ExportInputError(f"population_manifest.json has no agent {agent_id!r}")
        profile_path = _require_file(
            experiment_root,
            f"games/{game_root.name}/agents/{agent_id}/folio/manifest.json",
        )
        profile = _load_json(profile_path, experiment_root)
        person = population[agent_id]
        for power in powers:
            roster.append(
                {
                    "agent_id": agent_id,
                    "display_name": profile.get("display_name", person.get("display_name", agent_id)),
                    "profile_version": profile.get("schema_version", "unknown"),
                    "power": power,
                    "power_assignment_history": [{"game_id": game_root.name, "power": power}],
                    "runtime": person.get("runtime", {}),
                    "runtime_kind": person.get("runtime_kind"),
                    "blueprint_schema_version": person.get("blueprint_schema_version"),
                    "blueprint_sha256": person.get("blueprint_sha256"),
                    "runtime_profile": profile.get("runtime", {}).get("profile")
                    if isinstance(profile.get("runtime"), dict)
                    else None,
                }
            )
    return roster


def _load_terminal_private_records(
    game_root: Path,
    experiment_root: Path,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Preserve structured end-of-game memory without reading runtime transcripts."""
    records: dict[str, dict[str, Any]] = {}
    agents_root = _require_directory(experiment_root, f"games/{game_root.name}/agents")
    for agent_root in sorted((path for path in agents_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        agent_id = agent_root.name
        memory_path = agent_root / "folio" / "game_memory" / f"{game_root.name}.json"
        if memory_path.is_file():
            record_id = f"{game_root.name}:{agent_id}:game_memory"
            records[record_id] = {
                "record_id": record_id,
                "agent_id": agent_id,
                "record_type": "game_memory",
                "visibility": "private",
                "payload": _load_json(memory_path, experiment_root),
                "source_ref": _source(evidence, memory_path, experiment_root, "private_record", "private"),
            }
        persistent_path = agent_root / "folio" / "persistent_memory.md"
        if persistent_path.is_file():
            record_id = f"{game_root.name}:{agent_id}:persistent_memory"
            records[record_id] = {
                "record_id": record_id,
                "agent_id": agent_id,
                "record_type": "persistent_memory",
                "visibility": "private",
                "payload": {"text": persistent_path.read_text(encoding="utf-8")},
                "source_ref": _source(evidence, persistent_path, experiment_root, "private_record", "private"),
            }
    return records


def _load_postgame_records(
    game_root: Path,
    experiment_root: Path,
    evidence: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index safe, structured observatory products; omit provider/runtime diagnostics."""
    relative_records = {
        "board_evolution": "observatory/board_evolution.json",
        "diplomacy_metrics": "observatory/diplomacy_metrics.json",
        "reporter_summary": "observatory/reports/reporter_summary.json",
    }
    records: dict[str, dict[str, Any]] = {}
    for record_id, relative in sorted(relative_records.items()):
        path = game_root / relative
        if not path.is_file():
            continue
        records[record_id] = {
            "record_id": record_id,
            "visibility": "omniscient_postgame",
            "payload": _load_json(path, experiment_root),
            "source_ref": _source(evidence, path, experiment_root, "report", "omniscient_postgame"),
        }
    return records


def _build_bundle(inspection: ExperimentInspection, game_id: str) -> dict[str, Any]:
    root = inspection.root
    game_root = root / "games" / game_id
    if not game_root.is_dir():
        raise ExportInputError(f"unknown game {game_id!r}; discovered games: {', '.join(inspection.game_ids)}")
    snapshots, initial = _snapshots(game_root, root)
    history_path = _require_file(root, f"games/{game_id}/reporter/secret_history.json")
    history = _load_raw_json(history_path, root)
    chronology = {
        entry.get("phase_id"): entry
        for entry in history.get("chronology", [])
        if isinstance(entry, dict) and isinstance(entry.get("phase_id"), str)
    }
    game_result, game_result_path, game_result_line = _game_result(game_root, root)
    evidence: dict[str, dict[str, Any]] = {}
    provenance_refs = {
        "manifest": _source(evidence, root / "manifest.json", root, "manifest", "public"),
        "population_manifest": _source(evidence, root / "population_manifest.json", root, "manifest", "public"),
        "run_summary": _source(evidence, root / "run_summary.json", root, "manifest", "public"),
        "cost_summary": _source(evidence, root / "metrics" / "cost_summary.json", root, "manifest", "public"),
        "game_end": _source(
            evidence,
            game_result_path,
            root,
            "report",
            "public",
            f"/line/{game_result_line}/payload/result",
        ),
    }
    messages, messages_by_phase = _load_messages(game_root, root, evidence)
    reporter_source = _source(evidence, history_path, root, "report", "omniscient_postgame")
    orders: dict[str, dict[str, Any]] = {}
    order_occurrences: dict[str, int] = defaultdict(int)
    private_records: dict[str, dict[str, Any]] = {}
    phases: list[dict[str, Any]] = []
    previous = initial
    for phase_id in sorted(snapshots, key=_phase_key):
        year, season, kind = _phase_parts(phase_id)
        snapshot_path, snapshot = snapshots[phase_id]
        phase_root = game_root / "phases" / phase_id
        submitted_path = _require_file(root, f"games/{game_id}/phases/{phase_id}/orders/submitted_orders.json")
        resolution_path = _require_file(root, f"games/{game_id}/phases/{phase_id}/resolution/resolution.json")
        submitted = _load_raw_json(submitted_path, root)
        resolution = _load_raw_json(resolution_path, root)
        if not isinstance(submitted.get("orders"), list):
            raise ExportInputError(f"games/{game_id}/phases/{phase_id}/orders/submitted_orders.json must contain orders[]")
        snapshot_source = _source(evidence, snapshot_path, root, "board_snapshot", "public", "/payload")
        order_source = _source(evidence, submitted_path, root, "orders", "public")
        resolution_source = _source(evidence, resolution_path, root, "adjudication", "public")
        order_ids: list[str] = []
        public_orders = snapshot.get("payload", {}).get("latest_public_resolution", {}).get("public_final_orders", [])
        if not isinstance(public_orders, list):
            raise ExportInputError(f"snapshot {snapshot_path.name} latest_public_resolution.public_final_orders must be an array")
        for order_index, item in enumerate(public_orders):
            if not isinstance(item, dict) or not isinstance(item.get("unit_id"), str):
                raise ExportInputError(f"snapshot {snapshot_path.name} contains a public order without unit_id")
            base_order_id = f"{phase_id}:{item['unit_id']}"
            order_occurrences[base_order_id] += 1
            occurrence = order_occurrences[base_order_id]
            order_id = base_order_id if occurrence == 1 else f"{base_order_id}:{occurrence}"
            final_order_source = _source(
                evidence,
                snapshot_path,
                root,
                "orders",
                "public",
                f"/payload/latest_public_resolution/public_final_orders/{order_index}",
            )
            orders[order_id] = {
                **item,
                "source_ref": final_order_source,
                "submitted_source_ref": order_source,
                "adjudication_source_ref": resolution_source,
            }
            order_ids.append(order_id)
        record_ids: list[str] = []
        reflection_root = phase_root / "reflection"
        for reflection_path in sorted(reflection_root.glob("*.json")) if reflection_root.is_dir() else ():
            agent_id = reflection_path.stem
            record_id = f"{phase_id}:{agent_id}:reflection"
            private_records[record_id] = {
                "record_id": record_id,
                "agent_id": agent_id,
                "record_type": "reflection",
                "visibility": "private",
                "payload": _load_json(reflection_path, root),
                "source_ref": _source(evidence, reflection_path, root, "private_record", "private"),
            }
            record_ids.append(record_id)
        negotiation_phase = f"{year}_{season}_negotiation"
        phase_message_ids = list(messages_by_phase.get(negotiation_phase, ())) + list(messages_by_phase.get(phase_id, ()))
        entry = chronology.get(phase_id, {})
        phases.append(
            {
                "phase_id": phase_id,
                "kind": kind,
                "year": year,
                "season": season,
                "board_before": _board(previous, f"previous snapshot before {phase_id}"),
                "board_after": _board(snapshot, f"snapshot {phase_id}"),
                "messages": phase_message_ids,
                "orders": order_ids,
                "adjudication": resolution,
                "private_records": record_ids,
                "narrative": {
                    "overview": entry.get("summary", "No postgame narrative available for this phase."),
                    "theater_sections": [],
                    "aftermath": "",
                    "source_refs": [reporter_source],
                },
                "source_refs": [snapshot_source, order_source, resolution_source, reporter_source],
                "extensions": {
                    "submitted_order_count": len(submitted["orders"]),
                    "negotiation_context_phase": negotiation_phase,
                },
            }
        )
        previous = snapshot
    terminal_records = _load_terminal_private_records(game_root, root, evidence)
    private_records.update(terminal_records)
    phases[-1]["private_records"].extend(sorted(terminal_records))
    postgame_records = _load_postgame_records(game_root, root, evidence)
    years = [
        {"year": year, "phases": [phase for phase in phases if phase["year"] == year]}
        for year in sorted({phase["year"] for phase in phases})
    ]
    final_year = years[-1]["year"]
    roster = _build_roster(game_root, root, inspection.population_manifest, game_result)
    max_years = inspection.manifest.get("max_years", 10)
    if not isinstance(max_years, int) or isinstance(max_years, bool) or max_years < 1:
        raise ExportInputError("manifest.json max_years must be a positive integer when present")
    scheduled_max_year = 1900 + max_years
    run_summary = dict(inspection.run_summary)
    for field in ("cost_summary_path", "leaderboard_by_country_path", "leaderboard_overall_path"):
        value = run_summary.get(field)
        if isinstance(value, str) and Path(value).is_absolute():
            try:
                run_summary[field] = Path(value).resolve().relative_to(root).as_posix()
            except ValueError as error:
                raise ExportInputError(f"run_summary {field} escapes experiment root") from error
    bundle = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "bundle_id": f"{inspection.experiment_id}:{game_id}:v1",
        "source": {
            "experiment_id": inspection.experiment_id,
            "game_id": game_id,
            "artifact_manifest_sha256": _sha256(root / "manifest.json"),
            "visibility_mode": AUDIENCE,
        },
        "map": {"map_id": "standard_v1", "geometry_version": "standard_geometry_v1"},
        "game": {
            "title": f"{inspection.experiment_id} / {game_id}",
            "years_played": [years[0]["year"], final_year],
            "stop_condition": game_result.get("result", "completed"),
            "outcome": _normalized_outcome(game_result, final_year, scheduled_max_year, phases[-1]),
            "model_provenance": inspection.population_manifest.get("model_provider"),
            "per_player_model_provenance": inspection.population_manifest.get("providers_by_agent"),
            "cost_and_call_metadata": inspection.cost_summary,
            "provenance": {
                "raw_outcome": game_result,
                "experiment_manifest": "manifest.json",
                "run_summary": run_summary,
                "source_refs": provenance_refs,
            },
        },
        "roster": roster,
        "summary": {"chronology_source_ref": reporter_source},
        "strategic_arcs": [],
        "years": years,
        "messages": messages,
        "orders": orders,
        "private_records": private_records,
        "evidence_index": evidence,
        "extensions": {
            "batch_b": {},
            "normalization": {
                "generator": "llm_diplomacy.reporting.bundle_exporter",
                "raw_layout_version": RAW_LAYOUT_VERSION,
            },
            "postgame_records": postgame_records,
        },
    }
    _validate_bundle(bundle)
    serialized = json.dumps(bundle, sort_keys=True, ensure_ascii=False).replace("\\\\", "/").lower()
    if root.as_posix().lower() in serialized:
        raise ExportInputError("exported bundle contains an absolute experiment-root path")
    return bundle


def _validate_bundle(bundle: Mapping[str, Any]) -> None:
    validate_report_schema(bundle)
    required = {
        "schema_version", "bundle_id", "source", "map", "game", "roster", "summary",
        "strategic_arcs", "years", "messages", "orders", "private_records", "evidence_index", "extensions",
    }
    missing = sorted(required.difference(bundle))
    if missing:
        raise ExportInputError(f"generated bundle is missing required fields: {', '.join(missing)}")
    if bundle.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ExportInputError(f"generated bundle has unsupported schema_version {bundle.get('schema_version')!r}")
    if bundle.get("source", {}).get("visibility_mode") != AUDIENCE:
        raise ExportInputError("Phase 4A exporter only supports omniscient_postgame bundles")
    sources = bundle.get("evidence_index", {})
    messages = bundle.get("messages", {})
    orders = bundle.get("orders", {})
    records = bundle.get("private_records", {})
    if not all(isinstance(value, dict) for value in (sources, messages, orders, records)):
        raise ExportInputError("generated evidence collections must be objects")
    for source_id, source in sources.items():
        if source.get("source_id") != source_id:
            raise ExportInputError(f"evidence key {source_id!r} differs from source_id")
        artifact = source.get("artifact_path")
        if not isinstance(artifact, str) or Path(artifact).is_absolute() or ".." in Path(artifact).parts:
            raise ExportInputError(f"evidence {source_id!r} has a non-portable artifact_path")
    for message_id, message in messages.items():
        if message.get("message_id") != message_id or message.get("source_ref") not in sources:
            raise ExportInputError(f"message {message_id!r} has invalid identity or source_ref")
    for order_id, order in orders.items():
        if order.get("source_ref") not in sources:
            raise ExportInputError(f"order {order_id!r} has an unresolved source_ref")
        for field in ("submitted_source_ref", "adjudication_source_ref"):
            if field in order and order[field] not in sources:
                raise ExportInputError(f"order {order_id!r} has an unresolved {field}")
    for record_id, record in records.items():
        if record.get("record_id") != record_id or record.get("source_ref") not in sources:
            raise ExportInputError(f"private record {record_id!r} has invalid identity or source_ref")
        if sources[record["source_ref"]].get("visibility") != "private":
            raise ExportInputError(f"private record {record_id!r} does not cite private evidence")
    postgame_records = bundle.get("extensions", {}).get("postgame_records", {})
    if not isinstance(postgame_records, dict):
        raise ExportInputError("extensions.postgame_records must be an object")
    for record_id, record in postgame_records.items():
        if not isinstance(record, dict) or record.get("record_id") != record_id:
            raise ExportInputError(f"postgame record {record_id!r} has an invalid identity")
        source_ref = record.get("source_ref")
        if source_ref not in sources or sources[source_ref].get("visibility") != "omniscient_postgame":
            raise ExportInputError(f"postgame record {record_id!r} has invalid evidence visibility")
    previous_key: tuple[int, int, str] | None = None
    for year in bundle.get("years", []):
        for phase in year.get("phases", []):
            key = _phase_key(phase.get("phase_id", ""))
            if previous_key is not None and key <= previous_key:
                raise ExportInputError("generated phases are not in strict chronological order")
            previous_key = key
            if phase.get("year") != year.get("year"):
                raise ExportInputError(f"phase {phase.get('phase_id')!r} year differs from its parent year")
            for source_id in [*phase.get("source_refs", []), *phase.get("narrative", {}).get("source_refs", [])]:
                if source_id not in sources:
                    raise ExportInputError(f"phase {phase.get('phase_id')!r} has unresolved source {source_id!r}")
            for message_id in phase.get("messages", []):
                if message_id not in messages:
                    raise ExportInputError(f"phase {phase.get('phase_id')!r} has unresolved message {message_id!r}")
            for order_id in phase.get("orders", []):
                if order_id not in orders:
                    raise ExportInputError(f"phase {phase.get('phase_id')!r} has unresolved order {order_id!r}")
            for record_id in phase.get("private_records", []):
                if record_id not in records:
                    raise ExportInputError(f"phase {phase.get('phase_id')!r} has unresolved private record {record_id!r}")


def audit_exported_evidence(report_path: str | Path, experiment_root: str | Path) -> None:
    report = Path(report_path).resolve()
    root = Path(experiment_root).resolve()
    bundle = _load_json(report, report.parent)
    _validate_bundle(bundle)
    for source_id, source in bundle["evidence_index"].items():
        artifact = source["artifact_path"]
        candidate = (root / artifact).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ExportInputError(f"evidence {source_id} escapes experiment root: {artifact}") from error
        if not candidate.is_file():
            raise ExportInputError(f"evidence {source_id} is missing artifact {artifact}")
        if _sha256(candidate) != source["sha256"]:
            raise ExportInputError(f"evidence {source_id} hash mismatch for {artifact}")


def _normalize_game_ids(inspection: ExperimentInspection, game_ids: Iterable[str] | None) -> tuple[str, ...]:
    selected = tuple(sorted(set(game_ids))) if game_ids is not None else inspection.game_ids
    if not selected:
        raise ExportInputError("at least one game must be selected")
    unknown = sorted(set(selected).difference(inspection.game_ids))
    if unknown:
        raise ExportInputError(
            f"unknown game(s): {', '.join(unknown)}; discovered games: {', '.join(inspection.game_ids)}"
        )
    return selected


def export_experiment(
    experiment_root: str | Path,
    output_root: str | Path,
    *,
    game_ids: Iterable[str] | None = None,
    audience: str = AUDIENCE,
) -> tuple[Path, ...]:
    if audience != AUDIENCE:
        raise ExportInputError(f"Phase 4A supports only audience {AUDIENCE!r}")
    inspection = inspect_experiment(experiment_root)
    selected = _normalize_game_ids(inspection, game_ids)
    destination = Path(output_root).resolve()
    if destination == inspection.root or inspection.root in destination.parents:
        raise ExportInputError("output root must be outside the raw experiment directory")
    if destination.exists():
        raise ExportInputError(f"output root already exists; refusing to overwrite: {destination}")
    output_paths = [
        destination / "reports" / inspection.experiment_id / game_id / "omniscient" / "report.json"
        for game_id in selected
    ]
    bundles = [(game_id, _build_bundle(inspection, game_id)) for game_id in selected]
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}.report-export-", dir=destination.parent))
    try:
        for (game_id, bundle), output in zip(bundles, output_paths, strict=True):
            staged = staging_root / "reports" / inspection.experiment_id / game_id / "omniscient" / "report.json"
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(
                json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            audit_exported_evidence(staged, inspection.root)
        if destination.exists():
            raise ExportInputError(f"output root already exists; refusing to overwrite: {destination}")
        staging_root.rename(destination)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return tuple(output_paths)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export-report-bundle",
        description="Export completed games to validated Diplomacy Viewer v1 factual bundles.",
    )
    parser.add_argument("--experiment-root", required=True, help="Read-only completed experiment root.")
    parser.add_argument("--output-root", required=True, help="Explicit destination outside the experiment root.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--game", action="append", dest="games", help="Game ID to export; repeat as needed.")
    selection.add_argument("--all-games", action="store_true", help="Export every discovered game directory.")
    parser.add_argument("--audience", required=True, choices=(AUDIENCE,))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        paths = export_experiment(
            args.experiment_root,
            args.output_root,
            game_ids=None if args.all_games else args.games,
            audience=args.audience,
        )
    except ExportInputError as error:
        print(f"export-report-bundle: error: {error}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
