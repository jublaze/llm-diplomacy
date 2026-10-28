from __future__ import annotations

import json
from pathlib import Path

from llm_diplomacy.agents import AgentContext, BaselineLLMAgentRuntime, ScriptedAgentRuntime
from llm_diplomacy.agents.baseline_llm_runtime import PERSISTENCE_HANDOFF_VERSION
from llm_diplomacy.agents.llm_provider import FakeLLMProvider
from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.factory import AgentFactory
from llm_diplomacy.runner import ScriptedExperimentRunner
from llm_diplomacy.gm.game_master import GameMaster
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def _context(agent_id: str, game_id: str, country: str) -> AgentContext:
    return AgentContext(
        schema_version="1.0",
        experiment_id="batch_a",
        game_id=game_id,
        phase_id="game_end_cleanup",
        agent_id=agent_id,
        country_assignment=country,
        visible_state={"game_id": game_id, "year": 1901, "season": "winter", "phase": "adjustment", "units": [], "supply_ownership": {}},
    )


def test_cleanup_promotes_historical_memory_for_next_reuse_prompt(tmp_path: Path) -> None:
    source = tmp_path / "canonical_agents"
    AgentFactory(schema_version="1.0").create_agent(Path("blueprints/major.yaml"), agents_root=source)
    provider = FakeLLMProvider(
        responses_by_method={
            "game_end_cleanup": json.dumps(
                {
                    "persistent_memory_update": "Shaver, playing Russia in Game 1, broke a concrete Bulgaria/Serbia arrangement after reciprocal cooperation.",
                    "public_profile_update": None,
                }
            )
        }
    )
    game_one = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "game_001_agents", provider=provider, source_folio_root=source)
    game_one.register_agent(agent_id="major")
    game_one.game_end_cleanup(_context("major", "game_001", "turkey"))

    canonical = (source / "major" / "persistent_memory.md").read_text(encoding="utf-8")
    assert "## Prior-game historical evidence" in canonical
    assert "Source game: game_001" in canonical
    assert "Current-game facts override this memory" in canonical
    assert "Shaver, playing Russia in Game 1" in canonical

    game_two = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "game_002_agents", provider=FakeLLMProvider(), source_folio_root=source)
    game_two.register_agent(agent_id="major")
    ctx = _context("major", "game_002", "england")
    built = game_two.context_builder.build(ctx)
    for method in ("reflect", "negotiate_tick", "submit_orders"):
        prompt = game_two.context_builder.build_prompt(method=method, built_context=built)
        assert "Shaver, playing Russia in Game 1" in prompt
        assert "Current-game facts override this memory" in prompt


def test_elimination_is_unit_based_and_lifecycle_records_are_machine_readable(tmp_path: Path) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="batch_a", games=1, agents=[], rule_engine={}, negotiation={}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    runner._reset_game_stats()
    runner._record_new_eliminations(
        game_id="game_001",
        phase_id="1902_winter_adjustment",
        state={"units": [{"owner": "england", "status": "active"}], "supply_ownership": {"lon": "france"}},
        assignments={"england": "major", "france": "shaver"},
    )
    assert "france" in runner._eliminated_by_country
    assert "england" not in runner._eliminated_by_country
    record = runner._eliminated_by_country["france"]
    assert record["elimination_phase_id"] == "1902_winter_adjustment"
    assert record["reason"] == "authoritative_adjudicated_state_zero_active_units"
    assert record["legacy_artifact"] is False
    assert runner._agent_is_eliminated(agent_id="shaver", assignments={"england": "major", "france": "shaver"})


def test_retreated_unit_remains_active_until_destroyed_then_changes_barrier_participants(tmp_path: Path) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="batch_lifecycle", games=1, agents=[], rule_engine={}, negotiation={}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    runner._reset_game_stats()
    assignments = {"austria": "helena", "england": "other"}
    runner._record_new_eliminations(
        game_id="game_001", phase_id="1906_fall_movement",
        state={"units": [{"owner": "austria", "status": "dislodged", "must_retreat": True}, {"owner": "england", "status": "active"}]}, assignments=assignments,
    )
    assert runner._active_runtime_participants(assignments=assignments) == ["helena", "other"]
    runner._record_new_eliminations(
        game_id="game_001", phase_id="1906_fall_retreat",
        state={"units": [{"owner": "austria", "status": "destroyed"}, {"owner": "england", "status": "active"}]}, assignments=assignments,
    )
    assert runner._active_runtime_participants(assignments=assignments) == ["other"]
    assert runner._eliminated_by_country["austria"]["elimination_phase_id"] == "1906_fall_retreat"


def test_zero_centers_with_active_unit_remains_scheduled_for_barrier(tmp_path: Path) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="zero_centers", games=1, agents=[], rule_engine={}, negotiation={}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    runner._reset_game_stats()
    assignments = {"austria": "helena", "england": "other"}
    runner._record_new_eliminations(
        game_id="game_001", phase_id="1905_fall_retreat",
        state={"units": [{"owner": "austria", "status": "active"}, {"owner": "england", "status": "active"}], "supply_ownership": {"lon": "england"}},
        assignments=assignments,
    )
    assert "austria" not in runner._eliminated_by_country  # zero centers is not zero units
    assert runner._active_runtime_participants(assignments=assignments) == ["helena", "other"]


def test_multiple_eliminations_reduce_barrier_to_one_survivor_without_synthetic_output(tmp_path: Path) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="reduced", games=1, agents=[], rule_engine={}, negotiation={}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    runner._reset_game_stats()
    assignments = {"austria": "helena", "england": "eng", "france": "fra"}
    runner._record_new_eliminations(
        game_id="game_001", phase_id="1906_winter_adjustment",
        state={"units": [{"owner": "england", "status": "active"}]}, assignments=assignments,
    )
    assert set(runner._eliminated_by_country) == {"austria", "france"}
    assert runner._active_runtime_participants(assignments=assignments) == ["eng"]


def test_elimination_state_is_reset_for_same_agent_in_game_two(tmp_path: Path) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="two_games", games=2, agents=[], rule_engine={}, negotiation={}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    assignments_one = {"austria": "helena", "england": "other"}
    runner._reset_game_stats()
    runner._record_new_eliminations(game_id="game_001", phase_id="1903_fall_retreat", state={"units": [{"owner": "england", "status": "active"}]}, assignments=assignments_one)
    assert runner._active_runtime_participants(assignments=assignments_one) == ["other"]
    runner._reset_game_stats()  # ScriptedExperimentRunner invokes this at every new game.
    assignments_two = {"turkey": "helena", "england": "other"}
    assert runner._active_runtime_participants(assignments=assignments_two) == ["helena", "other"]
    assert runner._eliminated_by_country == {}


def test_post_elimination_negotiation_has_no_runtime_artifact_or_synthetic_stage(tmp_path: Path) -> None:
    engine = DiplomacyRuleEngine()
    powers = list(engine.map_def.powers)
    agents = [f"agent_{power}" for power in powers]
    config = ExperimentConfig(schema_version="1.0", experiment_id="post_elimination", games=1, agents=[{"agent_id": agent} for agent in agents], rule_engine={}, negotiation={"max_ticks": 1}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path, negotiation_ticks=1)
    runner._reset_game_stats()
    gm = GameMaster(config=config, base_dir=tmp_path, game_id="game_001")
    scripted = ScriptedAgentRuntime(schema_version="1.0", runtime_root=gm.paths.game_root / "agents", map_def=engine.map_def, seed=1)
    for agent in agents:
        scripted.register_agent(agent_id=agent, kind="hold")
    assignments = {power: f"agent_{power}" for power in powers}
    runner._record_new_eliminations(
        game_id="game_001", phase_id="1906_fall_retreat",
        state={"units": [{"owner": power, "status": "active"} for power in powers if power != "austria"]}, assignments=assignments,
    )
    llm = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=gm.paths.game_root / "agents", provider=FakeLLMProvider())
    state = {"game_id": "game_001", "year": 1906, "season": "winter", "phase": "adjustment", "units": [{"owner": power, "status": "active"} for power in powers if power != "austria"], "supply_ownership": {}, "private_messages": [], "global_messages": []}
    runner._run_negotiation_tick(gm=gm, scripted_runtime=scripted, llm_runtime=llm, runtime_by_agent={agent: "scripted" for agent in agents}, assignments=assignments, state=state, phase_id="1906_winter_negotiation")
    helena = assignments["austria"]
    assert not list((gm.paths.game_root / "agents" / helena / "runtime" / "phase_inputs").glob("1906_winter_negotiation_*"))
    assert not (gm.paths.outbox_staging_dir / "1906_winter_negotiation" / "1" / f"{helena}.jsonl").exists()
    for power in powers:
        if power != "austria":
            assert list((gm.paths.game_root / "agents" / assignments[power] / "runtime" / "phase_inputs").glob("1906_winter_negotiation_*"))
    assert runner._eliminated_by_country["austria"]["live_provider_calls_skipped"] == 1


def test_major_profile_refinement_is_generated_into_normal_prompts(tmp_path: Path) -> None:
    root = tmp_path / "agents"
    AgentFactory(schema_version="1.0").create_agent(Path("blueprints/major.yaml"), agents_root=root)
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "runtime", provider=FakeLLMProvider(), source_folio_root=root)
    runtime.register_agent(agent_id="major")
    built = runtime.context_builder.build(_context("major", "game_001", "italy"))
    for method in ("reflect", "negotiate_tick", "submit_orders"):
        prompt = runtime.context_builder.build_prompt(method=method, built_context=built)
        assert "recipient-tailored, legal, coordinate-specific" in prompt
        assert "no safer route is available" in prompt
    blueprint_text = Path("blueprints/major.yaml").read_text(encoding="utf-8")
    assert "England" not in blueprint_text and "Germany" not in blueprint_text
    assert PERSISTENCE_HANDOFF_VERSION == "persistent_memory_handoff_v1_promoted_canonical_folio"
