from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_diplomacy.reporting.bundle_exporter import (
    ExportInputError,
    audit_exported_evidence,
    discover_games,
    export_experiment,
    inspect_experiment,
    validate_report_schema,
)
from llm_diplomacy.runner import ScriptedExperimentRunner

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _snapshot(experiment_id: str, game_id: str, phase_id: str) -> dict:
    if phase_id == "initial":
        year, season, phase = 1900, "winter", "initial"
    else:
        year_text, season, phase = phase_id.split("_", 2)
        year = int(year_text)
    return {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "game_id": game_id,
        "phase_id": phase_id,
        "snapshot_id": f"{game_id}:{phase_id}",
        "created_at": "2000-01-01T00:00:00Z",
        "payload": {
            "schema_version": "1.0",
            "game_id": game_id,
            "year": year,
            "season": season,
            "phase": phase,
            "supply_ownership": {"par": "france", "ber": "germany"},
            "units": [],
            "standoff_provinces": [],
            "latest_public_resolution": {"public_final_orders": []},
        },
    }


def _make_completed_experiment(root: Path, game_ids: tuple[str, ...] = ("game_alpha", "game_zeta")) -> Path:
    experiment_id = "portable_completed_experiment"
    _write_json(
        root / "manifest.json",
        {"schema_version": "1.0", "experiment_id": experiment_id, "games": len(game_ids)},
    )
    _write_json(
        root / "population_manifest.json",
        {
            "schema_version": "1.0",
            "experiment_id": experiment_id,
            "model_provider": "test-provider",
            "agents": [
                {"agent_id": "alice", "display_name": "Alice", "runtime": {"kind": "scripted"}},
                {"agent_id": "bob", "display_name": "Bob", "runtime": {"kind": "scripted"}},
            ],
        },
    )
    _write_json(
        root / "run_summary.json",
        {
            "schema_version": "1.0",
            "experiment_id": experiment_id,
            "games_requested": len(game_ids),
            "games_completed": len(game_ids),
            "cost_summary_path": "metrics/cost_summary.json",
        },
    )
    _write_json(root / "metrics" / "cost_summary.json", {"schema_version": "1.0", "records_count": 0})

    phase_ids = (
        "1901_spring_movement",
        "1901_fall_movement",
        "1901_winter_adjustment",
    )
    for game_id in game_ids:
        game_root = root / "games" / game_id
        for agent_id, display_name in (("alice", "Alice"), ("bob", "Bob")):
            _write_json(
                game_root / "agents" / agent_id / "folio" / "manifest.json",
                {"schema_version": "1.0", "display_name": display_name},
            )
            _write_json(
                game_root / "agents" / agent_id / "folio" / "game_memory" / f"{game_id}.json",
                {"schema_version": "1.0", "belief_summary": f"Final belief for {agent_id}."},
            )
            (game_root / "agents" / agent_id / "folio" / "persistent_memory.md").write_text(
                f"# Persistent memory\n\nFinal memory for {agent_id}.\n",
                encoding="utf-8",
            )
        _write_json(game_root / "state" / "snapshots" / "initial_state.json", _snapshot(experiment_id, game_id, "initial"))
        chronology = []
        for phase_id in phase_ids:
            _write_json(
                game_root / "state" / "snapshots" / f"{phase_id}_state.json",
                _snapshot(experiment_id, game_id, phase_id),
            )
            _write_json(
                game_root / "phases" / phase_id / "orders" / "submitted_orders.json",
                {"schema_version": "1.0", "orders": []},
            )
            _write_json(
                game_root / "phases" / phase_id / "resolution" / "resolution.json",
                {"schema_version": "1.0", "phase_id": phase_id, "movement": {"outcomes": []}},
            )
            if phase_id == "1901_spring_movement":
                _write_json(
                    game_root / "phases" / phase_id / "reflection" / "alice.json",
                    {"schema_version": "1.0", "reflection": {"belief_summary": "Private belief."}},
                )
            chronology.append({"phase_id": phase_id, "summary": f"Summary for {phase_id}."})
        _write_json(
            game_root / "reporter" / "secret_history.json",
            {"schema_version": "1.0", "game_id": game_id, "chronology": chronology},
        )
        events = {
            "schema_version": "1.0",
            "phase_id": "game_end",
            "payload": {
                "result": {
                    "result": "draw",
                    "country_results": {
                        "france": {"agent_id": "alice", "owned_centers": 17},
                        "germany": {"agent_id": "bob", "owned_centers": 17},
                    },
                }
            },
        }
        event_path = game_root / "logs" / "events.jsonl"
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_text(json.dumps(events) + "\n", encoding="utf-8")
        _write_json(
            game_root / "observatory" / "reports" / "reporter_summary.json",
            {"schema_version": "1.0", "game_id": game_id, "winner_or_leader": {"kind": "leader_at_cap"}},
        )
    return root


def test_discovers_games_without_fixed_ids_and_exports_selected_game(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source")

    assert discover_games(source) == ("game_alpha", "game_zeta")
    paths = export_experiment(source, tmp_path / "output", game_ids=("game_zeta",))

    assert len(paths) == 1
    assert paths[0].as_posix().endswith(
        "reports/portable_completed_experiment/game_zeta/omniscient/report.json"
    )
    assert not (tmp_path / "output" / "reports" / "portable_completed_experiment" / "game_alpha").exists()


def test_export_is_deterministic_chronological_and_path_portable(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source")

    first = export_experiment(source, tmp_path / "first")
    second = export_experiment(source, tmp_path / "second")

    assert len(first) == len(second) == 2
    for first_path, second_path in zip(first, second, strict=True):
        assert first_path.read_bytes() == second_path.read_bytes()
        text = first_path.read_text(encoding="utf-8")
        assert str(source.resolve()) not in text
        report = json.loads(text)
        assert [phase["phase_id"] for phase in report["years"][0]["phases"]] == [
            "1901_spring_movement",
            "1901_fall_movement",
            "1901_winter_adjustment",
        ]
        assert "editorial" not in report["summary"]
        assert all("editorial" not in phase["narrative"] for phase in report["years"][0]["phases"])


def test_export_does_not_modify_any_source_artifact(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}

    export_experiment(source, tmp_path / "output")

    after = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    assert after == before


def test_input_inspection_reports_the_exact_missing_artifact(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source")
    (source / "metrics" / "cost_summary.json").unlink()

    with pytest.raises(ExportInputError, match=r"metrics[/\\]cost_summary\.json"):
        inspect_experiment(source)


def test_evidence_audit_detects_source_mutation(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    report_path = export_experiment(source, tmp_path / "output")[0]
    audit_exported_evidence(report_path, source)

    reporter_path = source / "games" / "game_alpha" / "reporter" / "secret_history.json"
    reporter_path.write_text(reporter_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ExportInputError, match="hash mismatch"):
        audit_exported_evidence(report_path, source)


def test_refuses_to_write_inside_raw_experiment(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))

    with pytest.raises(ExportInputError, match="outside the raw experiment"):
        export_experiment(source, source / "exports")

    assert not (source / "exports").exists()


def test_multi_game_failure_publishes_no_partial_report(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source")
    broken = source / "games" / "game_zeta" / "phases" / "1901_fall_movement" / "resolution" / "resolution.json"
    broken.unlink()
    output = tmp_path / "output"

    with pytest.raises(ExportInputError, match=r"game_zeta/phases/1901_fall_movement/resolution/resolution\.json"):
        export_experiment(source, output)

    assert not (output / "reports").exists()


def test_existing_output_root_is_never_overwritten(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    output = tmp_path / "output"
    report_path = export_experiment(source, output)[0]
    original = report_path.read_bytes()

    with pytest.raises(ExportInputError, match="output root already exists"):
        export_experiment(source, output)

    assert report_path.read_bytes() == original


def test_existing_empty_output_root_is_rejected(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(ExportInputError, match="output root already exists"):
        export_experiment(source, output)


def test_checkout_cli_is_directly_runnable() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/export_report_bundle.py", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--experiment-root" in result.stdout
    assert "--all-games" in result.stdout


def test_duplicate_adjustment_unit_ids_preserve_every_order(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    snapshot_path = source / "games" / "game_alpha" / "state" / "snapshots" / "1901_winter_adjustment_state.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["payload"]["latest_public_resolution"]["public_final_orders"] = [
        {"unit_id": "adjustment::france", "notation": "BUILD A par", "order_type": "BUILD"},
        {"unit_id": "adjustment::france", "notation": "BUILD A mar", "order_type": "BUILD"},
    ]
    _write_json(snapshot_path, snapshot)

    report = json.loads(export_experiment(source, tmp_path / "output")[0].read_text(encoding="utf-8"))

    assert list(report["orders"]) == [
        "1901_winter_adjustment:adjustment::france",
        "1901_winter_adjustment:adjustment::france:2",
    ]
    winter = report["years"][0]["phases"][-1]
    assert winter["orders"] == [
        "1901_winter_adjustment:adjustment::france",
        "1901_winter_adjustment:adjustment::france:2",
    ]


def test_message_ids_reused_across_ticks_preserve_each_record(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    path = source / "games/game_alpha/messages/private/alice/inbox.jsonl"
    path.parent.mkdir(parents=True)
    rows = [dict(schema_version="1.0", message_id="reused", phase_id="1901_spring_negotiation",
                 tick=tick, from_agent_id="bob", to={"to_agent_id": "alice"}, body=f"Tick {tick}")
            for tick in (1, 2, 3)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    duplicate = path.with_name("outbox.jsonl")
    duplicate.write_bytes(path.read_bytes())
    before = path.read_bytes()
    report_path = export_experiment(source, tmp_path / "output")[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["messages"]) == 3
    for tick in (1, 2, 3):
        message_id = f"reused:phase:1901_spring_negotiation:tick:{tick}"
        message = report["messages"][message_id]
        assert message["text"] == f"Tick {tick}"
        assert report["evidence_index"][message["source_ref"]]["json_pointer"] == f"/line/{tick}"
        assert message_id in report["years"][0]["phases"][0]["messages"]
    assert path.read_bytes() == before
    assert export_experiment(source, tmp_path / "again")[0].read_bytes() == report_path.read_bytes()

    rows.append({**rows[0], "body": "Conflicting content"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ExportInputError, match="conflicting duplicate message_id"):
        export_experiment(source, tmp_path / "conflict")


def test_qualified_message_id_collision_is_rejected(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    path = source / "games/game_alpha/messages/global/global.jsonl"
    path.parent.mkdir(parents=True)
    rows = [dict(schema_version="1.0", message_id="reused", phase_id="1901_spring_negotiation",
                 tick=tick, from_agent_id="bob", body="Text") for tick in (1, 2)]
    rows.append({**rows[0], "message_id": "reused:phase:1901_spring_negotiation:tick:1"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ExportInputError, match="qualified message_id collision"):
        export_experiment(source, tmp_path / "output")


def test_run_summary_paths_are_portable_without_mutating_source(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    path = source / "run_summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    paths = {"cost_summary_path": "metrics/cost_summary.json",
             "leaderboard_by_country_path": "leaderboard/by_country.json",
             "leaderboard_overall_path": "leaderboard/overall.json"}
    summary.update({key: str(source.resolve() / value) for key, value in paths.items()})
    _write_json(path, summary)
    before = path.read_bytes()
    report = json.loads(export_experiment(source, tmp_path / "output")[0].read_text(encoding="utf-8"))
    assert all(report["game"]["provenance"]["run_summary"][key] == value for key, value in paths.items())
    assert path.read_bytes() == before
    summary["leaderboard_overall_path"] = str(tmp_path / "outside.json")
    _write_json(path, summary)
    with pytest.raises(ExportInputError, match="escapes experiment root"):
        export_experiment(source, tmp_path / "outside")


def test_public_private_visibility_and_postgame_records_are_preserved(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    private_path = source / "games" / "game_alpha" / "messages" / "private" / "alice" / "inbox.jsonl"
    private_path.parent.mkdir(parents=True)
    private_path.write_text(
        json.dumps({
            "schema_version": "1.0", "message_id": "private-1", "phase_id": "1901_spring_negotiation", "tick": 1,
            "from_agent_id": "bob", "to": {"to_agent_id": "alice"}, "body": "Private text verbatim.",
        }) + "\n",
        encoding="utf-8",
    )
    global_path = source / "games" / "game_alpha" / "messages" / "global" / "global.jsonl"
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        json.dumps({
            "schema_version": "1.0", "message_id": "public-1", "phase_id": "1901_spring_negotiation", "tick": 2,
            "from_agent_id": "alice", "body": "Public text verbatim.",
        }) + "\n",
        encoding="utf-8",
    )

    report = json.loads(export_experiment(source, tmp_path / "output")[0].read_text(encoding="utf-8"))

    assert report["messages"]["private-1"]["visibility"] == "private"
    assert report["messages"]["private-1"]["text"] == "Private text verbatim."
    assert report["messages"]["public-1"]["visibility"] == "public"
    assert report["messages"]["public-1"]["text"] == "Public text verbatim."
    assert {record["record_type"] for record in report["private_records"].values()} >= {
        "reflection", "game_memory", "persistent_memory",
    }
    assert report["extensions"]["postgame_records"]["reporter_summary"]["visibility"] == "omniscient_postgame"


def test_orders_cite_final_public_record_and_adjudication(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    snapshot_path = source / "games" / "game_alpha" / "state" / "snapshots" / "1901_spring_movement_state.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["payload"]["latest_public_resolution"]["public_final_orders"] = [
        {"unit_id": "unit_1", "notation": "A par H", "order_type": "HOLD", "power": "france"},
    ]
    _write_json(snapshot_path, snapshot)

    report = json.loads(export_experiment(source, tmp_path / "output")[0].read_text(encoding="utf-8"))
    order = report["orders"]["1901_spring_movement:unit_1"]
    final_source = report["evidence_index"][order["source_ref"]]

    assert final_source["artifact_path"].endswith("1901_spring_movement_state.json")
    assert final_source["json_pointer"] == "/payload/latest_public_resolution/public_final_orders/0"
    assert report["evidence_index"][order["submitted_source_ref"]]["kind"] == "orders"
    assert report["evidence_index"][order["adjudication_source_ref"]]["kind"] == "adjudication"


@pytest.mark.parametrize(
    ("recorded_result", "centers", "final_phase", "expected_kind", "solo_power"),
    [
        ("completed", 18, "1910_winter_adjustment", "scheduled_max_year", None),
    ],
)
def test_outcome_policy_distinguishes_cutoff_from_explicit_solo(
    tmp_path: Path,
    recorded_result: str,
    centers: int,
    final_phase: str,
    expected_kind: str,
    solo_power: str | None,
) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    events_path = source / "games" / "game_alpha" / "logs" / "events.jsonl"
    event = json.loads(events_path.read_text(encoding="utf-8"))
    event["payload"]["result"]["result"] = recorded_result
    event["payload"]["result"]["country_results"]["france"]["owned_centers"] = centers
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    if final_phase.startswith("1910"):
        for phase in ("1910_spring_movement", "1910_fall_movement", "1910_winter_adjustment"):
            _write_json(source / "games" / "game_alpha" / "state" / "snapshots" / f"{phase}_state.json", _snapshot("portable_completed_experiment", "game_alpha", phase))
            _write_json(source / "games" / "game_alpha" / "phases" / phase / "orders" / "submitted_orders.json", {"schema_version": "1.0", "orders": []})
            _write_json(source / "games" / "game_alpha" / "phases" / phase / "resolution" / "resolution.json", {"schema_version": "1.0", "phase_id": phase})

    report = json.loads(export_experiment(source, tmp_path / "output")[0].read_text(encoding="utf-8"))
    assert report["game"]["outcome"]["termination"]["kind"] == expected_kind
    assert (report["game"]["outcome"]["solo_victor"] or {}).get("power") == solo_power
    assert report["game"]["provenance"]["raw_outcome"]["result"] == recorded_result
    game_end_source = report["evidence_index"][report["game"]["provenance"]["source_refs"]["game_end"]]
    assert game_end_source["artifact_path"].endswith("logs/events.jsonl")
    assert game_end_source["json_pointer"] == "/line/1/payload/result"


def test_export_accepts_only_evidenced_final_fall_solo(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "solo-source", ("game_alpha",))
    game_root = source / "games" / "game_alpha"
    (game_root / "state" / "snapshots" / "1901_winter_adjustment_state.json").unlink()
    supply_centers = [
        province["id"]
        for province in json.loads((ROOT / "maps" / "standard.json").read_text(encoding="utf-8"))["provinces"]
        if province["supply_center"]
    ]
    ownership = {center: "france" for center in supply_centers[:18]}
    ownership.update({center: "germany" for center in supply_centers[18:]})
    fall_snapshot = game_root / "state" / "snapshots" / "1901_fall_movement_state.json"
    snapshot = json.loads(fall_snapshot.read_text(encoding="utf-8"))
    snapshot["payload"]["supply_ownership"] = ownership
    _write_json(fall_snapshot, snapshot)
    fall_resolution = game_root / "phases" / "1901_fall_movement" / "resolution" / "resolution.json"
    resolution = json.loads(fall_resolution.read_text(encoding="utf-8"))
    resolution["output_state"] = {"supply_ownership": ownership, "units": snapshot["payload"]["units"]}
    _write_json(fall_resolution, resolution)

    event_path = game_root / "logs" / "events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    result = event["payload"]["result"]
    result.update({
        "result": "solo_victory", "termination_reason": "solo_victory",
        "winners": ["alice"], "final_phase_id": "1901_fall_movement",
        "solo_victor": {"power": "france", "agent_id": "alice", "owned_centers": 18},
        "country_results": {
            "france": {"agent_id": "alice", "owned_centers": 18, "result": "win"},
            "germany": {"agent_id": "alice", "owned_centers": len(supply_centers) - 18, "result": "not_won"},
        },
    })
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    shutil.rmtree(game_root / "agents" / "bob")

    report_path = export_experiment(source, tmp_path / "solo-output")[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["game"]["outcome"]["termination"]["kind"] == "solo_victory"
    assert report["game"]["outcome"]["termination"]["final_phase_id"] == "1901_fall_movement"
    phases = report["years"][0]["phases"]
    assert [phase["phase_id"] for phase in phases][-1] == "1901_fall_movement"
    assert len(phases) == 2
    assert sum(owner == "france" for owner in phases[-1]["board_after"]["supply_ownership"].values()) == 18
    assert len(report["roster"]) == 2
    assert {(member["agent_id"], member["power"]) for member in report["roster"]} == {("alice", "france"), ("alice", "germany")}
    validate_report_schema(report)
    viewer_validation = subprocess.run(
        ["node", "--experimental-strip-types", "scripts/validate_bundle.ts", str(report_path)],
        cwd=ROOT / "viewer", check=False, capture_output=True, text=True,
    )
    assert viewer_validation.returncode == 0, viewer_validation.stderr

    invalid_ownership = {**ownership, "neutral": ["not", "a", "power"]}
    snapshot["payload"]["supply_ownership"] = invalid_ownership
    _write_json(fall_snapshot, snapshot)
    resolution["output_state"] = {"supply_ownership": invalid_ownership, "units": snapshot["payload"]["units"]}
    _write_json(fall_resolution, resolution)
    with pytest.raises(ExportInputError, match="conflicts with the final Fall board"):
        export_experiment(source, tmp_path / "invalid-owner-output")

    snapshot["payload"]["supply_ownership"] = ownership
    _write_json(fall_snapshot, snapshot)
    resolution["output_state"] = {"supply_ownership": ownership, "units": snapshot["payload"]["units"]}
    _write_json(fall_resolution, resolution)
    resolution["output_state"]["supply_ownership"] = {**ownership, supply_centers[0]: "germany"}
    _write_json(fall_resolution, resolution)
    with pytest.raises(ExportInputError, match="conflicts with the final Fall board"):
        export_experiment(source, tmp_path / "mismatched-adjudication-output")

    resolution["output_state"] = {"supply_ownership": ownership, "units": [{"owner": "germany", "status": "dislodged", "must_retreat": True}]}
    snapshot["payload"]["units"] = resolution["output_state"]["units"]
    _write_json(fall_snapshot, snapshot)
    _write_json(fall_resolution, resolution)
    with pytest.raises(ExportInputError, match="conflicts with the final Fall board"):
        export_experiment(source, tmp_path / "pending-retreat-output")


def test_solo_export_rejects_a_nonwinning_country_count_not_on_final_board(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "solo-source", ("game_alpha",))
    game_root = source / "games" / "game_alpha"
    (game_root / "state" / "snapshots" / "1901_winter_adjustment_state.json").unlink()
    supply_centers = [
        province["id"]
        for province in json.loads((ROOT / "maps" / "standard.json").read_text(encoding="utf-8"))["provinces"]
        if province["supply_center"]
    ]
    ownership = {center: "france" for center in supply_centers[:18]}
    ownership.update({center: "germany" for center in supply_centers[18:]})
    snapshot_path = game_root / "state" / "snapshots" / "1901_fall_movement_state.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["payload"]["supply_ownership"] = ownership
    _write_json(snapshot_path, snapshot)
    resolution_path = game_root / "phases" / "1901_fall_movement" / "resolution" / "resolution.json"
    resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
    resolution["output_state"] = {"supply_ownership": ownership, "units": snapshot["payload"]["units"]}
    _write_json(resolution_path, resolution)
    event_path = game_root / "logs" / "events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    result = event["payload"]["result"]
    result.update({
        "result": "solo_victory", "winners": ["alice"], "final_phase_id": "1901_fall_movement",
        "solo_victor": {"power": "france", "agent_id": "alice", "owned_centers": 18},
        "country_results": {
            "france": {"agent_id": "alice", "owned_centers": 18, "result": "win"},
            "germany": {"agent_id": "bob", "owned_centers": 25, "result": "not_won"},
        },
    })
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(ExportInputError, match="conflicts with the final Fall board"):
        export_experiment(source, tmp_path / "output")


def test_export_and_viewer_accept_a_coherent_final_fall_retreat_solo(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "solo-source", ("game_alpha",))
    game_root = source / "games" / "game_alpha"
    (game_root / "state" / "snapshots" / "1901_winter_adjustment_state.json").unlink()
    supply_centers = [
        province["id"]
        for province in json.loads((ROOT / "maps" / "standard.json").read_text(encoding="utf-8"))["provinces"]
        if province["supply_center"]
    ]
    ownership = {center: "france" for center in supply_centers[:18]}
    ownership.update({center: "germany" for center in supply_centers[18:]})
    retreat_id = "1901_fall_retreat"
    retreat_snapshot = _snapshot("portable_completed_experiment", "game_alpha", retreat_id)
    retreat_snapshot["payload"]["supply_ownership"] = ownership
    retreat_snapshot["payload"]["units"] = []
    _write_json(game_root / "state" / "snapshots" / f"{retreat_id}_state.json", retreat_snapshot)
    phase_root = game_root / "phases" / retreat_id
    _write_json(phase_root / "orders" / "submitted_orders.json", {"schema_version": "1.0", "orders": []})
    _write_json(phase_root / "resolution" / "resolution.json", {
        "schema_version": "1.0", "phase_id": retreat_id,
        "output_state": {"supply_ownership": ownership, "units": []},
        "movement": {"outcomes": []},
    })
    history_path = game_root / "reporter" / "secret_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    history["chronology"].append({"phase_id": retreat_id, "summary": "Final Fall retreat."})
    _write_json(history_path, history)
    fall_snapshot_path = game_root / "state" / "snapshots" / "1901_fall_movement_state.json"
    fall_snapshot = json.loads(fall_snapshot_path.read_text(encoding="utf-8"))
    fall_snapshot["payload"]["supply_ownership"] = ownership
    _write_json(fall_snapshot_path, fall_snapshot)
    fall_resolution_path = game_root / "phases" / "1901_fall_movement" / "resolution" / "resolution.json"
    fall_resolution = json.loads(fall_resolution_path.read_text(encoding="utf-8"))
    fall_resolution["output_state"] = {"supply_ownership": ownership, "units": []}
    _write_json(fall_resolution_path, fall_resolution)
    event_path = game_root / "logs" / "events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"]["result"].update({
        "result": "solo_victory", "winners": ["alice"], "final_phase_id": retreat_id,
        "solo_victor": {"power": "france", "agent_id": "alice", "owned_centers": 18},
        "country_results": {
            "france": {"agent_id": "alice", "owned_centers": 18, "result": "win"},
            "germany": {"agent_id": "bob", "owned_centers": len(supply_centers) - 18, "result": "not_won"},
        },
    })
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    report_path = export_experiment(source, tmp_path / "output")[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["game"]["outcome"]["termination"]["final_phase_id"] == retreat_id
    assert report["years"][0]["phases"][-1]["phase_id"] == retreat_id
    viewer_validation = subprocess.run(
        ["node", "--experimental-strip-types", "scripts/validate_bundle.ts", str(report_path)],
        cwd=ROOT / "viewer", check=False, capture_output=True, text=True,
    )
    assert viewer_validation.returncode == 0, viewer_validation.stderr


def test_runner_result_exports_solo_with_null_neutral_centers(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "solo-source", ("game_alpha",))
    game_root = source / "games" / "game_alpha"
    supply_centers = [
        province["id"]
        for province in json.loads((ROOT / "maps" / "standard.json").read_text(encoding="utf-8"))["provinces"]
        if province["supply_center"]
    ]
    ownership = {center: None for center in supply_centers}
    for center in supply_centers[:18]:
        ownership[center] = "france"
    assignments = {
        "austria": "bob", "england": "bob", "france": "alice", "germany": "bob",
        "italy": "bob", "russia": "bob", "turkey": "bob",
    }
    runner = object.__new__(ScriptedExperimentRunner)
    runner.config = SimpleNamespace(schema_version="1.0")
    runner.budget = None
    raw_result = runner._game_result_payload(
        {"year": 1901, "season": "fall", "phase": "movement", "supply_ownership": ownership, "units": []},
        assignments,
    )
    assert raw_result["result"] == "solo_victory"
    assert raw_result["solo_victor"] == {"power": "france", "agent_id": "alice", "owned_centers": 18}

    (game_root / "state" / "snapshots" / "1901_winter_adjustment_state.json").unlink()
    snapshot_path = game_root / "state" / "snapshots" / "1901_fall_movement_state.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["payload"]["supply_ownership"] = ownership
    snapshot["payload"]["units"] = []
    _write_json(snapshot_path, snapshot)
    resolution_path = game_root / "phases" / "1901_fall_movement" / "resolution" / "resolution.json"
    resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
    resolution["output_state"] = {"supply_ownership": ownership, "units": []}
    _write_json(resolution_path, resolution)
    event_path = game_root / "logs" / "events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"]["result"] = raw_result
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    report_path = export_experiment(source, tmp_path / "output")[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["game"]["outcome"]["solo_victor"] == {"agent_id": "alice", "power": "france", "supply_centers": 18}
    final_phase = report["years"][0]["phases"][-1]
    assert final_phase["phase_id"] == "1901_fall_movement"
    assert any(owner is None for owner in final_phase["board_after"]["supply_ownership"].values())
    assert final_phase["adjudication"]["output_state"]["supply_ownership"] == final_phase["board_after"]["supply_ownership"]
    validate_report_schema(report)
    viewer_validation = subprocess.run(
        ["node", "--experimental-strip-types", "scripts/validate_bundle.ts", str(report_path)],
        cwd=ROOT / "viewer", check=False, capture_output=True, text=True,
    )
    assert viewer_validation.returncode == 0, viewer_validation.stderr


def test_audit_rejects_unsafe_paths_and_unresolved_references(tmp_path: Path) -> None:
    source = _make_completed_experiment(tmp_path / "source", ("game_alpha",))
    report_path = export_experiment(source, tmp_path / "output")[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_id = next(iter(report["evidence_index"]))
    report["evidence_index"][source_id]["artifact_path"] = "../escape.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ExportInputError, match="non-portable artifact_path"):
        audit_exported_evidence(report_path, source)

    report = json.loads(export_experiment(source, tmp_path / "second")[0].read_text(encoding="utf-8"))
    order_id = next(iter(report["orders"]), None)
    if order_id is None:
        report["orders"]["bad"] = {"source_ref": "missing"}
    else:
        report["orders"][order_id]["source_ref"] = "missing"
    bad_path = tmp_path / "bad-report.json"
    bad_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ExportInputError, match="unresolved source_ref"):
        audit_exported_evidence(bad_path, source)


def test_packaged_viewer_schema_rejects_invalid_bundle() -> None:
    with pytest.raises(ExportInputError, match=r"viewer v1 JSON Schema violation.*schema_version"):
        validate_report_schema({})


@pytest.mark.parametrize("version", [None, "2.0", 1])
@pytest.mark.parametrize("relative", [
    "manifest.json", "population_manifest.json", "run_summary.json",
    "metrics/cost_summary.json", "games/game_alpha/state/snapshots/initial.json",
    "games/game_alpha/reporter/secret_history.json",
    "games/game_alpha/phases/1901_spring_movement/orders/submitted_orders.json",
    "games/game_alpha/phases/1901_spring_movement/resolution/resolution.json",
    "games/game_alpha/logs/events.jsonl",
])
def test_export_rejects_unsupported_raw_versions(tmp_path, version, relative):
    source = _make_completed_experiment(tmp_path / "raw")
    path = source / relative
    if not path.exists() and path.name == "initial.json":
        path = path.with_name("initial_state.json")
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.suffix == ".jsonl" else [json.loads(path.read_text())]
    for row in rows:
        if version is None:
            row.pop("schema_version", None)
        else:
            row["schema_version"] = version
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ExportInputError, match="schema_version"):
        export_experiment(source, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_legacy_summary_remains_unmodified_and_accepts_additive_fields(tmp_path):
    source = _make_completed_experiment(tmp_path / "raw")
    path = source / "run_summary.json"
    summary = json.loads(path.read_text())
    for field in ("status", "games_started", "games_stopped", "games_failed"):
        summary.pop(field, None)
    summary["future_optional_note"] = "retained"
    _write_json(path, summary)
    before = path.read_bytes()
    inspection = inspect_experiment(source)
    assert "games_failed" not in inspection.run_summary
    export_experiment(source, tmp_path / "report")
    assert path.read_bytes() == before


@pytest.mark.parametrize("channel", ["private/alice/inbox.jsonl", "global/global.jsonl"])
@pytest.mark.parametrize("version", [None, "2.0"])
def test_export_rejects_unsupported_message_versions(tmp_path, channel, version):
    source = _make_completed_experiment(tmp_path / "raw")
    path = source / "games/game_alpha/messages" / channel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": version}) + "\n")
    with pytest.raises(ExportInputError, match="schema_version"):
        export_experiment(source, tmp_path / "report")
    assert not (tmp_path / "report").exists()
