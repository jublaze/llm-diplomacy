from __future__ import annotations

import json
from pathlib import Path

import yaml

from llm_diplomacy.observatory.api import load_game, summarize_diplomacy_metrics
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config


def test_example_multi_llm_configs_load() -> None:
    for rel in [
        "config/examples/two_llm_adjacent_pilot.yaml",
        "config/examples/three_llm_balkans_pilot.yaml",
        "config/examples/seven_llm_tiny_pilot.yaml",
    ]:
        cfg = load_population_experiment_config(Path(rel))
        assert cfg.experiment_id
        assert cfg.runtime_kind == "baseline_llm"
        assert isinstance(cfg.raw.get("runtime"), dict)


def test_dry_run_estimate_increases_with_llm_count(tmp_path) -> None:
    reports = []
    for rel in [
        "config/examples/two_llm_adjacent_pilot.yaml",
        "config/examples/three_llm_balkans_pilot.yaml",
        "config/examples/seven_llm_tiny_pilot.yaml",
    ]:
        cfg = load_population_experiment_config(Path(rel))
        result = PopulationExperimentRunner(
            config=cfg,
            base_dir=tmp_path / cfg.experiment_id,
            agents_root=tmp_path / "agents",
        ).run(dry_run_override=True)
        report = json.loads(result.dry_run_report_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        inputs = report["inputs"]
        years = max(1, int(inputs["max_year"]) - 1900)
        games = max(1, int(inputs["games"]))
        normalized = report["estimated_max_provider_calls"] / (games * years)
        reports.append(normalized)
    assert reports[0] < reports[1] < reports[2]


def test_diplomacy_metrics_count_global_private_and_pairs(tmp_path) -> None:
    game_dir = tmp_path / "game"
    (game_dir / "logs").mkdir(parents=True, exist_ok=True)
    (game_dir / "messages" / "global").mkdir(parents=True, exist_ok=True)
    (game_dir / "messages" / "private" / "a1").mkdir(parents=True, exist_ok=True)
    (game_dir / "messages" / "private" / "a2").mkdir(parents=True, exist_ok=True)
    events = [
        {
            "schema_version": "1.0",
            "event_type": "phase_completed",
            "phase_id": "game_end",
            "event_id": "e1",
            "payload": {
                "result": {
                    "country_results": {
                        "austria": {"agent_id": "a1"},
                        "italy": {"agent_id": "a2"},
                    }
                }
            },
        }
    ]
    (game_dir / "logs" / "events.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in events) + "\n", encoding="utf-8"
    )
    global_rows = [
        {
            "schema_version": "1.0",
            "message_id": "g1",
            "phase_id": "1901_spring_negotiation",
            "tick": 1,
            "from_agent_id": "a1",
            "to": {"type": "global"},
            "body": "signal",
        }
    ]
    (game_dir / "messages" / "global" / "global.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in global_rows) + "\n", encoding="utf-8"
    )
    private_rows = [
        {
            "schema_version": "1.0",
            "message_id": "p1",
            "phase_id": "1901_spring_negotiation",
            "tick": 1,
            "from_agent_id": "a1",
            "to": {"type": "private", "to_agent_id": "a2"},
            "body": "ally?",
        },
        {
            "schema_version": "1.0",
            "message_id": "p2",
            "phase_id": "1901_spring_negotiation",
            "tick": 2,
            "from_agent_id": "a2",
            "to": {"type": "private", "to_agent_id": "a1"},
            "body": "yes",
        },
    ]
    (game_dir / "messages" / "private" / "a1" / "inbox.jsonl").write_text(
        json.dumps(private_rows[1], sort_keys=True) + "\n", encoding="utf-8"
    )
    (game_dir / "messages" / "private" / "a2" / "inbox.jsonl").write_text(
        json.dumps(private_rows[0], sort_keys=True) + "\n", encoding="utf-8"
    )
    data = load_game(game_dir)
    metrics = summarize_diplomacy_metrics(data)
    assert metrics["global_messages_sent"] == 1
    assert metrics["private_messages_sent"] == 2
    assert metrics["private_messages_by_pair"]["a1__a2"] == 2
    assert "a1__a2" in metrics["private_pairs_with_replies"]
    agent_rows = {row["agent_id"]: row for row in metrics["per_agent"]}
    assert agent_rows["a1"]["unique_private_contacts"] == 1
    assert agent_rows["a1"]["private_replies_received"] == 1
    quality = metrics.get("conversation_quality_heuristics", {})
    assert isinstance(quality, dict)
    assert quality.get("heuristic") is True
    assert "concrete_support_requests" in quality
    assert "repeated_confirmation_loops" in quality


def test_conversation_quality_metrics_detect_repeated_confirmation_loops(tmp_path) -> None:
    game_dir = tmp_path / "game_quality"
    (game_dir / "logs").mkdir(parents=True, exist_ok=True)
    (game_dir / "messages" / "global").mkdir(parents=True, exist_ok=True)
    (game_dir / "messages" / "private" / "a1").mkdir(parents=True, exist_ok=True)
    (game_dir / "messages" / "private" / "a2").mkdir(parents=True, exist_ok=True)
    events = [
        {
            "schema_version": "1.0",
            "event_type": "phase_completed",
            "phase_id": "game_end",
            "event_id": "e1",
            "payload": {
                "result": {
                    "country_results": {
                        "austria": {"agent_id": "a1"},
                        "italy": {"agent_id": "a2"},
                    }
                }
            },
        }
    ]
    (game_dir / "logs" / "events.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in events) + "\n", encoding="utf-8"
    )
    (game_dir / "messages" / "global" / "global.jsonl").write_text("", encoding="utf-8")
    a1_rows = [
        {
            "schema_version": "1.0",
            "message_id": "p1",
            "phase_id": "1901_spring_negotiation",
            "tick": 1,
            "from_agent_id": "a1",
            "to": {"type": "private", "to_agent_id": "a2"},
            "body": "Let's continue building trust and stay coordinated.",
        }
    ]
    a2_rows = [
        {
            "schema_version": "1.0",
            "message_id": "p2",
            "phase_id": "1901_spring_negotiation",
            "tick": 2,
            "from_agent_id": "a2",
            "to": {"type": "private", "to_agent_id": "a1"},
            "body": "Agreed, let's continue and stay coordinated.",
        }
    ]
    (game_dir / "messages" / "private" / "a1" / "inbox.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in a1_rows) + "\n", encoding="utf-8"
    )
    (game_dir / "messages" / "private" / "a2" / "inbox.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in a2_rows) + "\n", encoding="utf-8"
    )
    data = load_game(game_dir)
    metrics = summarize_diplomacy_metrics(data)
    quality = metrics.get("conversation_quality_heuristics", {})
    assert isinstance(quality, dict)
    assert int(quality.get("messages_with_repeated_confirmation_language", 0)) >= 1


def test_reports_generated_include_diplomacy_metrics_and_bilateral_section(tmp_path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    payload = {
        "schema_version": "1.0",
        "experiment_id": "multi_llm_reports",
        "agent_state_mode": "fresh",
        "agents": [
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
        ],
        "runtime": {
            "kind": "baseline_llm",
            "provider": "fake",
            "max_ticks": 1,
            "llm_agent_ids": ["cassandra", "dorian"],
            "provider_config": {"kind": "fake"},
        },
        "games": {"count": 1, "max_year": 1901, "country_assignment": "fixed", "seed": 11},
        "budget": {"max_provider_calls": 200, "stop_policy": "stop_experiment"},
        "outputs": {"observatory": True, "summary": True},
    }
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    cfg = load_population_experiment_config(cfg_path)
    result = PopulationExperimentRunner(
        config=cfg,
        base_dir=tmp_path,
        agents_root=tmp_path / "agents",
    ).run()
    pilot = json.loads(result.pilot_report_json_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert "bilateral_diplomacy_analysis" in pilot
    assert "private_messages_occurred" in pilot["bilateral_diplomacy_analysis"]
    obs_root = result.experiment_root / "games" / "game_001" / "observatory"
    assert (obs_root / "diplomacy_metrics.json").exists()
    assert (obs_root / "diplomacy_metrics.md").exists()
    assert (obs_root / "conversations" / "index.md").exists()
    assert (result.experiment_root / "belief_evidence_observability_summary.json").exists()
    assert (result.experiment_root / "belief_evidence_observability_summary.md").exists()


def test_budget_stop_prevents_runaway_multi_agent_calls(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_budget.yaml"
    payload = {
        "schema_version": "1.0",
        "experiment_id": "multi_llm_budget_stop",
        "agent_state_mode": "fresh",
        "agents": [
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
            str((Path("blueprints") / "marcellus.yaml").resolve()),
        ],
        "runtime": {
            "kind": "baseline_llm",
            "provider": "fake",
            "max_ticks": 1,
            "llm_agent_ids": ["cassandra", "dorian", "marcellus"],
            "provider_config": {"kind": "fake"},
        },
        "games": {"count": 3, "max_year": 1901, "country_assignment": "fixed", "seed": 17},
        "budget": {"max_provider_calls": 0, "stop_policy": "stop_experiment"},
        "outputs": {"observatory": True, "summary": True},
    }
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    cfg = load_population_experiment_config(cfg_path)
    PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    run_summary = json.loads(
        (tmp_path / "experiments" / "multi_llm_budget_stop" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert run_summary["games_completed"] == 0
    assert run_summary["games_stopped"] == 1
