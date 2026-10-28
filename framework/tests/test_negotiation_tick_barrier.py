from __future__ import annotations

import json

import pytest

from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.gm.game_master import GameMaster


def _build_gm(tmp_path) -> GameMaster:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_barrier",
        "games": 1,
        "agents": [{"agent_id": "a1"}, {"agent_id": "a2"}],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 2},
        "seed": 3,
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return GameMaster(config=load_experiment_config(cfg_path), base_dir=tmp_path, game_id="g1")


def test_tick_barrier_blocks_early_release(tmp_path) -> None:
    gm = _build_gm(tmp_path)
    phase = "1901_spring_negotiation"
    gm.stage_negotiation_tick_outputs(
        phase_id=phase,
        tick_index=1,
        agent_id="a1",
        messages=[{"message_id": "m1", "to": {"type": "global"}, "body": "hi"}],
    )

    with pytest.raises(ValueError):
        gm.commit_negotiation_tick(phase_id=phase, tick_index=1, active_agent_ids=["a1", "a2"])


def test_tick_release_happens_after_barrier(tmp_path) -> None:
    gm = _build_gm(tmp_path)
    phase = "1901_spring_negotiation"
    gm.stage_negotiation_tick_outputs(
        phase_id=phase,
        tick_index=1,
        agent_id="a1",
        messages=[{"message_id": "m1", "to": {"type": "global"}, "body": "g"}],
    )
    gm.stage_negotiation_tick_outputs(
        phase_id=phase,
        tick_index=1,
        agent_id="a2",
        messages=[{"message_id": "m2", "to": {"type": "private", "to_agent_id": "a1"}, "body": "p"}],
    )

    committed = gm.commit_negotiation_tick(phase_id=phase, tick_index=1, active_agent_ids=["a1", "a2"])
    assert len(committed) == 2
    assert gm.paths.global_channel_file.exists()
    global_rows = gm.paths.global_channel_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(global_rows) == 1

    inbox = gm.paths.private_messages_dir / "a1" / "inbox.jsonl"
    assert inbox.exists()
    inbox_rows = inbox.read_text(encoding="utf-8").strip().splitlines()
    assert len(inbox_rows) == 1


def test_invalid_private_target_is_ignored_not_crash(tmp_path) -> None:
    gm = _build_gm(tmp_path)
    phase = "1901_spring_negotiation"
    gm.stage_negotiation_tick_outputs(
        phase_id=phase,
        tick_index=1,
        agent_id="a1",
        messages=[{"message_id": "m1", "to": {"type": "private", "to_agent_id": "private:a2"}, "body": "x"}],
    )
    gm.stage_negotiation_tick_outputs(
        phase_id=phase,
        tick_index=1,
        agent_id="a2",
        messages=[{"message_id": "m2", "to": {"type": "global"}, "body": "g"}],
    )
    committed = gm.commit_negotiation_tick(phase_id=phase, tick_index=1, active_agent_ids=["a1", "a2"])
    assert len(committed) == 2
    bad_inbox = gm.paths.private_messages_dir / "private:a2" / "inbox.jsonl"
    assert not bad_inbox.exists()


def test_1906_fall_retreat_elimination_excludes_agent_from_winter_barrier_without_synthetic_stage(tmp_path) -> None:
    """Regression: the old roster barrier demanded Austria/Helena after final-unit destruction."""
    gm = _build_gm(tmp_path)
    phase = "1906_winter_negotiation"
    survivors = ["a1", "a2", "a3", "a4", "a5", "a6"]
    for agent_id in survivors:
        gm.stage_negotiation_tick_outputs(
            phase_id=phase,
            tick_index=1,
            agent_id=agent_id,
            messages=[{"message_id": f"m_{agent_id}", "to": {"type": "global"}, "body": "normal committed message"}],
        )
    committed = gm.commit_negotiation_tick(phase_id=phase, tick_index=1, active_agent_ids=survivors)
    assert len(committed) == 6
    assert not (gm.paths.outbox_staging_dir / phase / "1" / "a7.jsonl").exists()


def test_eliminated_private_recipient_is_not_committed(tmp_path) -> None:
    gm = _build_gm(tmp_path)
    phase = "1906_winter_negotiation"
    gm.stage_negotiation_tick_outputs(
        phase_id=phase, tick_index=1, agent_id="a1",
        messages=[{"message_id": "old_target", "to": {"type": "private", "to_agent_id": "a2"}, "body": "do not route"}],
    )
    assert gm.commit_negotiation_tick(phase_id=phase, tick_index=1, active_agent_ids=["a1"]) == []
    assert not (gm.paths.private_messages_dir / "a2" / "inbox.jsonl").exists()


def test_normal_seven_agent_barrier_commits_all_staged_outputs_and_routes_messages(tmp_path) -> None:
    gm = _build_gm(tmp_path)
    phase = "1902_spring_negotiation"
    agents = [f"a{index}" for index in range(1, 8)]
    for agent_id in agents:
        target = {"type": "private", "to_agent_id": "a1"} if agent_id == "a2" else {"type": "global"}
        gm.stage_negotiation_tick_outputs(
            phase_id=phase, tick_index=1, agent_id=agent_id,
            messages=[{"message_id": f"full_{agent_id}", "to": target, "body": "normal"}],
        )
    committed = gm.commit_negotiation_tick(phase_id=phase, tick_index=1, active_agent_ids=agents)
    assert len(committed) == 7
    assert (gm.paths.private_messages_dir / "a1" / "inbox.jsonl").exists()
