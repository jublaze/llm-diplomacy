from __future__ import annotations

import json

from llm_diplomacy.agents.contracts import AgentContext, CleanupResult, OrderSet, ReflectionResult, TickResult
from llm_diplomacy.agents.runtime import AgentRuntime
from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.gm.game_master import GameMaster
from llm_diplomacy.metrics.cost import CostTracker
from llm_diplomacy.metrics.leaderboard import Leaderboard
from llm_diplomacy.rules.base import RuleEngine


def test_interfaces_import_cleanly() -> None:
    assert RuleEngine is not None
    assert AgentRuntime is not None
    assert TickResult is not None
    assert OrderSet is not None
    assert ReflectionResult is not None
    assert CleanupResult is not None


def test_game_master_initializes_minimal_game_directory(tmp_path) -> None:
    config_data = {
        "schema_version": "1.0",
        "experiment_id": "exp_001",
        "games": 1,
        "agents": [{"agent_id": "agent_alpha"}],
        "rule_engine": {"name": "stub_rule_engine"},
        "negotiation": {"max_ticks": 1},
        "seed": 11,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")
    config = load_experiment_config(config_path)

    gm = GameMaster(config=config, base_dir=tmp_path, game_id="game_001")
    snapshot = gm.initialize_game_state({"schema_version": "1.0", "board": {}})
    event = gm.start_game()

    assert gm.paths.game_root.exists()
    assert gm.paths.events_file.exists()
    assert (gm.paths.snapshots_dir / f"{snapshot.snapshot_id}.json").exists()
    assert event.event_type == "game_started"


def test_stub_services_record_entries() -> None:
    leaderboard = Leaderboard(schema_version="1.0")
    cost_tracker = CostTracker(schema_version="1.0")

    result = leaderboard.record_game_result(
        experiment_id="exp_001", game_id="game_001", payload={"winner": "agent_alpha"}
    )
    usage = cost_tracker.record_usage(
        experiment_id="exp_001",
        game_id="game_001",
        agent_id="agent_alpha",
        phase_id="1901_spring_negotiation",
        usage={"tokens": 123},
    )

    assert result.schema_version == "1.0"
    assert usage.schema_version == "1.0"


def test_agent_context_is_machine_readable() -> None:
    ctx = AgentContext(
        schema_version="1.0",
        experiment_id="exp_001",
        game_id="game_001",
        phase_id="1901_spring_negotiation",
        agent_id="agent_alpha",
        country_assignment="france",
    )
    assert ctx.schema_version == "1.0"
