"""Deterministic audience projections for Viewer Report Bundle v1 exports."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .bundle_exporter import REPORT_SCHEMA_VERSION, validate_report_schema


AUDIENCE_SCHEMA_VERSION = "diplomacy_report_audience_v1"
AUDIENCE_POLICY_VERSION = "diplomacy_audience_policy_v1"
AUDIENCE_MODES = ("omniscient_research", "postgame_public")
RELEASE_CONTEXT = "postgame_released_negotiation_transcript"
AUDIENCE_DESCRIPTIONS = {
    "omniscient_research": (
        "Research-only postgame bundle containing private cognition and other restricted records; "
        "not approved for static public distribution."
    ),
    "postgame_public": (
        "Postgame-shareable account containing public game facts and a postgame-released "
        "negotiation transcript; the transcript was not public during play."
    ),
}
CLASSIFIED_PRIVATE_RECORD_TYPES = frozenset(
    {
        "reflection",
        "belief",
        "belief_summary",
        "campaign_plan",
        "commitment_memory",
        "game_memory",
        "persistent_memory",
        "outcome_review",
        "winter_assessment",
        "agent_folio",
        "raw_prompt_context",
    }
)
REFERENCE_FIELDS = frozenset(
    {"source_ref", "submitted_source_ref", "adjudication_source_ref", "chronology_source_ref"}
)
PRIVATE_PATH_FRAGMENTS = (
    "/messages/private/",
    "/reflection/",
    "/reflections/",
    "/belief",
    "/campaign_plan",
    "/commitment",
    "/persistent_memory",
    "/agent_folio",
    "/folios/",
    "/reporter/secret_history",
)
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class ProjectionError(ValueError):
    """The requested projection cannot be proven structurally safe."""


@dataclass(frozen=True)
class ProjectionResult:
    bundle: dict[str, Any]
    released_artifacts: Mapping[str, bytes]
    audit: dict[str, Any]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _audience_metadata(mode: str, source_bundle_id: str, source_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": AUDIENCE_SCHEMA_VERSION,
        "policy_version": AUDIENCE_POLICY_VERSION,
        "mode": mode,
        "reader_description": AUDIENCE_DESCRIPTIONS[mode],
        "approved_for_static_public_distribution": mode == "postgame_public",
        "projection": {
            "generator": "llm_diplomacy.reporting.report_projector",
            "source_bundle_id": source_bundle_id,
            "source_bundle_sha256": source_sha256,
        },
    }


def resolve_audience_metadata(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve explicit metadata, treating legacy bundles as unsafe research data."""
    extensions = bundle.get("extensions")
    raw = extensions.get("audience") if isinstance(extensions, dict) else None
    if raw is None:
        return {
            "schema_version": AUDIENCE_SCHEMA_VERSION,
            "policy_version": "legacy_unversioned",
            "mode": "omniscient_research",
            "reader_description": AUDIENCE_DESCRIPTIONS["omniscient_research"],
            "approved_for_static_public_distribution": False,
            "legacy_inference": True,
        }
    if not isinstance(raw, dict):
        raise ProjectionError("extensions.audience must be an object")
    mode = raw.get("mode")
    if mode not in AUDIENCE_MODES:
        raise ProjectionError(f"unsupported audience mode {mode!r}")
    required = (
        "schema_version",
        "policy_version",
        "reader_description",
        "approved_for_static_public_distribution",
        "projection",
    )
    missing = [field for field in required if field not in raw]
    if missing:
        raise ProjectionError(f"explicit audience metadata is missing {', '.join(missing)}")
    if raw["schema_version"] != AUDIENCE_SCHEMA_VERSION or raw["policy_version"] != AUDIENCE_POLICY_VERSION:
        raise ProjectionError("unsupported audience metadata or policy version")
    approved = raw["approved_for_static_public_distribution"]
    if not isinstance(approved, bool) or approved != (mode == "postgame_public"):
        raise ProjectionError("audience static-distribution approval contradicts its mode")
    if not isinstance(raw["reader_description"], str) or not raw["reader_description"].strip():
        raise ProjectionError("audience reader_description must be non-empty")
    projection = raw["projection"]
    if not isinstance(projection, dict) or not all(
        isinstance(projection.get(key), str) and projection[key]
        for key in ("generator", "source_bundle_id", "source_bundle_sha256")
    ):
        raise ProjectionError("audience projection provenance is incomplete")
    return {**copy.deepcopy(raw), "legacy_inference": False}


def _require_source_shape(bundle: Mapping[str, Any]) -> None:
    try:
        validate_report_schema(bundle)
    except Exception as error:
        raise ProjectionError(f"source bundle does not satisfy Viewer Report Bundle v1: {error}") from error
    if bundle.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ProjectionError(f"unsupported source bundle version {bundle.get('schema_version')!r}")
    if bundle.get("source", {}).get("visibility_mode") != "omniscient_postgame":
        raise ProjectionError("source must be a Phase 4A omniscient_postgame bundle")
    evidence = bundle.get("evidence_index", {})
    if not isinstance(evidence, dict):
        raise ProjectionError("source evidence_index must be an object")
    for source_id, item in evidence.items():
        if not isinstance(item, dict) or item.get("source_id") != source_id:
            raise ProjectionError(f"source evidence {source_id!r} has an invalid identity")
        path = item.get("artifact_path")
        if not isinstance(path, str) or not path or _is_absolute_string(path) or ".." in PurePosixPath(path.replace("\\", "/")).parts:
            raise ProjectionError(f"source evidence {source_id!r} has an unsafe artifact path")
    for collection_name in ("messages", "orders", "private_records"):
        collection = bundle.get(collection_name, {})
        if not isinstance(collection, dict):
            raise ProjectionError(f"source {collection_name} must be an object")
        for record_id, record in collection.items():
            if not isinstance(record, dict):
                raise ProjectionError(f"source {collection_name} record {record_id!r} must be an object")
            for field in REFERENCE_FIELDS:
                if field in record and record[field] not in evidence:
                    raise ProjectionError(f"source {collection_name} record {record_id!r} has unresolved {field}")
    for record_id, record in bundle.get("private_records", {}).items():
        record_type = record.get("record_type")
        if record_type not in CLASSIFIED_PRIVATE_RECORD_TYPES:
            raise ProjectionError(f"unclassified private record type {record_type!r} on {record_id!r}")


def _all_phases(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [phase for year in bundle.get("years", []) for phase in year.get("phases", [])]


def _referenced_evidence(bundle: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()

    def visit(value: Any, key: str | None = None) -> None:
        if key in REFERENCE_FIELDS and isinstance(value, str):
            refs.add(value)
        elif key == "source_refs" and isinstance(value, list):
            refs.update(item for item in value if isinstance(item, str))
        elif isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(bundle)
    return refs


def _is_absolute_string(value: str) -> bool:
    return bool(WINDOWS_ABSOLUTE.match(value)) or value.startswith("/") or value.startswith("file://")


def _contains_local_absolute_path(value: str) -> bool:
    return (
        bool(WINDOWS_ABSOLUTE.match(value))
        or value.startswith("\\\\")
        or value.startswith("file://")
        or bool(re.match(r"^/(?:Users|home|tmp|var)/", value, re.IGNORECASE))
    )


def _audit(source: Mapping[str, Any], projected: Mapping[str, Any], mode: str) -> dict[str, Any]:
    source_records = Counter(record.get("record_type", "unclassified") for record in source.get("private_records", {}).values())
    projected_records = Counter(record.get("record_type", "unclassified") for record in projected.get("private_records", {}).values())
    source_postgame = source.get("extensions", {}).get("postgame_records", {})
    projected_postgame = projected.get("extensions", {}).get("postgame_records", {})
    included_records = {
        "messages": len(projected.get("messages", {})),
        "orders": len(projected.get("orders", {})),
        "postgame_records": len(projected_postgame) if isinstance(projected_postgame, dict) else 0,
        **dict(sorted(projected_records.items())),
    }
    excluded_records = {
        "messages": len(source.get("messages", {})) - len(projected.get("messages", {})),
        "orders": len(source.get("orders", {})) - len(projected.get("orders", {})),
        "postgame_records": (len(source_postgame) if isinstance(source_postgame, dict) else 0)
        - (len(projected_postgame) if isinstance(projected_postgame, dict) else 0),
    }
    for record_type, count in sorted(source_records.items()):
        excluded_records[record_type] = count - projected_records.get(record_type, 0)

    source_evidence = Counter(item.get("visibility", "unclassified") for item in source.get("evidence_index", {}).values())
    projected_evidence = Counter(item.get("visibility", "unclassified") for item in projected.get("evidence_index", {}).values())
    refs = _referenced_evidence(projected)
    available = set(projected.get("evidence_index", {}))
    unresolved = sorted(refs.difference(available))

    disallowed: list[str] = []
    if mode == "postgame_public":
        if projected.get("private_records"):
            disallowed.append("private_records")
        text = json.dumps(projected, sort_keys=True, ensure_ascii=False)
        source_private_tokens: set[str] = set(source.get("private_records", {}))
        source_private_tokens.update(
            record.get("source_ref") for record in source.get("private_records", {}).values() if record.get("source_ref")
        )
        if isinstance(source_postgame, dict):
            source_private_tokens.update(source_postgame)
            source_private_tokens.update(
                record.get("source_ref") for record in source_postgame.values() if isinstance(record, dict) and record.get("source_ref")
            )
        for message in source.get("messages", {}).values():
            ref = message.get("source_ref")
            evidence = source.get("evidence_index", {}).get(ref, {})
            if evidence.get("visibility") == "private":
                source_private_tokens.update((ref, evidence.get("artifact_path")))
        for token in sorted(item for item in source_private_tokens if isinstance(item, str) and item):
            if token in text:
                disallowed.append(token)
        for source_id, evidence in projected.get("evidence_index", {}).items():
            visibility = evidence.get("visibility")
            path = str(evidence.get("artifact_path", "")).replace("\\", "/").lower()
            if visibility == "omniscient_postgame":
                disallowed.append(source_id)
            if visibility == "private" and evidence.get("release_context") != RELEASE_CONTEXT:
                disallowed.append(source_id)
            if any(fragment in f"/{path}" for fragment in PRIVATE_PATH_FRAGMENTS):
                disallowed.append(path)

    absolute: list[str] = []
    for source_id, evidence in projected.get("evidence_index", {}).items():
        path = evidence.get("artifact_path")
        if isinstance(path, str) and (_is_absolute_string(path) or ".." in PurePosixPath(path.replace("\\", "/")).parts):
            absolute.append(source_id)
    projection = projected.get("extensions", {}).get("audience", {}).get("projection", {})
    for key, value in projection.items():
        if isinstance(value, str) and _contains_local_absolute_path(value):
            absolute.append(f"projection.{key}")

    def find_absolute(value: Any, path: str = "$") -> None:
        if isinstance(value, str) and _contains_local_absolute_path(value):
            absolute.append(path)
        elif isinstance(value, dict):
            for key, child in value.items():
                find_absolute(child, f"{path}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                find_absolute(child, f"{path}/{index}")

    find_absolute(projected)

    return {
        "policy_version": AUDIENCE_POLICY_VERSION,
        "audience_mode": mode,
        "included_records_by_type": included_records,
        "excluded_records_by_type": excluded_records,
        "included_evidence_by_visibility": dict(sorted(projected_evidence.items())),
        "excluded_evidence_by_visibility": {
            key: max(0, count - projected_evidence.get(key, 0)) for key, count in sorted(source_evidence.items())
        },
        "unresolved_reference_count": len(unresolved),
        "unresolved_references": unresolved,
        "disallowed_private_reference_count": len(set(disallowed)),
        "disallowed_private_references": sorted(set(disallowed)),
        "absolute_path_count": len(set(absolute)),
        "absolute_paths": sorted(set(absolute)),
    }


def _safe_game(game: Mapping[str, Any]) -> dict[str, Any]:
    safe = {key: copy.deepcopy(game[key]) for key in ("title", "years_played", "stop_condition", "outcome") if key in game}
    source_refs = game.get("provenance", {}).get("source_refs", {}) if isinstance(game.get("provenance"), dict) else {}
    safe["provenance"] = {"source_refs": {"game_end": source_refs["game_end"]}} if "game_end" in source_refs else {"source_refs": {}}
    return safe


def _safe_roster(roster: Sequence[Any]) -> list[dict[str, Any]]:
    fields = ("agent_id", "display_name", "power", "power_assignment_history")
    return [
        {key: copy.deepcopy(item[key]) for key in fields if key in item}
        for item in roster
        if isinstance(item, dict)
    ]


def _build_transcript(bundle: Mapping[str, Any]) -> tuple[bytes, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    ordered = sorted(
        bundle.get("messages", {}).values(),
        key=lambda item: (str(item.get("phase_id", "")), int(item.get("chronological_position", 0)), str(item.get("message_id", ""))),
    )
    transcript_messages = [
        {
            "message_id": item["message_id"],
            "phase_id": item["phase_id"],
            "chronological_position": item["chronological_position"],
            "sender_agent_id": item["sender_agent_id"],
            "recipient_agent_ids": copy.deepcopy(item["recipient_agent_ids"]),
            "channel": item["channel"],
            "thread_id": item["thread_id"],
            "text": item["text"],
            "original_visibility": item["visibility"],
        }
        for item in ordered
    ]
    transcript = {
        "schema_version": "diplomacy_postgame_released_transcript_v1",
        "release_context": RELEASE_CONTEXT,
        "description": "Player-authored negotiations released after the game; not public during play.",
        "messages": transcript_messages,
    }
    artifact = _json_bytes(transcript)
    digest = _hash_bytes(artifact)
    game_id = str(bundle["source"]["game_id"])
    path = "released/negotiation_transcript.json"
    messages: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(ordered):
        message_id = item["message_id"]
        source_id = f"source_released_message_{hashlib.sha256(f'{game_id}:{message_id}'.encode()).hexdigest()[:20]}"
        projected = copy.deepcopy(item)
        projected["source_ref"] = source_id
        messages[message_id] = projected
        evidence[source_id] = {
            "source_id": source_id,
            "artifact_path": path,
            "json_pointer": f"/messages/{index}",
            "sha256": digest,
            "visibility": item["visibility"],
            "kind": "message",
            "release_context": RELEASE_CONTEXT,
        }
    return artifact, messages, evidence


def _project_public(bundle: Mapping[str, Any], metadata: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    source_evidence = bundle["evidence_index"]
    transcript, messages, message_evidence = _build_transcript(bundle)
    projected_orders = copy.deepcopy(bundle["orders"])

    required_public: set[str] = set()
    for order in projected_orders.values():
        required_public.update(order[field] for field in REFERENCE_FIELDS if field in order)
    game = _safe_game(bundle["game"])
    required_public.update(_referenced_evidence({"game": game}))

    years: list[dict[str, Any]] = []
    for year in bundle["years"]:
        projected_phases: list[dict[str, Any]] = []
        for phase in year["phases"]:
            public_refs = [
                source_id
                for source_id in phase.get("source_refs", [])
                if source_id in source_evidence and source_evidence[source_id].get("visibility") == "public"
            ]
            required_public.update(public_refs)
            phase_messages = [message_id for message_id in phase.get("messages", []) if message_id in messages]
            phase_orders = [order_id for order_id in phase.get("orders", []) if order_id in projected_orders]
            overview = (
                f"Postgame public record: {len(phase_orders)} adjudicated order(s) and "
                f"{len(phase_messages)} released negotiation message(s)."
            )
            projected_phases.append(
                {
                    "phase_id": phase["phase_id"],
                    "kind": phase["kind"],
                    "year": phase["year"],
                    "season": phase["season"],
                    "board_before": copy.deepcopy(phase["board_before"]),
                    "board_after": copy.deepcopy(phase["board_after"]),
                    "messages": phase_messages,
                    "orders": phase_orders,
                    "adjudication": copy.deepcopy(phase["adjudication"]),
                    "private_records": [],
                    "narrative": {
                        "overview": overview,
                        "theater_sections": [],
                        "aftermath": "",
                        "source_refs": public_refs,
                    },
                    "source_refs": public_refs,
                    "extensions": {
                        "submitted_order_count": phase.get("extensions", {}).get("submitted_order_count", len(phase_orders)),
                        "audience_context": RELEASE_CONTEXT,
                    },
                }
            )
        years.append({"year": year["year"], "phases": projected_phases})

    retained_evidence: dict[str, Any] = {}
    for source_id in sorted(required_public):
        evidence = source_evidence.get(source_id)
        if evidence is None:
            raise ProjectionError(f"public record has unresolved source reference {source_id!r}")
        if evidence.get("visibility") != "public":
            raise ProjectionError(f"public fact cites non-public evidence {source_id!r}; refusing to rewrite it")
        retained_evidence[source_id] = copy.deepcopy(evidence)
    retained_evidence.update(message_evidence)

    projected = {
        "schema_version": bundle["schema_version"],
        "bundle_id": f"{bundle['bundle_id']}:postgame_public",
        "source": {**copy.deepcopy(bundle["source"]), "visibility_mode": "public"},
        "map": copy.deepcopy(bundle["map"]),
        "game": game,
        "roster": _safe_roster(bundle["roster"]),
        "summary": {},
        "strategic_arcs": [],
        "years": years,
        "messages": messages,
        "orders": projected_orders,
        "private_records": {},
        "evidence_index": dict(sorted(retained_evidence.items())),
        "extensions": {
            "audience": copy.deepcopy(metadata),
            "normalization": {
                "generator": "llm_diplomacy.reporting.report_projector",
                "projection_policy_version": AUDIENCE_POLICY_VERSION,
            },
        },
    }
    return projected, {"released/negotiation_transcript.json": transcript}


def _validate_projected(result: ProjectionResult, mode: str) -> None:
    try:
        validate_report_schema(result.bundle)
    except Exception as error:
        raise ProjectionError(f"projected bundle fails Viewer Report Bundle v1 validation: {error}") from error
    metadata = resolve_audience_metadata(result.bundle)
    if metadata["mode"] != mode or metadata["legacy_inference"]:
        raise ProjectionError("projected bundle does not carry the requested explicit audience metadata")
    for key in ("unresolved_reference_count", "disallowed_private_reference_count", "absolute_path_count"):
        if result.audit[key] != 0:
            raise ProjectionError(f"projection closure audit failed: {key}={result.audit[key]}")


def project_bundle(
    bundle: Mapping[str, Any], audience_mode: str, *, source_sha256: str | None = None
) -> ProjectionResult:
    """Project one Phase 4A bundle using structural, reviewable rules only."""
    if audience_mode not in AUDIENCE_MODES:
        raise ProjectionError(f"unsupported audience mode {audience_mode!r}")
    _require_source_shape(bundle)
    canonical_source = _json_bytes(bundle)
    digest = source_sha256 or _hash_bytes(canonical_source)
    metadata = _audience_metadata(audience_mode, str(bundle["bundle_id"]), digest)
    if audience_mode == "omniscient_research":
        projected = copy.deepcopy(bundle)
        projected["bundle_id"] = f"{bundle['bundle_id']}:omniscient_research"
        projected.setdefault("extensions", {})["audience"] = metadata
        artifacts: dict[str, bytes] = {}
    else:
        projected, artifacts = _project_public(bundle, metadata)
    audit = _audit(bundle, projected, audience_mode)
    result = ProjectionResult(projected, artifacts, audit)
    _validate_projected(result, audience_mode)
    return result


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _source_reports(source: Path) -> tuple[Path, ...]:
    if source.is_file():
        if source.name != "report.json":
            raise ProjectionError("source file must be named report.json")
        return (source,)
    if not source.is_dir():
        raise ProjectionError(f"source does not exist: {source}")
    reports = tuple(sorted(source.rglob("report.json")))
    if not reports:
        raise ProjectionError("source export contains no report.json files")
    return reports


def project_export(
    source: str | Path,
    output_root: str | Path,
    audience_mode: str,
    *,
    raw_experiment_root: str | Path | None = None,
) -> tuple[Path, ...]:
    """Atomically project every discovered report into a new no-overwrite tree."""
    if audience_mode not in AUDIENCE_MODES:
        raise ProjectionError(f"unsupported audience mode {audience_mode!r}")
    source_path = Path(source).resolve()
    destination = Path(output_root).resolve()
    source_tree = source_path if source_path.is_dir() else source_path.parent
    if _inside(destination, source_tree) or _inside(source_tree, destination):
        raise ProjectionError("output root must be outside and separate from the source export tree")
    if raw_experiment_root is not None:
        raw_root = Path(raw_experiment_root).resolve()
        if _inside(destination, raw_root) or _inside(raw_root, destination):
            raise ProjectionError("output root must be outside and separate from the raw experiment tree")
    if destination.exists():
        raise ProjectionError(f"output root already exists; refusing to overwrite: {destination}")

    prepared: list[tuple[str, str, ProjectionResult]] = []
    identities: set[tuple[str, str]] = set()
    for report_path in _source_reports(source_path):
        try:
            source_bytes = report_path.read_bytes()
            bundle = json.loads(source_bytes)
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectionError(f"cannot read source report {report_path.name}: {error}") from error
        experiment_id = bundle.get("source", {}).get("experiment_id")
        game_id = bundle.get("source", {}).get("game_id")
        if not all(isinstance(item, str) and item for item in (experiment_id, game_id)):
            raise ProjectionError("source report has incomplete experiment/game identity")
        identity = (experiment_id, game_id)
        if identity in identities:
            raise ProjectionError(f"duplicate source report identity {experiment_id}/{game_id}")
        identities.add(identity)
        prepared.append((experiment_id, game_id, project_bundle(bundle, audience_mode, source_sha256=_hash_bytes(source_bytes))))
    prepared.sort(key=lambda item: (item[0], item[1]))

    manifest = {
        "schema_version": "diplomacy_projection_audit_v1",
        "policy_version": AUDIENCE_POLICY_VERSION,
        "audience_mode": audience_mode,
        "reports": [
            {
                "experiment_id": experiment_id,
                "game_id": game_id,
                "bundle_id": result.bundle["bundle_id"],
                "projected_sha256": _hash_bytes(_json_bytes(result.bundle)),
                "audit": result.audit,
            }
            for experiment_id, game_id, result in prepared
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep the staging name short enough for historical Windows MAX_PATH environments.
    staging = Path(tempfile.mkdtemp(prefix=".p5a-", dir=destination.parent))
    try:
        for experiment_id, game_id, result in prepared:
            report = staging / "reports" / experiment_id / game_id / audience_mode / "report.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_bytes(_json_bytes(result.bundle))
            for relative, content in result.released_artifacts.items():
                artifact = report.parent / PurePosixPath(relative)
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(content)
        (staging / "projection-audit.json").write_bytes(_json_bytes(manifest))
        if destination.exists():
            raise ProjectionError(f"output root already exists; refusing to overwrite: {destination}")
        staging.rename(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return tuple(
        destination / "reports" / experiment_id / game_id / audience_mode / "report.json"
        for experiment_id, game_id, _ in prepared
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-report-bundle",
        description="Create deterministic audience-safe projections of Phase 4A report bundles.",
    )
    parser.add_argument("--source", required=True, help="Phase 4A report.json or export root.")
    parser.add_argument("--output-root", required=True, help="New destination outside source and raw trees.")
    parser.add_argument("--audience-mode", required=True, choices=AUDIENCE_MODES)
    parser.add_argument("--raw-experiment-root", help="Optional raw experiment root used only for output-path safety.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = project_export(
            args.source,
            args.output_root,
            args.audience_mode,
            raw_experiment_root=args.raw_experiment_root,
        )
    except ProjectionError as error:
        print(f"project-report-bundle: error: {error}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
