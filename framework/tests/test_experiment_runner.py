from __future__ import annotations

import json

from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.runner import ExperimentRunner


def test_experiment_runner_executes_batch_and_writes_artifacts(tmp_path) -> None:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_batch",
        "games": 3,
        "agents": [{"agent_id": "a1"}, {"agent_id": "a2"}],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 9,
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    runner = ExperimentRunner(config=load_experiment_config(cfg_path), base_dir=tmp_path)

    summary = runner.run()

    assert summary.games_completed == 3
    assert (tmp_path / "experiments" / "exp_batch" / "manifest.json").exists()
    assert (tmp_path / "experiments" / "exp_batch" / "run_summary.json").exists()
    assert (tmp_path / "experiments" / "exp_batch" / "leaderboard" / "overall.json").exists()
    assert (tmp_path / "experiments" / "exp_batch" / "leaderboard" / "by_country.json").exists()
    assert (tmp_path / "experiments" / "exp_batch" / "metrics" / "cost_summary.json").exists()


def test_experiment_runner_leaderboard_and_cost_summaries_have_schema(tmp_path) -> None:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_summary",
        "games": 1,
        "agents": [{"agent_id": "a1"}],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {},
        "seed": 1,
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    runner = ExperimentRunner(config=load_experiment_config(cfg_path), base_dir=tmp_path)
    runner.run()

    overall = json.loads(
        (tmp_path / "experiments" / "exp_summary" / "leaderboard" / "overall.json").read_text(
            encoding="utf-8"
        )
    )
    cost = json.loads(
        (tmp_path / "experiments" / "exp_summary" / "metrics" / "cost_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert overall["schema_version"] == "1.0"
    assert cost["schema_version"] == "1.0"
