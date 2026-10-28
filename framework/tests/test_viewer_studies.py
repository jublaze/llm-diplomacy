import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_viewer_studies.py"
SPEC = importlib.util.spec_from_file_location("viewer_studies", SCRIPT)
studies = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = studies
SPEC.loader.exec_module(studies)


def _planning_viewer(tmp_path, monkeypatch):
    viewer = tmp_path / "viewer"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    for name in ("validate_schema.ts", "validate_bundle.ts", "build_report_catalog.ts"):
        (scripts / name).write_text("// planning-only tool placeholder\n", encoding="utf-8")
    (viewer / "node_modules" / "typescript").mkdir(parents=True)
    monkeypatch.setattr(studies.shutil, "which", lambda name: "node.exe" if name == "node" else None)
    return viewer


def _copy_study_configs(destination):
    destination.mkdir(parents=True)
    for name in ("viewer_study_general.yaml", "viewer_study_specialist.yaml", "viewer_study_memory_reuse.yaml"):
        shutil.copyfile(ROOT / "config" / "examples" / name, destination / name)
    return destination


def _force_lexical_link(monkeypatch, alias, target):
    alias = Path(os.path.abspath(alias))
    target = Path(target)
    resolve = Path.resolve
    is_link = studies._is_link

    def resolve_alias(path, strict=False):
        if Path(os.path.abspath(path)) == alias:
            return resolve(target, strict=strict)
        return resolve(path, strict=strict)

    def report_alias(path):
        return Path(os.path.abspath(path)) == alias or is_link(path)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    monkeypatch.setattr(studies, "_is_link", report_alias)


def test_all_study_plans_are_paired_and_effectively_fake(tmp_path, monkeypatch):
    output = tmp_path / "new-output"
    viewer = _planning_viewer(tmp_path, monkeypatch)
    _, _, arms = studies.plan_studies(("ticks", "roster", "memory"), output, viewer_root=viewer)
    assert [arm.tag for arm in arms] == ["n1", "n3", "rg", "rs", "mw", "mr", "mf"]
    assert not output.exists()
    assert all(set(arm.canonical_providers) == set(studies.PLAYER_IDS) for arm in arms)
    assert all(provider["kind"] == "fake" for arm in arms for provider in arm.canonical_providers.values())
    assert len({arm.experiment_id for arm in arms}) == 7
    assert studies._planned_destinations(arms).count("agents/memory-treatment") == 1
    assert "agents/mf" in studies._planned_destinations(arms)


def test_roster_pair_rejects_tick_mismatch_before_output_creation(tmp_path, monkeypatch):
    viewer = _planning_viewer(tmp_path, monkeypatch)
    config_dir = _copy_study_configs(tmp_path / "configs")
    specialist_path = config_dir / "viewer_study_specialist.yaml"
    specialist = yaml.safe_load(specialist_path.read_text(encoding="utf-8"))
    specialist["runtime"]["max_ticks"] = 3
    specialist_path.write_text(yaml.safe_dump(specialist, sort_keys=False), encoding="utf-8")
    output = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="roster pair varies beyond its roster intervention"):
        studies.plan_studies(("roster",), output, config_dir=config_dir, viewer_root=viewer)

    assert not output.exists()


@pytest.mark.parametrize("linked_path", ("config_root", "viewer_root", "template", "blueprint"))
def test_study_preflight_rejects_lexical_links_before_output_creation(tmp_path, monkeypatch, linked_path):
    viewer = _planning_viewer(tmp_path, monkeypatch)
    config_dir = _copy_study_configs(tmp_path / "configs")
    viewer_arg = viewer

    if linked_path == "config_root":
        alias = tmp_path / "config-junction"
        alias.mkdir()
        _force_lexical_link(monkeypatch, alias, config_dir)
        config_dir = alias
    elif linked_path == "viewer_root":
        alias = tmp_path / "viewer-junction"
        alias.mkdir()
        _force_lexical_link(monkeypatch, alias, viewer)
        viewer_arg = alias
    elif linked_path == "template":
        template = config_dir / "viewer_study_general.yaml"
        target = config_dir / "general-template-source.yaml"
        target.write_bytes(template.read_bytes())
        _force_lexical_link(monkeypatch, template, target)
    else:
        template = config_dir / "viewer_study_general.yaml"
        config = yaml.safe_load(template.read_text(encoding="utf-8"))
        blueprint_alias = tmp_path / "rowan-blueprint-link.yaml"
        blueprint_alias.write_text("placeholder", encoding="utf-8")
        config["agents"][0]["blueprint"] = str(blueprint_alias)
        template.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        _force_lexical_link(monkeypatch, blueprint_alias, ROOT / "blueprints" / "rowan.yaml")

    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="symlink or junction"):
        studies.plan_studies(("ticks",), output, config_dir=config_dir, viewer_root=viewer_arg)
    assert not output.exists()


def test_nonfake_override_fails_before_output_creation(tmp_path, monkeypatch):
    viewer = _planning_viewer(tmp_path, monkeypatch)
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    for name in ("viewer_study_general.yaml", "viewer_study_specialist.yaml", "viewer_study_memory_reuse.yaml"):
        shutil.copyfile(ROOT / "config" / "examples" / name, config_dir / name)
    text = (config_dir / "viewer_study_general.yaml").read_text(encoding="utf-8")
    text = text.replace("{kind: fake, model: offline-study-fake}", "{kind: openai_compatible, model: live-model}", 1)
    (config_dir / "viewer_study_general.yaml").write_text(text, encoding="utf-8")
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="non-fake provider override"):
        studies.plan_studies(("ticks",), output, config_dir=config_dir, viewer_root=viewer)
    assert not output.exists()


def test_existing_or_linked_output_is_rejected_without_mutation(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        studies.validate_output_root(existing, source_roots=(ROOT,))
    link = tmp_path / "linked"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create symlinks.")
    with pytest.raises(ValueError, match="linked path component"):
        studies.validate_output_root(link / "nested", source_roots=(ROOT,))


def test_memory_visibility_audits_saved_provider_prompts(tmp_path):
    marker = "SYNTHETIC_MEMORY_VISIBILITY_PROBE_TEST"
    for agent_id in studies.PLAYER_IDS:
        path = tmp_path / "games" / "game_001" / "agents" / agent_id / "runtime" / "phase_outputs" / "phase.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"llm_meta": {"prompt": "context " + marker}}), encoding="utf-8")
    observed = studies.audit_memory_prompts(tmp_path, marker=marker, expected_present=True)
    assert set(observed) == set(studies.PLAYER_IDS)
    assert all(row["marker_prompts"] == 1 for row in observed.values())
    with pytest.raises(ValueError, match="leaked into fresh control"):
        studies.audit_memory_prompts(tmp_path, marker=marker, expected_present=False)


def test_memory_control_rejects_marker_absence_from_treatment(tmp_path):
    marker = "SYNTHETIC_MEMORY_VISIBILITY_PROBE_TEST"
    for agent_id in studies.PLAYER_IDS:
        path = tmp_path / "games" / "game_001" / "agents" / agent_id / "runtime" / "phase_outputs" / "phase.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"llm_meta": {"prompt": "ordinary fake prompt"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="absent from every provider prompt"):
        studies.audit_memory_prompts(tmp_path, marker=marker, expected_present=True)


def test_run_command_preserves_nonzero_exit_and_output(monkeypatch):
    command = [sys.executable, "-c", "pass"]

    def fail(command_arg, **kwargs):
        return subprocess.CompletedProcess(command_arg, 7, "stdout sentinel", "stderr sentinel")

    monkeypatch.setattr(studies.subprocess, "run", fail)
    result = studies._run_command(command, cwd=ROOT)
    assert result == {
        "command": command,
        "cwd": str(ROOT),
        "exit_code": 7,
        "stdout": "stdout sentinel",
        "stderr": "stderr sentinel",
    }


@pytest.mark.parametrize(
    ("failure", "expected_error", "expected_stdout", "expected_stderr"),
    [
        (subprocess.TimeoutExpired(["node"], 1, output=b"partial out", stderr=b"partial err"), "TimeoutExpired", "partial out", "partial err"),
        (FileNotFoundError("node was not found"), "FileNotFoundError", "", "node was not found"),
    ],
)
def test_run_command_preserves_timeout_and_start_failures(monkeypatch, failure, expected_error, expected_stdout, expected_stderr):
    command = ["node", "script.ts"]

    def fail(command_arg, **kwargs):
        raise failure

    monkeypatch.setattr(studies.subprocess, "run", fail)
    result = studies._run_command(command, cwd=ROOT, timeout=1)
    assert result["command"] == command
    assert result["cwd"] == str(ROOT)
    assert result["exit_code"] is None
    assert result["execution_error"].startswith(expected_error)
    assert result["stdout"] == expected_stdout
    assert result["stderr"] == expected_stderr


def test_execute_studies_persists_failed_command_before_raising(tmp_path, monkeypatch):
    viewer = _planning_viewer(tmp_path, monkeypatch)
    output = tmp_path / "failed-study"

    def fail(command_arg, **kwargs):
        return subprocess.CompletedProcess(command_arg, 7, "D1_STDOUT_SENTINEL", "D1_STDERR_SENTINEL")

    monkeypatch.setattr(studies.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="command failed with exit 7"):
        studies.execute_studies(("ticks",), output, viewer_root=viewer)

    manifest = json.loads((output / "study_manifest.json").read_text(encoding="utf-8"))
    first_attempt = manifest["arms"][0]["commands"][0]
    assert manifest["status"] == "failed"
    assert first_attempt["exit_code"] == 7
    assert first_attempt["command"][1].endswith("run_population_experiment.py")
    assert first_attempt["cwd"] == str(ROOT)
    assert first_attempt["stdout"] == "D1_STDOUT_SENTINEL"
    assert first_attempt["stderr"] == "D1_STDERR_SENTINEL"


def test_exporter_includes_viewer_study_usage_document():
    export_script = ROOT / "scripts" / "export_publication_candidate.py"
    source = export_script.read_text(encoding="utf-8")
    assert '"docs/VIEWER_EXPERIMENTS.md"' in source


def test_relative_runner_paths_produce_exportable_completed_game(tmp_path):
    config_path = ROOT / "config" / "examples" / "viewer_study_general.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["experiment_id"] = "dtest"
    config_path = tmp_path / "study.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    run_root = tmp_path / "run"
    agents_root = tmp_path / "agents"
    relative_run = os.path.relpath(run_root, ROOT)
    relative_agents = os.path.relpath(agents_root, ROOT)
    command = [
        sys.executable, str(ROOT / "scripts" / "run_population_experiment.py"),
        "--config", str(config_path), "--output-root", relative_run,
        "--agents-root", relative_agents,
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    experiment_root = run_root / "experiments" / "dtest"
    assert json.loads((experiment_root / "run_summary.json").read_text(encoding="utf-8"))["games_completed"] == 1
    bundle_root = tmp_path / "bundle"
    export = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "export_report_bundle.py"),
        "--experiment-root", str(experiment_root), "--output-root", str(bundle_root),
        "--all-games", "--audience", "omniscient_postgame",
    ], cwd=ROOT, capture_output=True, text=True, check=False)
    assert export.returncode == 0, export.stderr
    reports = list(bundle_root.rglob("report.json"))
    assert len(reports) == 1
    metrics = studies._public_metrics(reports[0], experiment_root)
    assert metrics["years_played_range"] == [1901, 1901]
    assert metrics["negotiation_tick_event_count"] == 3
    assert len(metrics["negotiation_ticks_by_phase"]) == 3
    assert metrics["provider_request_attempt_count"] > 0
    assert metrics["provider_request_attempt_count"] == sum(metrics["provider_request_attempts_by_method"].values())
