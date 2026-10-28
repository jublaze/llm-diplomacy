from __future__ import annotations

import json
from pathlib import Path

import yaml

from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config


def _write_cfg(
    path: Path,
    *,
    experiment_id: str,
    provider: dict[str, object] | None = None,
    budget: dict[str, object] | None = None,
    game_count: int = 1,
) -> None:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "agent_state_mode": "fresh",
        "agents": [
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
        ],
        "runtime": {
            "kind": "baseline_llm",
            "provider": "fake",
            "max_ticks": 1,
        },
        "games": {
            "count": game_count,
            "max_year": 1901,
            "country_assignment": "fixed",
            "seed": 9,
        },
        "outputs": {"observatory": True, "summary": True},
    }
    if provider is not None:
        payload["runtime"] = {
            "kind": "baseline_llm",
            "provider": str(provider.get("kind", "fake")),
            "max_ticks": 1,
            "provider_config": provider,
        }
    if budget is not None:
        payload["budget"] = budget
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def test_dry_run_writes_report_and_executes_zero_calls(tmp_path) -> None:
    cfg_path = tmp_path / "dry_run.yaml"
    _write_cfg(cfg_path, experiment_id="pilot_dry", game_count=2)
    cfg = load_population_experiment_config(cfg_path)
    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run(
        dry_run_override=True
    )
    assert result.dry_run_report_path is not None
    report = json.loads(result.dry_run_report_path.read_text(encoding="utf-8"))
    assert report["provider_calls_executed"] == 0
    assert report["estimated_max_provider_calls"] > 0
    assert not (tmp_path / "experiments" / "pilot_dry" / "games").exists()


def test_budget_stop_experiment_stops_after_first_game(tmp_path) -> None:
    cfg_path = tmp_path / "budget_stop.yaml"
    _write_cfg(
        cfg_path,
        experiment_id="pilot_stop_exp",
        game_count=3,
        budget={"max_provider_calls": 0, "stop_policy": "stop_experiment"},
    )
    cfg = load_population_experiment_config(cfg_path)
    PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    exp_root = tmp_path / "experiments" / "pilot_stop_exp"
    run_summary = json.loads((exp_root / "run_summary.json").read_text(encoding="utf-8"))
    assert run_summary["games_completed"] == 0
    assert run_summary["games_stopped"] == 1
    assert run_summary["games_started"] == 1
    budget_state = json.loads((exp_root / "metrics" / "budget" / "budget_state.json").read_text(encoding="utf-8"))
    assert budget_state["stop_reason"] == "max_provider_calls"
    assert budget_state["stop_experiment"] is True


def test_budget_switch_to_fallback_continues_and_records_budget_fallback(tmp_path) -> None:
    cfg_path = tmp_path / "budget_switch.yaml"
    _write_cfg(
        cfg_path,
        experiment_id="pilot_switch",
        game_count=1,
        budget={"max_provider_calls": 0, "stop_policy": "switch_to_fallback"},
    )
    cfg = load_population_experiment_config(cfg_path)
    PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    exp_root = tmp_path / "experiments" / "pilot_switch"
    files = list(exp_root.rglob("phase_outputs/*.json"))
    assert files
    saw_budget_fallback = False
    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        llm_meta = payload.get("llm_meta", {})
        if isinstance(llm_meta, dict) and llm_meta.get("provider_model") == "budget_fallback":
            saw_budget_fallback = True
            break
    assert saw_budget_fallback is True
    run_summary = json.loads((exp_root / "run_summary.json").read_text(encoding="utf-8"))
    assert run_summary["games_completed"] == 1


def test_missing_api_key_falls_back_and_logs_provider_boot_error(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg_path = tmp_path / "missing_key.yaml"
    _write_cfg(
        cfg_path,
        experiment_id="pilot_missing_key",
        provider={
            "kind": "openai_compatible",
            "model": "gpt-4.1-mini",
            "timeout_seconds": 5,
        },
        game_count=1,
    )
    cfg = load_population_experiment_config(cfg_path)
    PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    exp_root = tmp_path / "experiments" / "pilot_missing_key"
    provider_errors = exp_root / "games" / "game_001" / "logs" / "provider_errors.jsonl"
    assert provider_errors.exists()
    assert "Missing required API key" in provider_errors.read_text(encoding="utf-8")


def test_pilot_report_is_generated_with_guardrail_fields(tmp_path) -> None:
    cfg_path = tmp_path / "pilot_report.yaml"
    _write_cfg(
        cfg_path,
        experiment_id="pilot_report",
        game_count=1,
        budget={"max_provider_calls": 1, "stop_policy": "switch_to_fallback"},
    )
    cfg = load_population_experiment_config(cfg_path)
    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    assert result.pilot_report_json_path is not None
    assert result.pilot_report_md_path is not None
    report = json.loads(result.pilot_report_json_path.read_text(encoding="utf-8"))
    assert "provider_calls" in report
    assert "fallback_count" in report
    assert "parse_failure_count" in report
    assert "budget_stop_reason" in report
    assert "largest_prompt" in report
