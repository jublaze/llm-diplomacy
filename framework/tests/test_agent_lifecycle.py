from __future__ import annotations

import json

from llm_diplomacy.agents.lifecycle import AgentLifecycleStore
from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.gm.game_master import GameMaster


def test_lifecycle_idempotent_cleanup(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / "agents", schema_version="1.0")
    first = store.apply_game_end_cleanup(
        agent_id="agent_alpha",
        game_id="g1",
        payload={"summary": {"result": "draw"}},
    )
    second = store.apply_game_end_cleanup(
        agent_id="agent_alpha",
        game_id="g1",
        payload={"summary": {"result": "draw"}},
    )
    assert first.applied is True
    assert second.applied is False


def test_lifecycle_persists_across_games(tmp_path) -> None:
    store = AgentLifecycleStore(tmp_path / "agents", schema_version="1.0")
    store.apply_game_end_cleanup(agent_id="agent_alpha", game_id="g1", payload={"summary": {"score": 1}})
    store.apply_game_end_cleanup(agent_id="agent_alpha", game_id="g2", payload={"summary": {"score": 2}})

    memory = json.loads((tmp_path / "agents" / "agent_alpha" / "persistent_memory.json").read_text(encoding="utf-8"))
    assert len(memory["game_summaries"]) == 2


def test_game_master_cleanup_integration(tmp_path) -> None:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_cleanup",
        "games": 1,
        "agents": [{"agent_id": "agent_alpha"}],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 1,
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    gm = GameMaster(config=load_experiment_config(cfg_path), base_dir=tmp_path, game_id="game_1")

    result = gm.apply_game_end_cleanup(
        [{"agent_id": "agent_alpha", "payload": {"summary": {"placement": 1}}}]
    )
    assert len(result) == 1
    assert result[0].applied is True
