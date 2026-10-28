from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from llm_diplomacy.reporting.report_projector import (
    AUDIENCE_POLICY_VERSION,
    ProjectionError,
    project_bundle,
    project_export,
    resolve_audience_metadata,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_bundle(game_id: str = "game_alpha") -> dict:
    public_hash = "1" * 64
    private_hash = "2" * 64
    omniscient_hash = "3" * 64
    return {
        "schema_version": "diplomacy_report_bundle_v1",
        "bundle_id": f"experiment:{game_id}:v1",
        "source": {
            "experiment_id": "experiment",
            "game_id": game_id,
            "artifact_manifest_sha256": "4" * 64,
            "visibility_mode": "omniscient_postgame",
        },
        "map": {"map_id": "standard_v1", "geometry_version": "standard_geometry_v1"},
        "game": {
            "title": f"experiment / {game_id}",
            "years_played": [1901, 1901],
            "stop_condition": "draw",
            "outcome": {
                "termination": {"kind": "draw", "label": "Recorded draw", "final_year": 1901},
                "leaders": [],
                "solo_victor": None,
            },
            "model_provenance": "provider-diagnostic-must-not-leak",
            "cost_and_call_metadata": {"secret": "runtime-diagnostic-must-not-leak"},
            "provenance": {"source_refs": {"game_end": "source_game_end"}},
        },
        "roster": [
            {
                "agent_id": "alice",
                "display_name": "Alice",
                "power": "france",
                "runtime": {"secret": "runtime-profile-must-not-leak"},
            },
            {"agent_id": "bob", "display_name": "Bob", "power": "germany"},
        ],
        "summary": {"chronology_source_ref": "source_reporter"},
        "strategic_arcs": [],
        "years": [
            {
                "year": 1901,
                "phases": [
                    {
                        "phase_id": "1901_spring_movement",
                        "kind": "movement",
                        "year": 1901,
                        "season": "spring",
                        "board_before": {"supply_ownership": {}, "units": [], "standoff_provinces": []},
                        "board_after": {"supply_ownership": {}, "units": [], "standoff_provinces": []},
                        "messages": ["message_private", "message_global"],
                        "orders": ["order_1"],
                        "adjudication": {"movement_outcomes": []},
                        "private_records": ["reflection_1"],
                        "narrative": {
                            "overview": "PRIVATE_NARRATIVE_SENTINEL",
                            "theater_sections": [],
                            "aftermath": "",
                            "source_refs": ["source_reporter"],
                        },
                        "source_refs": ["source_board", "source_orders", "source_adjudication", "source_reporter"],
                        "extensions": {"submitted_order_count": 1, "provider_calls": 99},
                    }
                ],
            }
        ],
        "messages": {
            "message_private": {
                "message_id": "message_private",
                "phase_id": "1901_spring_negotiation",
                "chronological_position": 1,
                "sender_agent_id": "alice",
                "recipient_agent_ids": ["bob"],
                "channel": "private",
                "thread_id": "alice__bob",
                "text": "PRIVATE_NEGOTIATION_TEXT_RETAINED_VERBATIM",
                "visibility": "private",
                "source_ref": "source_private_message",
            },
            "message_global": {
                "message_id": "message_global",
                "phase_id": "1901_spring_negotiation",
                "chronological_position": 2,
                "sender_agent_id": "bob",
                "recipient_agent_ids": [],
                "channel": "global",
                "thread_id": "global",
                "text": "GLOBAL_NEGOTIATION_TEXT_RETAINED_VERBATIM",
                "visibility": "public",
                "source_ref": "source_global_message",
            },
        },
        "orders": {
            "order_1": {
                "unit_id": "unit_1",
                "power": "france",
                "notation": "A par H",
                "order_type": "HOLD",
                "source_ref": "source_order_final",
                "submitted_source_ref": "source_orders",
                "adjudication_source_ref": "source_adjudication",
            }
        },
        "private_records": {
            "reflection_1": {
                "record_id": "reflection_1",
                "agent_id": "alice",
                "record_type": "reflection",
                "visibility": "private",
                "payload": {
                    "belief_summary": "PRIVATE_COGNITION_SENTINEL",
                    "campaign_plan": "PRIVATE_PLAN_SENTINEL",
                },
                "source_ref": "source_private_record",
            }
        },
        "evidence_index": {
            "source_game_end": {"source_id": "source_game_end", "artifact_path": f"games/{game_id}/logs/events.jsonl", "json_pointer": "/line/1/payload/result", "sha256": public_hash, "visibility": "public", "kind": "report"},
            "source_board": {"source_id": "source_board", "artifact_path": f"games/{game_id}/state/snapshot.json", "sha256": public_hash, "visibility": "public", "kind": "board_snapshot"},
            "source_orders": {"source_id": "source_orders", "artifact_path": f"games/{game_id}/orders.json", "sha256": public_hash, "visibility": "public", "kind": "orders"},
            "source_order_final": {"source_id": "source_order_final", "artifact_path": f"games/{game_id}/state/snapshot.json", "json_pointer": "/payload/latest_public_resolution/public_final_orders/0", "sha256": public_hash, "visibility": "public", "kind": "orders"},
            "source_adjudication": {"source_id": "source_adjudication", "artifact_path": f"games/{game_id}/resolution.json", "sha256": public_hash, "visibility": "public", "kind": "adjudication"},
            "source_private_message": {"source_id": "source_private_message", "artifact_path": f"games/{game_id}/messages/private/alice/inbox.jsonl", "json_pointer": "/line/1", "sha256": private_hash, "visibility": "private", "kind": "message"},
            "source_global_message": {"source_id": "source_global_message", "artifact_path": f"games/{game_id}/messages/global/global.jsonl", "json_pointer": "/line/1", "sha256": public_hash, "visibility": "public", "kind": "message"},
            "source_private_record": {"source_id": "source_private_record", "artifact_path": f"games/{game_id}/phases/1901_spring_movement/reflection/alice.json", "sha256": private_hash, "visibility": "private", "kind": "private_record"},
            "source_reporter": {"source_id": "source_reporter", "artifact_path": f"games/{game_id}/reporter/secret_history.json", "sha256": omniscient_hash, "visibility": "omniscient_postgame", "kind": "report"},
            "source_postgame": {"source_id": "source_postgame", "artifact_path": f"games/{game_id}/observatory/reporter_summary.json", "sha256": omniscient_hash, "visibility": "omniscient_postgame", "kind": "report"},
        },
        "extensions": {
            "normalization": {"generator": "fixture"},
            "postgame_records": {
                "outcome_review": {
                    "record_id": "outcome_review",
                    "visibility": "omniscient_postgame",
                    "payload": {"text": "PRIVATE_POSTGAME_SENTINEL"},
                    "source_ref": "source_postgame",
                }
            },
        },
    }


def _write_source_export(root: Path) -> tuple[Path, dict[str, bytes]]:
    for game_id in ("game_alpha", "game_beta"):
        path = root / "reports" / "experiment" / game_id / "omniscient" / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_source_bundle(game_id), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    return root, before


def test_postgame_public_projection_is_closed_and_retains_released_messages() -> None:
    source = _source_bundle()
    result = project_bundle(source, "postgame_public")
    projected = result.bundle
    text = json.dumps(projected, sort_keys=True)

    metadata = projected["extensions"]["audience"]
    assert metadata["mode"] == "postgame_public"
    assert metadata["policy_version"] == AUDIENCE_POLICY_VERSION
    assert metadata["approved_for_static_public_distribution"] is True
    assert projected["source"]["visibility_mode"] == "public"
    assert projected["private_records"] == {}
    assert projected["extensions"].get("postgame_records") is None
    assert set(projected["messages"]) == {"message_private", "message_global"}
    assert set(projected["orders"]) == {"order_1"}
    assert projected["game"]["outcome"] == source["game"]["outcome"]
    assert projected["messages"]["message_private"]["visibility"] == "private"
    assert projected["messages"]["message_private"]["text"] == "PRIVATE_NEGOTIATION_TEXT_RETAINED_VERBATIM"
    private_evidence = projected["evidence_index"][projected["messages"]["message_private"]["source_ref"]]
    assert private_evidence["release_context"] == "postgame_released_negotiation_transcript"
    assert "messages/private" not in private_evidence["artifact_path"]
    transcript = result.released_artifacts[private_evidence["artifact_path"]]
    assert _sha(transcript) == private_evidence["sha256"]
    for sentinel in (
        "PRIVATE_COGNITION_SENTINEL",
        "PRIVATE_PLAN_SENTINEL",
        "PRIVATE_NARRATIVE_SENTINEL",
        "PRIVATE_POSTGAME_SENTINEL",
        "runtime-diagnostic-must-not-leak",
        "runtime-profile-must-not-leak",
    ):
        assert sentinel not in text
    assert result.audit["unresolved_reference_count"] == 0
    assert result.audit["disallowed_private_reference_count"] == 0
    assert result.audit["absolute_path_count"] == 0
    assert result.audit["excluded_records_by_type"]["reflection"] == 1
    assert result.audit["included_records_by_type"]["messages"] == 2


def test_omniscient_research_is_explicit_and_legacy_is_never_public_safe() -> None:
    source = _source_bundle()
    legacy = resolve_audience_metadata(source)
    assert legacy["mode"] == "omniscient_research"
    assert legacy["approved_for_static_public_distribution"] is False
    assert legacy["legacy_inference"] is True

    projected = project_bundle(source, "omniscient_research").bundle
    metadata = resolve_audience_metadata(projected)
    assert metadata["mode"] == "omniscient_research"
    assert metadata["approved_for_static_public_distribution"] is False
    assert metadata["legacy_inference"] is False
    assert projected["private_records"] == source["private_records"]


def test_projection_is_byte_deterministic_atomic_and_does_not_mutate_sources(tmp_path: Path) -> None:
    source_root, before = _write_source_export(tmp_path / "source-export")
    raw_root = tmp_path / "raw-experiment"
    raw_root.mkdir()
    (raw_root / "sentinel.txt").write_text("unchanged", encoding="utf-8")
    raw_before = (raw_root / "sentinel.txt").read_bytes()

    first_paths = project_export(source_root, tmp_path / "first", "postgame_public", raw_experiment_root=raw_root)
    second_paths = project_export(source_root, tmp_path / "second", "postgame_public", raw_experiment_root=raw_root)

    assert len(first_paths) == len(second_paths) == 2
    for first, second in zip(first_paths, second_paths, strict=True):
        assert first.read_bytes() == second.read_bytes()
        assert first.as_posix().endswith("postgame_public/report.json")
    assert (tmp_path / "first" / "projection-audit.json").read_bytes() == (tmp_path / "second" / "projection-audit.json").read_bytes()
    assert {path.relative_to(source_root): path.read_bytes() for path in source_root.rglob("*") if path.is_file()} == before
    assert (raw_root / "sentinel.txt").read_bytes() == raw_before
    assert not any("C:\\" in path.read_text(encoding="utf-8") for path in first_paths)

    with pytest.raises(ProjectionError, match="already exists"):
        project_export(source_root, tmp_path / "first", "postgame_public", raw_experiment_root=raw_root)


def test_projection_refuses_unsafe_roots_unknown_modes_and_invalid_closure(tmp_path: Path) -> None:
    source_root, _ = _write_source_export(tmp_path / "source-export")
    raw_root = tmp_path / "raw-experiment"
    raw_root.mkdir()

    with pytest.raises(ProjectionError, match="unsupported audience mode"):
        project_bundle(_source_bundle(), "power_limited")
    with pytest.raises(ProjectionError, match="source export tree"):
        project_export(source_root, source_root / "projected", "postgame_public", raw_experiment_root=raw_root)
    with pytest.raises(ProjectionError, match="raw experiment tree"):
        project_export(source_root, raw_root / "projected", "postgame_public", raw_experiment_root=raw_root)

    unknown = _source_bundle()
    unknown["private_records"]["reflection_1"]["record_type"] = "unclassified_secret"
    with pytest.raises(ProjectionError, match="unclassified private record type"):
        project_bundle(unknown, "postgame_public")

    unresolved = _source_bundle()
    unresolved["messages"]["message_private"]["source_ref"] = "missing"
    with pytest.raises(ProjectionError, match="unresolved"):
        project_bundle(unresolved, "postgame_public")

    unsafe = _source_bundle()
    unsafe["evidence_index"]["source_board"]["artifact_path"] = "C:\\private\\board.json"
    with pytest.raises(ProjectionError, match="unsafe artifact path"):
        project_bundle(unsafe, "postgame_public")


@pytest.mark.parametrize("record_type", sorted({
    "reflection", "belief", "belief_summary", "campaign_plan", "commitment_memory",
    "game_memory", "persistent_memory", "outcome_review", "winter_assessment",
    "agent_folio", "raw_prompt_context",
}))
def test_every_classified_private_record_type_is_excluded(record_type: str) -> None:
    source = _source_bundle()
    source["private_records"]["reflection_1"]["record_type"] = record_type
    projected = project_bundle(source, "postgame_public")
    assert projected.bundle["private_records"] == {}
    assert projected.audit["excluded_records_by_type"][record_type] == 1


def test_checkout_projector_cli_is_directly_runnable() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/project_report_bundle.py", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--source" in result.stdout
    assert "--audience-mode" in result.stdout
