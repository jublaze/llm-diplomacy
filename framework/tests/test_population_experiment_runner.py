from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest
from unittest.mock import patch

from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.factory import AgentFactory
from llm_diplomacy.agents.contracts import OrderSet, ReflectionResult
from llm_diplomacy.agents.scripted_runtime import ScriptedAgentRuntime
from llm_diplomacy.gm.game_master import GameMaster
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock


def _write_population_config(
    path: Path,
    *,
    experiment_id: str,
    blueprints: list[str],
    agent_state_mode: str | None = None,
    game_count: int = 1,
    max_year: int = 1901,
    seed: int = 123,
) -> None:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "agents": blueprints,
        "runtime": {"kind": "baseline_llm", "provider": "fake"},
        "games": {
            "count": game_count,
            "max_year": max_year,
            "country_assignment": "round_robin",
            "seed": seed,
        },
        "outputs": {"observatory": True, "summary": True},
    }
    if agent_state_mode is not None:
        payload["agent_state_mode"] = agent_state_mode
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def _normalize_summary(summary: dict[str, object]) -> dict[str, object]:
    games = summary.get("games", [])
    normalized_games = []
    if isinstance(games, list):
        for row in games:
            if not isinstance(row, dict):
                continue
            clean = dict(row)
            clean.pop("output_path", None)
            clean.pop("observatory_path", None)
            normalized_games.append(clean)
    clean_summary = dict(summary)
    clean_summary["games"] = normalized_games
    clean_summary["observatory_links"] = []
    clean_summary.pop("started_at", None)
    clean_summary.pop("completed_at", None)
    clean_summary.pop("elapsed_seconds", None)
    return clean_summary


def test_population_config_loader_defaults_agent_state_mode_to_fresh(tmp_path) -> None:
    cfg_path = tmp_path / "population.yaml"
    _write_population_config(
        cfg_path,
        experiment_id="pop_cfg_defaults",
        blueprints=[str((Path("blueprints") / "cassandra.yaml").resolve())],
    )
    cfg = load_population_experiment_config(cfg_path)
    assert cfg.agent_state_mode == "fresh"
    assert cfg.country_assignment == "round_robin"


@pytest.mark.parametrize("budget", [{"max_estimated_cost_usd": "0.01"}, {"max_provider_calls": True}])
def test_population_loader_rejects_invalid_budget_limits(tmp_path, budget) -> None:
    cfg_path = tmp_path / "invalid-budget.yaml"
    _write_population_config(cfg_path, experiment_id="invalid_budget",
        blueprints=[str((Path("blueprints") / "cassandra.yaml").resolve())])
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    payload["budget"] = budget
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_population_experiment_config(cfg_path)


def test_round_robin_assignment_is_deterministic() -> None:
    cfg = ExperimentConfig(
        schema_version="1.0",
        experiment_id="rr_test",
        games=3,
        agents=[{"agent_id": "a1"}, {"agent_id": "a2"}, {"agent_id": "a3"}],
        rule_engine={"name": "diplomacy"},
        negotiation={"max_ticks": 1},
        seed=7,
        raw={"games": {"count": 3, "country_assignment": "round_robin", "seed": 7}},
    )
    runner = ScriptedExperimentRunner(config=cfg, base_dir=Path("."), max_years=1, negotiation_ticks=1)
    game1 = runner._power_assignments(game_index=1)
    game2 = runner._power_assignments(game_index=2)
    game3 = runner._power_assignments(game_index=3)
    first_power = next(iter(game1.keys()))
    assert game1[first_power] == "a1"
    assert game2[first_power] == "a2"
    assert game3[first_power] == "a3"


def test_population_runner_generates_manifest_and_summary(tmp_path) -> None:
    cfg_path = tmp_path / "population.yaml"
    _write_population_config(
        cfg_path,
        experiment_id="pop_basic",
        blueprints=[
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
        ],
        game_count=2,
        max_year=1901,
    )
    cfg = load_population_experiment_config(cfg_path)
    result = PopulationExperimentRunner(
        config=cfg,
        base_dir=tmp_path,
        agents_root=tmp_path / "agents",
    ).run()

    assert result.manifest_path.exists()
    assert result.summary_json_path.exists()
    assert result.summary_md_path.exists()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.0"
    assert manifest["games"]["count"] == 2
    assert len(manifest["agents"]) == 2
    assert manifest["agents"][0]["blueprint_sha256"]

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["games_run"] == 2
    assert len(summary["games"]) == 2
    assert "message_counts" in summary


def test_population_runner_reuse_does_not_overwrite_persistent_memory(tmp_path) -> None:
    agents_root = tmp_path / "agents"
    blueprint_path = Path("blueprints") / "cassandra.yaml"
    agent_dir = AgentFactory(schema_version="1.0").create_agent(blueprint_path, agents_root=agents_root)
    persistent = agent_dir / "persistent_memory.md"
    marker = "KEEP_ME\n"
    persistent.write_text(marker, encoding="utf-8")

    cfg_path = tmp_path / "population_reuse.yaml"
    _write_population_config(
        cfg_path,
        experiment_id="pop_reuse",
        blueprints=[str(blueprint_path.resolve())],
        agent_state_mode="reuse",
        game_count=1,
        max_year=1901,
    )
    cfg = load_population_experiment_config(cfg_path)
    PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=agents_root).run()

    text_after = persistent.read_text(encoding="utf-8")
    assert text_after.startswith(marker)
    assert "## Prior-game historical evidence" in text_after
    assert "Source game: game_001" in text_after
    assert "Current-game facts override this memory" in text_after


def test_population_summary_is_stable_for_fixed_seed(tmp_path) -> None:
    cfg_path = tmp_path / "population_det.yaml"
    _write_population_config(
        cfg_path,
        experiment_id="pop_det",
        blueprints=[
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
        ],
        game_count=2,
        max_year=1901,
        seed=42,
    )
    cfg = load_population_experiment_config(cfg_path)
    run_a = PopulationExperimentRunner(
        config=cfg,
        base_dir=tmp_path / "run_a",
        agents_root=tmp_path / "agents_a",
    ).run()
    run_b = PopulationExperimentRunner(
        config=cfg,
        base_dir=tmp_path / "run_b",
        agents_root=tmp_path / "agents_b",
    ).run()
    summary_a = json.loads(run_a.summary_json_path.read_text(encoding="utf-8"))
    summary_b = json.loads(run_b.summary_json_path.read_text(encoding="utf-8"))
    assert _normalize_summary(summary_a) == _normalize_summary(summary_b)


def test_strict_population_resume_reuses_received_responses_and_publishes_once(tmp_path, monkeypatch) -> None:
    from llm_diplomacy.agents.llm_provider import FakeLLMProvider

    config_path = tmp_path / "resume.yaml"
    blueprints = [str((Path("blueprints") / f"{name}.yaml").resolve())
                  for name in ("cassandra", "dorian", "atlas", "birsan", "dolos", "helena", "iris")]
    _write_population_config(config_path, experiment_id="pop_resume_e2e", blueprints=blueprints,
                             max_year=1901, seed=73)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    payload["runtime"]["llm_agent_ids"] = ["cassandra"]
    payload["budget"] = {"max_provider_calls": 11}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    config = load_population_experiment_config(config_path)

    class InterruptThenFake:
        def __init__(self, *, interrupt=True):
            self.calls = []
            self.interrupt = interrupt
            self.failed = False
            self.fake = FakeLLMProvider()

        def generate(self, request):
            self.calls.append((request.method, request.context.get("attempt_index")))
            if self.interrupt and len(self.calls) == 2 and not self.failed:
                self.failed = True
                raise TimeoutError("mock timeout")
            if request.method == "negotiate_tick":
                from llm_diplomacy.agents.llm_provider import LLMResponse
                return LLMResponse(schema_version="1.0", content=json.dumps({
                    "private_messages": [],
                    "global_messages": [{"body": "Austria will defend its home centers.", "purpose": "public commitment"}],
                    "relationship_updates": [],
                    "withheld_information": [],
                }), model="offline-mock", raw={"provider": "fake"})
            if request.method == "submit_orders":
                legal = request.context.get("legal_order_context", {})
                orders = []
                for unit in legal.get("your_units", []):
                    options = unit.get("legal_orders", [])
                    if options:
                        orders.append({"unit_ref": unit["unit_ref"], **options[0]})
                from llm_diplomacy.agents.llm_provider import LLMResponse
                return LLMResponse(schema_version="1.0", content=json.dumps({
                    "orders": orders,
                    "order_rationale": {"goal_supported": "hold", "why_this_advances_strategy": "preserve position"},
                    "passivity_review": {"justified": True, "reason": "Hold at the assigned province."},
                }), model="offline-mock", raw={"provider": "fake"})
            if request.method == "reflect":
                from llm_diplomacy.agents.llm_provider import LLMResponse
                return LLMResponse(schema_version="1.0", content=json.dumps({
                    "schema_version": "reflection_compact_v3_campaign_memory",
                    "objective_observations": ["No private messages observed."],
                    "belief_updates": [],
                    "strategic_intent": {"short_term": "Hold", "medium_term": "Coordinate", "risk_notes": []},
                    "memory_update": "No new evidence.",
                    "outcome_review": "The orders and adjudication are the current evidence.",
                    "campaign_plan": "Maintain position and reassess next phase.",
                    "commitment_memory": "No decision-relevant commitments yet.",
                    "winter_assessment": "The board has no winter adjustment decision pending for this power.",
                    "explanation": {"summary": "offline mock"},
                }), model="offline-mock", raw={"provider": "fake"})
            return self.fake.generate(request)

    provider = InterruptThenFake()
    monkeypatch.setattr("llm_diplomacy.population.runner.build_provider_from_config", lambda _cfg: provider)
    monkeypatch.setattr("llm_diplomacy.runner.scripted_experiment_runner.build_provider_from_config", lambda _cfg: provider)
    experiment_root = tmp_path / "experiments" / config.experiment_id
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment_root / "agents")

    # A strict preflight dry-run leaves only dry-run evidence and must not block
    # the subsequent fresh launch.
    runner.run(dry_run_override=True)
    assert json.loads((experiment_root / "population_manifest.json").read_text(encoding="utf-8"))["dry_run"] is True
    with pytest.raises(RuntimeError, match="Recovery required before fallback"):
        runner.run()
    status = runner.recovery_status()
    assert len(provider.calls) == 2
    assert status["in_flight"]
    pending = status["in_flight"][0]
    assert not (tmp_path / "experiments" / config.experiment_id / "games" / "game_001").exists()
    assert (experiment_root / "agents").is_dir()  # preserved preflight folios remain canonical until commit

    original_apply = ExperimentRecovery._apply_commit
    report_publish_interrupted = False

    def interrupt_report_publish(self, manifest, *, canonical_root, stop_after_files=None):
        nonlocal report_publish_interrupted
        if manifest.get("cursor", {}).get("unit") == "population_reports_completed" and not report_publish_interrupted:
            original_apply(self, manifest, canonical_root=canonical_root, stop_after_files=1)
            report_publish_interrupted = True
            raise RuntimeError("simulated crash during final report publication")
        return original_apply(self, manifest, canonical_root=canonical_root, stop_after_files=stop_after_files)

    monkeypatch.setattr(ExperimentRecovery, "_apply_commit", interrupt_report_publish)
    with pytest.raises(RuntimeError, match="simulated crash during final report publication"):
        runner.run(resume=True, decision_action="retry_unknown",
                   decision_id=pending["decision_id"], attempt_id=pending["attempt_id"], prompt_hash=pending["prompt_hash"])
    assert report_publish_interrupted
    assert len(provider.calls) == 11  # all provider work finished before report publication
    runner.run(resume=True)

    # Replay must reuse the successful retry response for the first decision. Any
    # later decision requests are new logical requests, never a duplicate retry.
    assert provider.calls[1][0] == provider.calls[2][0]
    canonical = tmp_path / "experiments" / config.experiment_id
    game_events = [json.loads(line) for line in (canonical / "games/game_001/logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("event_type") == "game_end_cleanup_applied" for row in game_events) == 1
    experiment_events = [json.loads(line) for line in (canonical / "logs/experiment_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("event_type") == "experiment_completed" for row in experiment_events) == 1
    assert not any("recovery-work" in str(row) for row in game_events + experiment_events)
    checkpoint = json.loads((canonical / "recovery/checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["cursor"]["unit"] == "population_reports_completed"
    attempts = [json.loads(line) for line in (canonical / "recovery/journal.jsonl").read_text(encoding="utf-8").splitlines()]
    dispatches = [row for row in attempts if row.get("kind") == "dispatch"]
    assert len(dispatches) == len(provider.calls)
    assert len({row["attempt_id"] for row in dispatches}) == len(dispatches)
    usage_rows = [json.loads(line) for line in (canonical / "metrics/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("kind") == "request" for row in usage_rows) == len(provider.calls)
    lifecycle_rows = [row for row in usage_rows if row.get("kind") == "lifecycle"]
    assert len(lifecycle_rows) == len({row["lifecycle_id"] for row in lifecycle_rows})
    budget_state = json.loads((canonical / "metrics/budget/budget_state.json").read_text(encoding="utf-8"))
    assert budget_state["total_provider_calls"] == len(provider.calls) == 11
    assert len(budget_state["pending_reservations"]) == 1
    assert next(iter(budget_state["pending_reservations"].values()))["status"] == "uncertain"
    run_summary = json.loads((canonical / "run_summary.json").read_text(encoding="utf-8"))
    assert str(canonical) in run_summary["leaderboard_overall_path"]
    assert "recovery-work" not in json.dumps(run_summary)
    population_manifest = json.loads((canonical / "population_manifest.json").read_text(encoding="utf-8"))
    assert all("recovery-work" not in row.get("folio_path", "") for row in population_manifest["agents"])
    population_summary = json.loads((canonical / "population_summary.json").read_text(encoding="utf-8"))
    pilot_report = json.loads((canonical / "pilot_report.json").read_text(encoding="utf-8"))
    assert population_summary["provider_usage"]["totals"]["provider_calls"] == len(provider.calls)
    assert pilot_report["provider_calls"] == len(provider.calls)
    assert "recovery-work" not in json.dumps(population_summary)
    assert "recovery-work" not in json.dumps(pilot_report)
    assert (canonical / "dry_run_report.json").is_file()  # preserve the preflight record

    control_payload = dict(payload)
    control_payload["experiment_id"] = "pop_resume_control"
    control_path = tmp_path / "control.yaml"
    control_path.write_text(yaml.safe_dump(control_payload, sort_keys=True), encoding="utf-8")
    control_config = load_population_experiment_config(control_path)
    control_provider = InterruptThenFake(interrupt=False)
    monkeypatch.setattr("llm_diplomacy.population.runner.build_provider_from_config", lambda _cfg: control_provider)
    monkeypatch.setattr("llm_diplomacy.runner.scripted_experiment_runner.build_provider_from_config", lambda _cfg: control_provider)
    control_root = tmp_path / "experiments" / control_config.experiment_id
    PopulationExperimentRunner(config=control_config, base_dir=tmp_path,
                               agents_root=control_root / "agents").run()
    resumed_end = next(row["payload"]["result"] for row in reversed(game_events)
                       if row.get("event_type") == "phase_completed" and row.get("phase_id") == "game_end")
    control_events = [json.loads(line) for line in (control_root / "games/game_001/logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    control_end = next(row["payload"]["result"] for row in reversed(control_events)
                       if row.get("event_type") == "phase_completed" and row.get("phase_id") == "game_end")
    assert resumed_end == control_end
    resumed_messages = [json.loads(line) for line in (canonical / "games/game_001/messages/global/global.jsonl").read_text(encoding="utf-8").splitlines()]
    control_messages = [json.loads(line) for line in (control_root / "games/game_001/messages/global/global.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["body"] for row in resumed_messages] == [row["body"] for row in control_messages]
    assert len(resumed_messages) == len({row["message_id"] for row in resumed_messages})

    request_count = len(provider.calls)
    # An explicit resume after a completed publication is idempotent and does
    # not invoke providers or append duplicate game cleanup effects.
    runner.run(resume=True)
    assert len(provider.calls) == request_count
    repeated_events = [json.loads(line) for line in (canonical / "games/game_001/logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("event_type") == "game_end_cleanup_applied" for row in repeated_events) == 1
    assert json.loads((canonical / "pilot_report.json").read_text(encoding="utf-8")) == pilot_report


def test_strict_population_rejects_external_agents_root_before_mutation(tmp_path) -> None:
    config_path = tmp_path / "strict_external.yaml"
    _write_population_config(config_path, experiment_id="strict_external", blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    external_agents = tmp_path / "agents"
    external_agents.mkdir()
    marker = external_agents / "preserve.txt"
    marker.write_text("keep", encoding="utf-8")
    runner = PopulationExperimentRunner(config=load_population_experiment_config(config_path),
                                        base_dir=tmp_path, agents_root=external_agents)

    with pytest.raises(ValueError, match="--agents-root inside the experiment"):
        runner.run()

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "experiments" / "strict_external").exists()


def test_dry_run_refuses_existing_run_evidence_without_replacing_it(tmp_path) -> None:
    config_path = tmp_path / "dry_guard.yaml"
    _write_population_config(config_path, experiment_id="dry_guard",
                             blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    config = load_population_experiment_config(config_path)
    experiment = tmp_path / "experiments" / "dry_guard"
    marker = experiment / "agents" / "cassandra" / "keep-evidence.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("preserve", encoding="utf-8")
    manifest = experiment / "population_manifest.json"
    manifest.write_text('{"dry_run":false}', encoding="utf-8")
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment / "agents")
    with pytest.raises(FileExistsError, match="dry-run refuses to mutate"):
        runner.run(dry_run_override=True)
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert manifest.read_text(encoding="utf-8") == '{"dry_run":false}'


def test_dry_run_respects_active_writer_lock(tmp_path) -> None:
    config_path = tmp_path / "dry_lock.yaml"
    _write_population_config(config_path, experiment_id="dry_lock",
                             blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    config = load_population_experiment_config(config_path)
    experiment = tmp_path / "experiments" / "dry_lock"
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment / "agents")
    with RecoveryLock(experiment / "recovery" / "writer.lock"):
        with pytest.raises(RuntimeError, match="active recovery writer"):
            runner.run(dry_run_override=True)
    assert not (experiment / "population_manifest.json").exists()


def test_recovery_identity_binds_rule_engine_and_map_data(tmp_path, monkeypatch) -> None:
    import llm_diplomacy.population.runner as population_module

    config_path = tmp_path / "identity.yaml"
    _write_population_config(config_path, experiment_id="identity",
                             blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    config = load_population_experiment_config(config_path)
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path,
                                        agents_root=tmp_path / "experiments/identity/agents")
    before = runner._recovery_identity()
    original = population_module._sha256
    for suffix in ("rules/diplomacy/engine.py", "maps/standard.json"):
        changed = "changed-" + suffix
        monkeypatch.setattr(population_module, "_sha256",
                            lambda path, s=suffix, old=original: "changed" if str(path).replace("\\", "/").endswith(s) else old(path))
        assert runner._recovery_identity() != before
        monkeypatch.setattr(population_module, "_sha256", original)


def test_safe_halt_after_order_validation_prevents_adjudication(tmp_path, monkeypatch) -> None:
    from llm_diplomacy.rules.diplomacy.engine import DiplomacyRuleEngine

    config_path = tmp_path / "halt_before_resolve.yaml"
    _write_population_config(config_path, experiment_id="halt_before_resolve",
                             blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    payload["runtime"]["llm_agent_ids"] = []
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_population_experiment_config(config_path)
    experiment_root = tmp_path / "experiments" / "halt_before_resolve"
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment_root / "agents")
    original_validate = DiplomacyRuleEngine.validate_orders
    original_resolve = DiplomacyRuleEngine.resolve_phase
    resolutions = []

    def validate(engine, *args, **kwargs):
        result = original_validate(engine, *args, **kwargs)
        runner._active_recovery.request_safe_halt("before-adjudication")
        return result

    def resolve(engine, *args, **kwargs):
        resolutions.append(True)
        return original_resolve(engine, *args, **kwargs)

    monkeypatch.setattr(DiplomacyRuleEngine, "validate_orders", validate)
    monkeypatch.setattr(DiplomacyRuleEngine, "resolve_phase", resolve)
    with pytest.raises(RuntimeError, match="Safe halt requested"):
        runner.run()
    assert resolutions == []
    assert not (experiment_root / "games" / "game_001").exists()


@pytest.mark.parametrize("repeat", [False, True])
def test_strict_missing_unit_order_corrects_only_that_players_response(tmp_path, monkeypatch, repeat) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="missing_order_correction", games=1,
                              agents=[{"agent_id": "fr", "runtime_kind": "llm"},
                                      {"agent_id": "en", "runtime_kind": "llm"}],
                              rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1,
                              raw={"provider_failure_policy": {"strict_recovery": True}})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    if repeat:
        from llm_diplomacy.runner.recovery import ExperimentRecovery
        runner.recovery = ExperimentRecovery(tmp_path / 'recovery-test', identity={'test':'engine-loop'},
                                             resume=False, repeat_received_invalid=True)
        runner.recovery.prepare()
    state = runner.rule_engine.initial_state({})
    state.update(game_id="game_001", year=1901, season="spring", phase="movement")
    state["units"] = [row for row in state["units"] if row.get("owner") in {"france", "england"}]

    class Responses:
        def __init__(self):
            self.calls = []

        def submit_orders(self, context, correction_feedback=None):
            country = context.country_assignment
            self.calls.append((country, correction_feedback))
            units = [row for row in context.visible_state["units"] if row.get("owner") == country]
            orders = [{"schema_version": "1.0", "order_id": f"{country}-{row['unit_id']}",
                       "unit_ref": row["unit_id"], "power": country, "type": "HOLD",
                       "payload": {"at": row["province"]}}
                      for row in units]
            if country == "france" and (sum(c == country for c, _ in self.calls) <= 4 if repeat else correction_feedback is None):
                orders = orders[:1]
            metadata = {"decision_id": f"game_001/1901_spring_movement/once/{country}/{country}/submit_orders",
                        "prompt_hash": f"{country}-base", "attempt_id": f"{country}-attempt",
                        "raw_content": "original", "legal_order_context": {"your_options": [country]},
                        "correction_used": correction_feedback is not None}
            return OrderSet("1.0", True, orders, {}, {"_recovery": metadata})

        def reflect(self, _context):
            return ReflectionResult("1.0", True, {}, {}, {})

    responses = Responses()
    gm = GameMaster(config=config, base_dir=tmp_path, game_id="game_001")
    gm.initialize_game_state(state)
    monkeypatch.setattr(runner, "_run_negotiation_tick", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_log_agent_invocation_completed", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_emit_budget_events", lambda **_kwargs: None)
    try:
        runner._run_phase(gm=gm, scripted_runtime=responses, llm_runtime=responses,
                          runtime_by_agent={"fr": "llm", "en": "llm"},
                          assignments={"france": "fr", "england": "en"}, state=state,
                          year=1901, season="spring", phase="movement")
    except Exception as exc:
        pytest.fail(f"{exc}; calls={responses.calls!r}")

    assert [country for country, _ in responses.calls] == ["england"] + ["france"] * (5 if repeat else 2)
    corrected_call = next(feedback for country, feedback in responses.calls
                          if country == "france" and feedback is not None)
    assert "missing_order" in json.dumps(corrected_call)
    assert corrected_call["legal_options"] == {"your_options": ["france"]}


def test_status_and_halt_reopen_repeat_policy_with_same_identity(tmp_path):
    path=tmp_path/'config.yaml'
    _write_population_config(path, experiment_id='policy-reopen', blueprints=[str(Path('blueprints/cassandra.yaml').resolve())])
    raw=yaml.safe_load(path.read_text())
    raw['provider_failure_policy']={'strict_recovery':True,'received_invalid_policy':'repeat_with_feedback_v1','max_wall_seconds':1000}
    raw['budget']={'max_provider_calls':20,'max_input_tokens':100000,'max_output_tokens':10000,'max_estimated_cost_usd':1}
    path.write_text(yaml.safe_dump(raw))
    config=load_population_experiment_config(path)
    runner=PopulationExperimentRunner(config=config,base_dir=tmp_path,agents_root=tmp_path/'experiments/policy-reopen/agents')
    store=runner._recovery_store(resume=False)
    store.prepare()
    assert runner.recovery_status()['checkpoint_valid']
    runner.request_safe_halt('offline check')
    assert store.safe_halt_path.exists()


def test_strict_invalid_correction_halts_without_a_third_request(tmp_path, monkeypatch):
    config = ExperimentConfig(schema_version="1.0", experiment_id="invalid_correction", games=1,
                              agents=[{"agent_id": "fr", "runtime_kind": "llm"},
                                      {"agent_id": "en", "runtime_kind": "llm"}],
                              rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1,
                              raw={"provider_failure_policy": {"strict_recovery": True}})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    state = runner.rule_engine.initial_state({})
    state.update(game_id="game_001", year=1901, season="spring", phase="movement")
    state["units"] = [row for row in state["units"] if row.get("owner") in {"france", "england"}]

    class Responses:
        def __init__(self):
            self.calls = []
            self.precorrected = False

        def submit_orders(self, context, correction_feedback=None):
            country = context.country_assignment
            self.calls.append((country, correction_feedback))
            units = [row for row in context.visible_state["units"] if row.get("owner") == country]
            orders = [{"schema_version": "1.0", "order_id": f"{country}-{row['unit_id']}",
                       "unit_ref": row["unit_id"], "power": country, "type": "HOLD",
                       "payload": {"at": row["province"]}} for row in units]
            if country == "france":
                orders = orders[:1]
            return OrderSet("1.0", True, orders, {}, {"_recovery": {
                "decision_id": f"game_001/1901_spring_movement/once/{country}/{country}/submit_orders",
                "prompt_hash": f"{country}-base", "attempt_id": f"{country}-attempt",
                "raw_content": "rejected", "legal_order_context": {"options": [country]},
                "correction_used": self.precorrected or correction_feedback is not None,
                "order_validation_issue": "still illegal after correction" if self.precorrected and country == "france" else None,
            }})

    responses = Responses()
    gm = GameMaster(config=config, base_dir=tmp_path, game_id="game_001")
    gm.initialize_game_state(state)
    monkeypatch.setattr(runner, "_run_negotiation_tick", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_log_agent_invocation_completed", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_emit_budget_events", lambda **_kwargs: None)
    monkeypatch.setattr(runner.rule_engine, "resolve_phase", lambda *_args, **_kwargs:
                        pytest.fail("invalid correction reached adjudication"))
    with pytest.raises(RuntimeError, match="invalid or substituted orders"):
        runner._run_phase(gm=gm, scripted_runtime=responses, llm_runtime=responses,
                          runtime_by_agent={"fr": "llm", "en": "llm"},
                          assignments={"france": "fr", "england": "en"}, state=state,
                          year=1901, season="spring", phase="movement")
    assert [country for country, _ in responses.calls] == ["england", "france", "france"]

    responses.calls.clear()
    responses.precorrected = True
    with pytest.raises(RuntimeError, match="invalid or substituted orders"):
        runner._run_phase(gm=gm, scripted_runtime=responses, llm_runtime=responses,
                          runtime_by_agent={"fr": "llm", "en": "llm"},
                          assignments={"france": "fr", "england": "en"}, state=state,
                          year=1901, season="spring", phase="movement")
    assert [country for country, _ in responses.calls] == ["england", "france"]


@pytest.mark.parametrize("phase,order_type", [
    ("adjustment", "BUILD"),
    ("adjustment", "DISBAND"),
    ("retreat", "RETREAT"),
])
def test_strict_order_guard_accepts_engine_valid_phase_specific_orders(tmp_path, monkeypatch, phase, order_type) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id=f"phase_{order_type.lower()}", games=1,
                              agents=[{"agent_id": "a", "runtime_kind": "scripted"}],
                              rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1,
                              raw={"provider_failure_policy": {"strict_recovery": True}})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    state = runner.rule_engine.initial_state({})
    state.update(game_id="game_001", phase=phase, season="winter" if phase == "adjustment" else "spring")
    unit = state["units"][0]
    unit_id = unit["unit_id"]
    power = unit["owner"]
    province = unit["province"]
    if order_type == "BUILD":
        state["units"] = [row for row in state["units"] if row["unit_id"] != unit_id]
        order = {"schema_version": "1.0", "order_id": "build", "power": power, "type": "BUILD",
                 "payload": {"unit_type": "A", "province": province}}
    elif order_type == "DISBAND":
        state["supply_ownership"][province] = None
        order = {"schema_version": "1.0", "order_id": "disband", "power": power, "type": "DISBAND",
                 "payload": {"unit_ref": unit_id}}
    else:
        unit.update(status="dislodged", must_retreat=True)
        order = {"schema_version": "1.0", "order_id": "retreat", "power": power, "unit_ref": unit_id,
                 "type": "RETREAT", "payload": {"to": "boh"}}
    validated = runner.rule_engine.validate_orders(state, [order])
    assert validated["validated_orders"][0]["validation"] == "accepted"

    class Stub:
        def submit_orders(self, _context):
            return OrderSet("1.0", True, [order], {}, {})

        def reflect(self, _context):
            return ReflectionResult("1.0", True, {}, {}, {})

    gm = GameMaster(config=config, base_dir=tmp_path, game_id="game_001")
    gm.initialize_game_state(state)
    monkeypatch.setattr(runner, "_run_negotiation_tick", lambda **_kwargs: None)
    runner._run_phase(gm=gm, scripted_runtime=Stub(), llm_runtime={}, runtime_by_agent={"a": "scripted"},
                      assignments={power: "a"}, state=state, year=1901, season=state["season"], phase=phase)


def _run_strict_retreat(tmp_path, *, unit, blockers=(), additional_units=(), standoffs=(), orders=()):
    config = ExperimentConfig(schema_version="1.0", experiment_id="retreat_no_destination", games=1,
                              agents=[{"agent_id": "a", "runtime_kind": "scripted"}],
                              rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1,
                              raw={"provider_failure_policy": {"strict_recovery": True}})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    state = runner.rule_engine.initial_state({})
    state.update(game_id="game_001", year=1902, season="spring", phase="retreat")
    state["units"] = [unit, *blockers, *additional_units]
    state["standoff_provinces"] = list(standoffs)
    power = unit["owner"]

    class Stub:
        def submit_orders(self, _context):
            return OrderSet("1.0", True, list(orders), {}, {})

        def reflect(self, _context):
            return ReflectionResult("1.0", True, {}, {}, {})

    gm = GameMaster(config=config, base_dir=tmp_path, game_id="game_001")
    gm.initialize_game_state(state)
    result = runner._run_phase(gm=gm, scripted_runtime=Stub(), llm_runtime={},
                               runtime_by_agent={"a": "scripted"}, assignments={power: "a"},
                               state=state, year=1902, season="spring", phase="retreat")
    return result


def test_strict_retreat_allows_forced_disband_when_no_destination_exists(tmp_path) -> None:
    from llm_diplomacy.rules.diplomacy.engine import DiplomacyRuleEngine

    engine = DiplomacyRuleEngine()
    province = engine.province_by_id["par"]
    unit = {"schema_version": "1.0", "unit_id": "trapped", "owner": "france", "type": "A",
            "province": "par", "coast": None, "status": "dislodged", "must_retreat": True,
            "dislodged_from": "pic"}
    blockers = [{"schema_version": "1.0", "unit_id": f"blocker-{index}", "owner": "germany", "type": "A",
                 "province": destination, "coast": None, "status": "active", "must_retreat": False}
                for index, destination in enumerate(province.army_adjacency)]

    result = _run_strict_retreat(tmp_path, unit=unit, blockers=blockers)
    trapped = next(row for row in result["units"] if row["unit_id"] == "trapped")
    assert trapped["status"] == "destroyed"


def test_strict_retreat_still_halts_missing_order_with_legal_destination(tmp_path) -> None:
    unit = {"schema_version": "1.0", "unit_id": "can-retreat", "owner": "france", "type": "A",
            "province": "par", "coast": None, "status": "dislodged", "must_retreat": True,
            "dislodged_from": "bur"}
    with pytest.raises(RuntimeError, match="invalid or substituted orders"):
        _run_strict_retreat(tmp_path, unit=unit)


def test_strict_retreat_mixed_trapped_and_legal_missing_order_still_halts(tmp_path) -> None:
    from llm_diplomacy.rules.diplomacy.engine import DiplomacyRuleEngine

    engine = DiplomacyRuleEngine()
    trapped = {"schema_version": "1.0", "unit_id": "trapped", "owner": "france", "type": "A",
               "province": "par", "coast": None, "status": "dislodged", "must_retreat": True,
               "dislodged_from": "pic"}
    blockers = [{"schema_version": "1.0", "unit_id": f"blocker-{index}", "owner": "germany", "type": "A",
                 "province": destination, "coast": None, "status": "active", "must_retreat": False}
                for index, destination in enumerate(engine.province_by_id["par"].army_adjacency)]
    legal_missing = {"schema_version": "1.0", "unit_id": "legal-missing", "owner": "france", "type": "A",
                     "province": "bur", "coast": None, "status": "dislodged", "must_retreat": True,
                     "dislodged_from": "par"}
    with pytest.raises(RuntimeError, match="invalid or substituted orders"):
        _run_strict_retreat(tmp_path, unit=trapped, blockers=blockers, additional_units=[legal_missing])


def test_strict_retreat_still_halts_bad_submission_for_trapped_unit(tmp_path) -> None:
    from llm_diplomacy.rules.diplomacy.engine import DiplomacyRuleEngine

    engine = DiplomacyRuleEngine()
    unit = {"schema_version": "1.0", "unit_id": "trapped", "owner": "france", "type": "A",
            "province": "par", "coast": None, "status": "dislodged", "must_retreat": True,
            "dislodged_from": "pic"}
    blockers = [{"schema_version": "1.0", "unit_id": f"blocker-{index}", "owner": "germany", "type": "A",
                 "province": destination, "coast": None, "status": "active", "must_retreat": False}
                for index, destination in enumerate(engine.province_by_id["par"].army_adjacency)]
    with pytest.raises(RuntimeError, match="invalid or substituted orders"):
        _run_strict_retreat(tmp_path, unit=unit, blockers=blockers,
                            orders=[{"order_id": "wrong", "unit_ref": "trapped", "type": "HOLD", "payload": {}}])


def test_retreat_legality_uses_all_split_coast_nodes_and_convoy_exception(tmp_path) -> None:
    from llm_diplomacy.rules.diplomacy.engine import DiplomacyRuleEngine

    config = ExperimentConfig(schema_version="1.0", experiment_id="retreat_legal_nodes", games=1,
                              agents=[{"agent_id": "a", "runtime_kind": "scripted"}],
                              rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1,
                              raw={"provider_failure_policy": {"strict_recovery": True}})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    engine = DiplomacyRuleEngine()
    fleet = {"unit_id": "fleet", "owner": "turkey", "type": "F", "province": "bul",
             "coast": "bul_ec", "status": "dislodged", "dislodged_from": "bla"}
    assert "rum" in runner._legal_retreat_destinations({"units": [fleet]}, fleet)
    # BLA is the attacker's origin and is forbidden unless the dislodgement was by convoy.
    assert "bla" not in runner._legal_retreat_destinations({"units": [fleet]}, fleet)
    fleet["dislodged_by_convoy"] = True
    assert "bla" in runner._legal_retreat_destinations({"units": [fleet]}, fleet)
    constantinople_fleet = {**fleet, "unit_id": "constantinople", "province": "con", "coast": None,
                            "dislodged_from": "aeg", "dislodged_by_convoy": False}
    destinations = runner._legal_retreat_destinations({"units": [constantinople_fleet]}, constantinople_fleet)
    assert {"bul_ec", "bul_sc"} <= set(destinations)


def test_retreat_legality_does_not_count_standoffs_or_other_retreat_choices(tmp_path) -> None:
    config = ExperimentConfig(schema_version="1.0", experiment_id="retreat_standoff", games=1,
                              agents=[{"agent_id": "a", "runtime_kind": "scripted"}],
                              rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1,
                              raw={"provider_failure_policy": {"strict_recovery": True}})
    runner = ScriptedExperimentRunner(config=config, base_dir=tmp_path)
    fleet = {"unit_id": "fleet", "owner": "turkey", "type": "F", "province": "bul",
             "coast": "bul_sc", "status": "dislodged", "dislodged_from": "ion"}
    legal = runner._legal_retreat_destinations({"units": [fleet], "standoff_provinces": ["gre"]}, fleet)
    assert "gre" not in legal
    # A different retreating unit's submitted/possible destination does not block this unit.
    other_retreat = {"unit_id": "other", "owner": "italy", "type": "F", "province": "aeg",
                     "coast": None, "status": "dislodged"}
    assert "gre" in runner._legal_retreat_destinations(
        {"units": [fleet, other_retreat], "standoff_provinces": []}, fleet)


@pytest.mark.parametrize("case", ["occupied", "standoff", "attacker-origin"])
def test_strict_retreat_halts_explicit_state_illegal_destination(tmp_path, case) -> None:
    unit = {"schema_version": "1.0", "unit_id": "explicit", "owner": "france", "type": "A",
            "province": "par", "coast": None, "status": "dislodged", "must_retreat": True,
            "dislodged_from": "bur" if case == "attacker-origin" else "pic"}
    blockers = ([{"schema_version": "1.0", "unit_id": "occupier", "owner": "germany", "type": "A",
                  "province": "bur", "coast": None, "status": "active", "must_retreat": False}]
                if case == "occupied" else [])
    standoffs = ["bur"] if case == "standoff" else []
    retreat = {"order_id": "explicit-retreat", "unit_ref": "explicit", "type": "RETREAT",
               "payload": {"to": "bur"}}
    with pytest.raises(RuntimeError, match="invalid or substituted orders"):
        _run_strict_retreat(tmp_path, unit=unit, blockers=blockers, standoffs=standoffs, orders=[retreat])


def test_strict_retreat_accepts_explicit_legal_destination(tmp_path) -> None:
    unit = {"schema_version": "1.0", "unit_id": "explicit", "owner": "france", "type": "A",
            "province": "par", "coast": None, "status": "dislodged", "must_retreat": True,
            "dislodged_from": "pic"}
    retreat = {"order_id": "explicit-retreat", "unit_ref": "explicit", "type": "RETREAT",
               "payload": {"to": "bur"}}
    result = _run_strict_retreat(tmp_path, unit=unit, orders=[retreat])
    moved = next(row for row in result["units"] if row["unit_id"] == "explicit")
    assert moved["province"] == "bur" and moved["status"] == "active"


def test_safe_halt_after_game_completion_prevents_game_and_report_publication(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "publication-halt.yaml"
    names = ["cassandra", "dorian", "atlas", "birsan", "dolos", "helena", "iris"]
    _write_population_config(config_path, experiment_id="publication_halt",
                             blueprints=[str((Path(__file__).parents[1] / "blueprints" / f"{name}.yaml").resolve())
                                         for name in names], seed=73)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    payload["runtime"]["llm_agent_ids"] = []
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_population_experiment_config(config_path)
    experiment_root = tmp_path / "experiments" / "publication_halt"
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment_root / "agents")
    original_emit = ScriptedExperimentRunner._emit_event

    def halt_before_publish(self, event_type, **kwargs):
        if event_type == "experiment_completed":
            self.recovery.request_safe_halt("before-publication")
        return original_emit(self, event_type, **kwargs)

    monkeypatch.setattr(ScriptedExperimentRunner, "_emit_event", halt_before_publish)
    with pytest.raises(RuntimeError, match="Safe halt requested"):
        runner.run()
    assert (experiment_root / "recovery" / "safe_halt.json").exists()
    assert not (experiment_root / "games" / "game_001").exists()
    assert not (experiment_root / "pilot_report.json").exists()


def test_safe_halt_between_game_and_report_commits_keeps_game_without_reports(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "report-halt.yaml"
    names = ["cassandra", "dorian", "atlas", "birsan", "dolos", "helena", "iris"]
    _write_population_config(config_path, experiment_id="report_halt",
                             blueprints=[str((Path(__file__).parents[1] / "blueprints" / f"{name}.yaml").resolve())
                                         for name in names], seed=74)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    payload["runtime"]["llm_agent_ids"] = []
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_population_experiment_config(config_path)
    experiment_root = tmp_path / "experiments" / "report_halt"
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment_root / "agents")
    original_commit = ExperimentRecovery.commit_tree

    def halt_after_game_commit(self, **kwargs):
        original_commit(self, **kwargs)
        if kwargs.get("cursor", {}).get("unit") == "game_completed":
            self.request_safe_halt("between-game-and-report-publication")

    monkeypatch.setattr(ExperimentRecovery, "commit_tree", halt_after_game_commit)
    with pytest.raises(RuntimeError, match="Safe halt requested"):
        runner.run()
    assert (experiment_root / "games" / "game_001").is_dir()
    assert (experiment_root / "recovery" / "safe_halt.json").exists()
    assert not (experiment_root / "pilot_report.json").exists()
    assert json.loads((experiment_root / "recovery" / "checkpoint.json").read_text(encoding="utf-8"))["cursor"]["unit"] == "game_completed"


def test_resume_bootstraps_crash_before_initial_snapshot(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "bootstrap.yaml"
    _write_population_config(config_path, experiment_id="bootstrap",
                             blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    payload["runtime"]["llm_agent_ids"] = []
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_population_experiment_config(config_path)
    experiment_root = tmp_path / "experiments" / "bootstrap"
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment_root / "agents")
    original = ExperimentRecovery.save_initial_snapshot
    calls = 0

    def crash_once(recovery):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected crash before snapshot")
        original(recovery)

    monkeypatch.setattr(ExperimentRecovery, "save_initial_snapshot", crash_once)
    with pytest.raises(RuntimeError, match="injected crash before snapshot"):
        runner.run()
    result = runner.run(resume=True)
    assert result.pilot_report_json_path.exists()
    assert calls >= 2
    assert list((experiment_root / "recovery" / "partials").glob("stage-*"))


def test_strict_population_rejects_rule_engine_substitutions_before_publish(tmp_path, monkeypatch) -> None:
    from llm_diplomacy.agents.contracts import OrderSet
    from llm_diplomacy.agents.scripted_runtime import ScriptedAgentRuntime

    config_path = tmp_path / "reject_orders.yaml"
    _write_population_config(config_path, experiment_id="reject_orders",
                             blueprints=[str(Path("blueprints/cassandra.yaml").resolve())])
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["provider_failure_policy"] = {"strict_recovery": True}
    payload["runtime"]["llm_agent_ids"] = []
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = load_population_experiment_config(config_path)
    experiment_root = tmp_path / "experiments" / "reject_orders"
    runner = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=experiment_root / "agents")

    def invalid_orders(self, context):
        return OrderSet(schema_version="1.0", ok=True, orders=[{
            "schema_version": "1.0", "order_id": "bad", "unit_ref": "NO_UNIT",
            "power": context.country_assignment, "type": "HOLD", "payload": {},
        }], usage={}, explanation={})

    monkeypatch.setattr(ScriptedAgentRuntime, "submit_orders", invalid_orders)
    with pytest.raises(RuntimeError, match="before adjudication"):
        runner.run()
    assert not (experiment_root / "games" / "game_001").exists()
