from __future__ import annotations

import json
from pathlib import Path

from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner


def _scripted_cfg(path: Path, *, experiment_id: str) -> None:
    payload = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "games": 1,
        "agents": [
            {"agent_id": "agent_random", "scripted_kind": "random_legal"},
            {"agent_id": "agent_hold", "scripted_kind": "hold"},
            {"agent_id": "agent_greedy", "scripted_kind": "greedy_center"},
        ],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 17,
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def test_secret_history_artifacts_generated_and_schema(tmp_path) -> None:
    cfg_path = tmp_path / "cfg.json"
    _scripted_cfg(cfg_path, experiment_id="secret_history_basic")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    game_dir = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"
    md_path = game_dir / "reporter" / "secret_history.md"
    json_path = game_dir / "reporter" / "secret_history.json"
    assert md_path.exists()
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    for field in (
        "schema_version",
        "game_id",
        "executive_summary",
        "chronology",
        "turning_points",
        "relationship_summaries",
        "information_events",
        "strategy_evolution",
        "lessons_learned",
    ):
        assert field in payload
    assert payload["schema_version"] == "1.0"
    assert isinstance(payload["chronology"], list)
    assert isinstance(payload["turning_points"], list)
    assert isinstance(payload["relationship_summaries"], list)
    assert isinstance(payload["information_events"], list)
    assert isinstance(payload["strategy_evolution"], list)
    assert isinstance(payload["lessons_learned"], list)

    md = md_path.read_text(encoding="utf-8")
    assert "# Secret History" in md
    assert "## Chronological Narrative" in md
    assert "## Turning Points" in md
    assert "## Relationship Evolution" in md
    assert "## Information Flow" in md
    assert "## Strategy Evolution" in md
    assert "## Lessons Learned" in md


def test_secret_history_deterministic_for_deterministic_runs(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_det.json"
    _scripted_cfg(cfg_path, experiment_id="secret_history_det")
    cfg = load_experiment_config(cfg_path)
    run_a = tmp_path / "run_a"
    run_b = tmp_path / "run_b"
    ScriptedExperimentRunner(config=cfg, base_dir=run_a, max_years=1).run()
    ScriptedExperimentRunner(config=cfg, base_dir=run_b, max_years=1).run()

    a_json = (run_a / "experiments" / cfg.experiment_id / "games" / "game_001" / "reporter" / "secret_history.json").read_text(encoding="utf-8")
    b_json = (run_b / "experiments" / cfg.experiment_id / "games" / "game_001" / "reporter" / "secret_history.json").read_text(encoding="utf-8")
    a_md = (run_a / "experiments" / cfg.experiment_id / "games" / "game_001" / "reporter" / "secret_history.md").read_text(encoding="utf-8")
    b_md = (run_b / "experiments" / cfg.experiment_id / "games" / "game_001" / "reporter" / "secret_history.md").read_text(encoding="utf-8")
    assert a_json == b_json
    assert a_md == b_md


def test_secret_history_is_conservative_on_winner_and_betrayal(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_conservative.json"
    _scripted_cfg(cfg_path, experiment_id="secret_history_conservative")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    payload = json.loads(
        (
            tmp_path
            / "experiments"
            / cfg.experiment_id
            / "games"
            / "game_001"
            / "reporter"
            / "secret_history.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["outcome"]["kind"] == "leader_at_cap"
    betrayal_points = [row for row in payload.get("turning_points", []) if isinstance(row, dict) and row.get("category") == "betrayal"]
    assert betrayal_points == []


def test_secret_history_chronology_sorted_and_linked_from_observatory(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_chrono.json"
    _scripted_cfg(cfg_path, experiment_id="secret_history_chrono")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()
    game_root = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"

    payload = json.loads((game_root / "reporter" / "secret_history.json").read_text(encoding="utf-8"))
    phase_ids = [str(row.get("phase_id", "")) for row in payload.get("chronology", []) if isinstance(row, dict)]
    assert phase_ids == ["1901_spring_movement", "1901_fall_movement", "1901_winter_adjustment"]

    html = (game_root / "observatory" / "index.html").read_text(encoding="utf-8")
    assert "../reporter/secret_history.md" in html
    assert "../reporter/secret_history.json" in html
