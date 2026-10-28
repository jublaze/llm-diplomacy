from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from llm_diplomacy.compliance.artifacts import ArtifactValidator
from llm_diplomacy.compliance.datc import (
    discover_datc_fixture_dirs,
    ingest_datc_fixtures,
    run_datc_cases,
)
from llm_diplomacy.compliance.replay import compare_experiment_dirs, compare_rerun_determinism
from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine
from llm_diplomacy.runner.experiment_runner import ExperimentRunner


def _config(tmp_path: Path, exp_id: str = "exp_compliance") -> Path:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": exp_id,
        "games": 2,
        "agents": [{"agent_id": "a1"}, {"agent_id": "a2"}],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 4,
    }
    path = tmp_path / f"{exp_id}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _error_categories(report) -> set[str]:
    return {err.category for err in report.errors}


def test_artifact_validator_passes_for_runner_outputs(tmp_path) -> None:
    cfg_path = _config(tmp_path, "exp_val")
    cfg = load_experiment_config(cfg_path)
    ExperimentRunner(config=cfg, base_dir=tmp_path).run()
    exp_root = tmp_path / "experiments" / cfg.experiment_id
    report = ArtifactValidator(schema_version="1.0").validate_experiment_dir(exp_root)
    assert report.files_checked > 0
    assert report.ok, [str(err) for err in report.errors]


def test_deterministic_rerun_comparison(tmp_path) -> None:
    cfg_path = _config(tmp_path, "exp_replay")
    cfg = load_experiment_config(cfg_path)
    result = compare_rerun_determinism(cfg)
    assert result.compared_files > 0
    assert result.deterministic, result.differences


def test_datc_ingestion_runner_for_committed_fixtures() -> None:
    engine = DiplomacyRuleEngine()
    committed_dir, _ = discover_datc_fixture_dirs(Path("tests") / "fixtures" / "datc")
    cases = ingest_datc_fixtures(committed_dir)
    report = run_datc_cases(engine, cases)
    assert report.total >= 8
    assert report.failed == 0, [f"{f.case_id}:{f.detail}" for f in report.failures]


def test_datc_external_corpus_is_optional_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_DIPLOMACY_DATC_DIR", raising=False)
    _, external = discover_datc_fixture_dirs(Path("tests") / "fixtures" / "datc")
    assert external is None


def test_datc_external_corpus_missing_configured_directory_is_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "not_there"
    monkeypatch.setenv("LLM_DIPLOMACY_DATC_DIR", str(missing))
    with pytest.raises(FileNotFoundError, match="Configured DATC fixture directory does not exist"):
        discover_datc_fixture_dirs(Path("tests") / "fixtures" / "datc")


def test_datc_external_corpus_executes_all_pinned_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = Path("tests") / "fixtures" / "datc" / "upstream"
    monkeypatch.setenv("LLM_DIPLOMACY_DATC_DIR", str(corpus.resolve()))
    _, external = discover_datc_fixture_dirs(Path("tests") / "fixtures" / "datc")
    assert external == corpus.resolve()
    cases = ingest_datc_fixtures(external)
    assert len(cases) == 172
    assert len({case.case_id for case in cases}) == 172
    from llm_diplomacy.compliance.datc import check_datc_coverage
    report = check_datc_coverage(DiplomacyRuleEngine(), cases, corpus / "coverage.json")
    assert report["unexplained"] == [], report["unexplained"]
    assert report["passed"] == 155
    assert report["policy_deviations"] == 9
    assert report["adapter_limitations"] == 1
    assert report["historical_alternatives"] == 7


def test_datc_runner_rejects_empty_case_list() -> None:
    with pytest.raises(ValueError, match="requires at least one case"):
        run_datc_cases(DiplomacyRuleEngine(), [])


def test_end_to_end_smoke_script(tmp_path) -> None:
    cfg_path = _config(tmp_path, "exp_smoke_script")
    output_root = tmp_path / "out"
    python_exe = Path(".venv") / "Scripts" / "python.exe"
    proc = subprocess.run(
        [
            str(python_exe),
            "scripts/run_smoke_game.py",
            "--config",
            str(cfg_path),
            "--output-root",
            str(output_root),
        ],
        cwd=str(Path.cwd()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip())
    assert payload["summary"]["validation_ok"] is True


def test_artifact_validator_negative_matrix_categories(tmp_path) -> None:
    root = tmp_path / "exp"
    root.mkdir(parents=True, exist_ok=True)

    # malformed JSON + missing schema_version
    (root / "bad.json").write_text("{not json", encoding="utf-8")
    _write_json(root / "noschema.json", {"x": 1})

    # malformed JSONL + bad envelope rows
    (root / "logs" / "events.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "logs" / "events.jsonl").write_text(
        '{"schema_version":"1.0","event_type":"phase_started"}\n'
        '{"schema_version":"1.0","event_type":"phase_completed","experiment_id":"e","game_id":"g","payload":{},"event_id":"id","timestamp":"t"}\n'
        '{bad line}\n',
        encoding="utf-8",
    )
    (root / "messages" / "global" / "global.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "messages" / "global" / "global.jsonl").write_text(
        '{"schema_version":"1.0","message_id":"m1"}\n',
        encoding="utf-8",
    )

    # invalid snapshot/order/resolution/leaderboard/cost artifacts
    _write_json(
        root / "games" / "game_001" / "state" / "snapshots" / "s1.json",
        {"schema_version": "1.0", "snapshot_id": "s1"},
    )
    _write_json(
        root / "games" / "game_001" / "phases" / "1901_spring_movement" / "orders" / "submitted_orders.json",
        {"schema_version": "1.0", "phase_id": "1901_spring_movement"},
    )
    _write_json(
        root / "games" / "game_001" / "phases" / "1901_spring_movement" / "resolution" / "adjudication.json",
        {"schema_version": "1.0", "phase": "movement", "validated_orders": []},
    )
    _write_json(root / "leaderboard" / "leaderboard.json", {"schema_version": "1.0"})
    _write_json(root / "metrics" / "cost_log.json", {"schema_version": "1.0", "agent_id": "a1"})

    report = ArtifactValidator(schema_version="1.0").validate_experiment_dir(root)
    cats = _error_categories(report)
    assert report.ok is False
    assert "invalid_json" in cats
    assert "schema_version_mismatch" in cats
    assert "invalid_jsonl" in cats
    assert "missing_fields" in cats


def test_compare_experiment_dirs_detects_missing_and_content_differences(tmp_path) -> None:
    cfg_path = _config(tmp_path, "exp_cmp")
    cfg = load_experiment_config(cfg_path)
    runner_a_root = tmp_path / "a"
    runner_b_root = tmp_path / "b"
    ExperimentRunner(config=cfg, base_dir=runner_a_root).run()
    ExperimentRunner(config=cfg, base_dir=runner_b_root).run()
    exp_a = runner_a_root / "experiments" / cfg.experiment_id
    exp_b = runner_b_root / "experiments" / cfg.experiment_id

    # changed message ordering detection
    global_file = next(exp_b.rglob("global.jsonl"))
    lines = [line for line in global_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) >= 2:
        global_file.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    # changed manifest payload content for guaranteed content mismatch.
    summary = exp_b / "run_summary.json"
    summary_payload = json.loads(summary.read_text(encoding="utf-8"))
    summary_payload["games_completed"] = 0
    summary.write_text(json.dumps(summary_payload, sort_keys=True), encoding="utf-8")

    # missing artifact detection
    manifest = exp_b / "manifest.json"
    manifest.unlink()

    result = compare_experiment_dirs(exp_a, exp_b)
    assert result.deterministic is False
    assert any("missing_in_" in diff and "manifest.json" in diff for diff in result.differences)
    assert any("content_mismatch" in diff for diff in result.differences)


def test_compare_experiment_dirs_normalizes_nondeterministic_fields(tmp_path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    _write_json(
        left / "manifest.json",
        {"schema_version": "1.0", "experiment_id": "x", "games": 1, "agents": [], "seed": 1},
    )
    _write_json(
        right / "manifest.json",
        {"schema_version": "1.0", "experiment_id": "x", "games": 1, "agents": [], "seed": 1},
    )
    (left / "events.jsonl").write_text(
        '{"schema_version":"1.0","event_id":"a","timestamp":"t1","event_type":"x","experiment_id":"e","game_id":"g","payload":{}}\n',
        encoding="utf-8",
    )
    (right / "events.jsonl").write_text(
        '{"schema_version":"1.0","event_id":"b","timestamp":"t2","event_type":"x","experiment_id":"e","game_id":"g","payload":{}}\n',
        encoding="utf-8",
    )
    result = compare_experiment_dirs(left, right)
    assert result.deterministic is True


def test_compare_experiment_dirs_detects_message_order_and_resolution_output_changes(tmp_path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    (left / "logs").mkdir(parents=True)
    (right / "logs").mkdir(parents=True)
    (left / "resolution").mkdir(parents=True)
    (right / "resolution").mkdir(parents=True)

    _write_json(
        left / "manifest.json",
        {"schema_version": "1.0", "experiment_id": "x", "games": 1, "agents": [], "seed": 1},
    )
    _write_json(
        right / "manifest.json",
        {"schema_version": "1.0", "experiment_id": "x", "games": 1, "agents": [], "seed": 1},
    )

    left_msgs = [
        {
            "schema_version": "1.0",
            "message_id": "m1",
            "experiment_id": "x",
            "game_id": "g",
            "phase_id": "p",
            "tick": 1,
            "from_agent_id": "a1",
            "to": {"type": "global"},
            "body": "one",
            "created_at": "t",
        },
        {
            "schema_version": "1.0",
            "message_id": "m2",
            "experiment_id": "x",
            "game_id": "g",
            "phase_id": "p",
            "tick": 1,
            "from_agent_id": "a2",
            "to": {"type": "global"},
            "body": "two",
            "created_at": "t",
        },
    ]
    (left / "logs" / "global.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in left_msgs) + "\n",
        encoding="utf-8",
    )
    (right / "logs" / "global.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in reversed(left_msgs)) + "\n",
        encoding="utf-8",
    )

    _write_json(
        left / "resolution" / "resolution.json",
        {
            "schema_version": "1.0",
            "phase": "movement",
            "validated_orders": [],
            "movement_outcomes": [{"unit_id": "u1", "result": "held"}],
            "output_state": {"schema_version": "1.0", "units": []},
        },
    )
    _write_json(
        right / "resolution" / "resolution.json",
        {
            "schema_version": "1.0",
            "phase": "movement",
            "validated_orders": [],
            "movement_outcomes": [{"unit_id": "u1", "result": "moved"}],
            "output_state": {"schema_version": "1.0", "units": []},
        },
    )

    result = compare_experiment_dirs(left, right)
    assert result.deterministic is False
    assert any("global.jsonl" in diff for diff in result.differences)
    assert any("resolution.json" in diff for diff in result.differences)
