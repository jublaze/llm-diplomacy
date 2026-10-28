from __future__ import annotations

import json
import socket
from pathlib import Path
from dataclasses import replace

import pytest
import yaml

from llm_diplomacy.agents.llm_provider import BudgetedLLMProvider, FakeLLMProvider, LLMRequest, LLMResponse
from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
from llm_diplomacy.metrics.provider_usage import ProviderUsageTracker
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner
from llm_diplomacy.reporting.bundle_exporter import ExportInputError, _normalized_outcome, inspect_experiment
from test_report_bundle_exporter import _make_completed_experiment
import llm_diplomacy.runner.scripted_experiment_runner as runner_module
import llm_diplomacy.population.runner as population_module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Network is forbidden in offline acceptance tests")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr("urllib.request.urlopen", deny)


def population_config(tmp_path, *, budget=None, games=1):
    players = [{"agent_id": player, "blueprint": str(Path("blueprints/cassandra.yaml").resolve()),
                "provider_config": {"kind": "fake", "model": model, "pricing": {
                    "input_cost_per_1k_tokens": price, "output_cost_per_1k_tokens": price * 2}}}
               for player, model, price in (("cassandra_a", "model-a", 1), ("cassandra_b", "model-b", 2))]
    players += [str(Path(f"blueprints/{name}.yaml").resolve())
                for name in ("dorian", "helena", "marcellus", "seraphine", "victor")]
    raw = {"schema_version": "1.0", "experiment_id": "acceptance", "agent_state_mode": "reuse",
           "agents": players, "runtime": {"kind": "baseline_llm", "provider": "fake", "max_ticks": 1,
           "llm_agent_ids": ["cassandra_a", "cassandra_b"]},
           "games": {"count": games, "max_year": 1901, "country_assignment": "round_robin", "seed": 7},
           "outputs": {"observatory": False, "summary": True}}
    if budget is not None:
        raw["budget"] = budget
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_population_experiment_config(path)


class SpyProvider(FakeLLMProvider):
    def __init__(self, cfg, calls):
        super().__init__(model=cfg.model)
        self.calls = calls

    def generate(self, request):
        self.calls.append((self.model, request))
        response = super().generate(request)
        content = response.content
        if request.method == "game_end_cleanup":
            content = json.dumps({"persistent_memory_update": f"Unique lesson for {request.agent_id}: verify support before advancing.",
                                  "public_profile_update": None, "explanation": {"summary": "offline cleanup"}})
        return replace(response, content=content, raw={"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


def test_seven_players_two_games_route_repairs_cleanup_and_isolate_memory(tmp_path, monkeypatch):
    calls = []
    build = lambda cfg: SpyProvider(cfg, calls)
    monkeypatch.setattr(runner_module, "build_provider_from_config", build)
    monkeypatch.setattr(population_module, "build_provider_from_config", build)
    cfg = population_config(tmp_path, games=2)
    cfg.raw["reflection_protocol"] = "legacy_json_v1"
    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    summary = json.loads(result.summary_json_path.read_text())
    pilot = json.loads(result.pilot_report_json_path.read_text())
    manifest = json.loads(result.manifest_path.read_text())
    assert summary["games_completed"] == 2
    assert len(manifest["agents"]) == 7
    for artifact in (summary, pilot, manifest):
        assert artifact["provider_mode"] == "mixed"
        assert artifact["model_provider"]["requested_model"] is None
        assert artifact["model_provider"]["provider_name"] == "mixed"
    assert (tmp_path / "agents/cassandra_a/soul.md").read_bytes() == (tmp_path / "agents/cassandra_b/soul.md").read_bytes()
    for player, expected in (("cassandra_a", "model-a"), ("cassandra_b", "model-b")):
        requests = [r for model, r in calls if r.agent_id == player]
        assert {model for model, r in calls if r.agent_id == player} == {expected}
        assert {r.method for r in requests} == {"negotiate_tick", "submit_orders", "reflect", "game_end_cleanup"}
        assert any(r.context["attempt_index"] == 1 for r in requests)
        second = next(r for r in requests if r.context["game_id"] == "game_002")
        assert f"Unique lesson for {player}" in second.prompt
        other = "cassandra_b" if player == "cassandra_a" else "cassandra_a"
        assert f"Unique lesson for {other}" not in second.prompt
        stats = summary["provider_usage"]["by_agent"][player]
        assert stats["provider_calls"] == len(requests)
        assert stats["observed_resolved_models"] == [expected]
        price = 1 if player == "cassandra_a" else 2
        assert stats["estimated_cost_usd"] == pytest.approx(len(requests) * 0.02 * price)
    totals = summary["provider_usage"]["totals"]
    assert totals["provider_calls"] == len(calls) == pilot["provider_calls"]
    assert totals["repair_calls"] > 0
    assert totals["estimated_cost_usd"] == pytest.approx(sum(r["estimated_cost_usd"] for r in summary["provider_usage"]["by_agent"].values()))
    assert totals["provider_calls"] == sum(r["provider_calls"] for r in summary["provider_usage"]["by_method"].values())
    assert not (result.experiment_root / "metrics/budget/budget_state.json").exists()


def test_population_default_reflections_use_prose_without_reflection_repairs(tmp_path, monkeypatch):
    calls = []
    build = lambda cfg: SpyProvider(cfg, calls)
    monkeypatch.setattr(runner_module, "build_provider_from_config", build)
    monkeypatch.setattr(population_module, "build_provider_from_config", build)
    cfg = population_config(tmp_path)

    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()

    reflections = [request for _, request in calls if request.method == "reflect"]
    assert reflections
    assert all("Write a plain text reflection" in request.prompt for request in reflections)
    assert all("Required JSON Schema" not in request.prompt for request in reflections)
    assert all(request.context["attempt_index"] == 0 for request in reflections)
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["provider_usage"]["by_method"]["reflect"]["repair_calls"] == 0


def test_population_provider_alternative_is_used_and_reported(tmp_path, monkeypatch):
    class Unauthorized:
        def __init__(self, cfg):
            self.model = cfg.model
        def generate(self, request):
            from llm_diplomacy.agents.llm_provider import ProviderHTTPError
            raise ProviderHTTPError(status_code=401, error_body="denied", metadata={"provider_name": "primary"})

    def build(cfg):
        if cfg.model == "primary-fails":
            return Unauthorized(cfg)
        return SpyProvider(cfg, [])

    monkeypatch.setattr(runner_module, "build_provider_from_config", build)
    monkeypatch.setattr(population_module, "build_provider_from_config", build)
    cfg = population_config(tmp_path)
    changed = []
    for player in cfg.players:
        if player.agent_id == "cassandra_a":
            primary = {"kind": "fake", "model": "primary-fails"}
            player_config = {**primary, "fallback_providers": [{"kind": "fake", "model": "alternate-ok"}]}
            changed.append(replace(player, provider_config=player_config))
        else:
            changed.append(player)
    result = PopulationExperimentRunner(config=replace(cfg, players=changed), base_dir=tmp_path,
        agents_root=tmp_path / "agents").run()
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["provider_usage"]["by_agent"]["cassandra_a"]["observed_resolved_models"] == ["alternate-ok"]
    out = next((result.experiment_root / "games/game_001/agents/cassandra_a/runtime/phase_outputs").glob("*.json"))
    metadata = json.loads(out.read_text(encoding="utf-8"))["llm_meta"]
    assert metadata["retry_attempts"][0]["failure_category"] == "provider_auth"
    assert metadata["retry_attempts"][1]["provider_route_metadata"]["requested_model"] == "alternate-ok"


@pytest.mark.parametrize("policy,completed,stopped", [("stop_experiment", 0, 1), ("stop_game_after_phase", 0, 1), ("switch_to_fallback", 1, 0)])
def test_budget_policies_have_consistent_outcomes_and_zero_requests(tmp_path, policy, completed, stopped):
    cfg = population_config(tmp_path, budget={"max_provider_calls": 0, "stop_policy": policy})
    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    run = json.loads((result.experiment_root / "run_summary.json").read_text())
    population = json.loads(result.summary_json_path.read_text())
    assert run["games_completed"] == population["games_completed"] == completed
    assert run["games_stopped"] == population["games_stopped"] == stopped
    assert run["games_started"] == 1
    totals = population["provider_usage"]["totals"]
    assert totals["provider_calls"] == 0
    assert totals["estimated_cost_usd"] == 0
    assert totals["budget_blocked_attempts"] > 0
    assert all(not row["observed_resolved_models"] for row in population["provider_usage"]["by_agent"].values())
    if completed:
        assert population["games"][0]["outcome"]["budget_fallback_used"]
    else:
        with pytest.raises(ExportInputError, match="incomplete|stopped"):
            inspect_experiment(result.experiment_root)


def test_failure_writes_failed_summary_and_preserves_error(tmp_path, monkeypatch):
    cfg = ExperimentConfig(schema_version="1.0", experiment_id="failure", games=1, agents=[], rule_engine={}, negotiation={}, seed=1, raw={})
    runner = ScriptedExperimentRunner(config=cfg, base_dir=tmp_path)
    def fail(**kwargs):
        raise RuntimeError("offline failure")
    monkeypatch.setattr(runner, "_run_single_game", fail)
    with pytest.raises(RuntimeError, match="offline failure"):
        runner.run()
    summary = json.loads((tmp_path / "experiments/failure/run_summary.json").read_text())
    assert (summary["status"], summary["games_completed"], summary["games_failed"]) == ("failed", 0, 1)


@pytest.mark.parametrize("result,partial", [("stopped", True), ("completed", True), ("failed", False), ("unknown", False)])
def test_export_rejects_legacy_incomplete_result_even_with_completed_counter(tmp_path, result, partial):
    root = _make_completed_experiment(tmp_path / "legacy", ("game_one",))
    path = root / "games/game_one/logs/events.jsonl"
    row = json.loads(path.read_text())
    row["payload"]["result"].update(result=result, partial=partial)
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ExportInputError, match="stopped|incomplete|unknown"):
        inspect_experiment(root)
    outcome = _normalized_outcome(row["payload"]["result"], 1910, 1910)
    assert outcome["termination"]["kind"] != "scheduled_max_year"


def test_export_rejects_missing_terminal_evidence(tmp_path):
    root = _make_completed_experiment(tmp_path / "legacy", ("game_one",))
    (root / "games/game_one/logs/events.jsonl").write_text("")
    with pytest.raises(ExportInputError, match="no completed game_end"):
        inspect_experiment(root)


def test_unknown_error_and_repair_usage_remain_unknown_with_known_subtotal(tmp_path):
    tracker = ProviderUsageTracker(tmp_path / "metrics")
    budget = BudgetController(schema_version="1.0", experiment_id="test", artifact_root=tmp_path / "budget", config=BudgetConfig(max_provider_calls=3))
    class Provider:
        count = 0
        def generate(self, request):
            self.count += 1
            if self.count == 1:
                raise RuntimeError("no usage returned")
            return LLMResponse("1.0", "{}", "resolved", {"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    wrapper = BudgetedLLMProvider(inner=Provider(), budget=budget, usage_tracker=tracker,
                                 input_cost_per_1k_tokens=1, output_cost_per_1k_tokens=2)
    request = LLMRequest("1.0", "a", "reflect", "private text must not enter ledger", {"game_id": "g", "phase_id": "p", "attempt_index": 0})
    with pytest.raises(RuntimeError):
        wrapper.generate(request)
    wrapper.generate(replace(request, context={**request.context, "attempt_index": 1}))
    wrapper.generate(request)
    wrapper.generate(request)  # blocked, not a request
    totals = tracker.write_summary()["totals"]
    assert totals["provider_calls"] == 3
    assert totals["provider_errors"] == totals["repair_calls"] == totals["budget_blocked_attempts"] == 1
    assert totals["estimated_cost_usd"] is None
    assert totals["known_estimated_cost_usd"] == pytest.approx(0.04)
    assert totals["unknown_estimated_cost_usd_calls"] == 1
    assert budget.total_estimated_cost_usd is None
    assert "private text" not in (tmp_path / "metrics/provider_attempts.jsonl").read_text()


def test_eliminated_cleanup_usage_is_separate_without_guessing_savings(tmp_path):
    tracker = ProviderUsageTracker(tmp_path)
    tracker.eliminated.add(("g", "a"))
    tracker.append({"kind": "eliminated_skip", "agent_id": "a", "game_id": "g", "method": "reflect"})
    request = LLMRequest("1.0", "a", "game_end_cleanup", "", {"game_id": "g", "phase_id": "game_end_cleanup"})
    tracker.record(request, response=LLMResponse("1.0", "{}", "model", {"usage": {"prompt_tokens": 4, "completion_tokens": 2}}))
    result = tracker.summary()
    assert result["post_elimination_cleanup"]["provider_calls"] == 1
    assert result["post_elimination_cleanup"]["estimated_cost_usd"] is None
    assert result["totals"]["skipped_eliminated_invocations"] == 1
    assert "estimated_savings" not in result


@pytest.mark.parametrize("mutation", ["unknown_id", "duplicate", "scripted_provider", "missing_key"])
def test_preflight_rejects_before_folio_mutation(tmp_path, mutation, monkeypatch):
    cfg = population_config(tmp_path)
    raw = cfg.raw
    if mutation == "unknown_id":
        raw["runtime"]["llm_agent_ids"].append("unknown")
    elif mutation == "duplicate":
        raw["agents"][1]["agent_id"] = "cassandra_a"
    elif mutation == "scripted_provider":
        raw["runtime"]["llm_agent_ids"] = ["cassandra_a"]
    else:
        monkeypatch.delenv("MISSING_ACCEPTANCE_KEY", raising=False)
        raw["agents"][1]["provider_config"] = {"kind": "responses", "model": "offline-test", "api_key_env_var": "MISSING_ACCEPTANCE_KEY"}
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))
    agents = tmp_path / "agents"
    agents.mkdir()
    sentinel = agents / "sentinel.txt"
    sentinel.write_text("untouched")
    with pytest.raises((ValueError, RuntimeError)):
        cfg = load_population_experiment_config(path)
        PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=agents).run()
    assert list(agents.iterdir()) == [sentinel]
    assert sentinel.read_text() == "untouched"


def test_inherited_provider_reuse_change_fails_before_any_new_folio(tmp_path):
    cfg = population_config(tmp_path)
    raw = cfg.raw
    raw["agents"][0].pop("provider_config")
    path = tmp_path / "inherited.yaml"
    path.write_text(yaml.safe_dump(raw))
    cfg = load_population_experiment_config(path)
    runner = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents")
    runner.run(dry_run_override=True)
    before = {p.relative_to(tmp_path / "agents"): p.read_bytes() for p in (tmp_path / "agents").rglob("*") if p.is_file()}
    raw["runtime"]["provider_config"] = {"kind": "fake", "model": "changed-model"}
    path.write_text(yaml.safe_dump(raw))
    runner = PopulationExperimentRunner(config=load_population_experiment_config(path), base_dir=tmp_path, agents_root=tmp_path / "agents")
    with pytest.raises(FileExistsError, match="existing run or recovery evidence"):
        runner.run(dry_run_override=True)
    after = {p.relative_to(tmp_path / "agents"): p.read_bytes() for p in (tmp_path / "agents").rglob("*") if p.is_file()}
    assert before == after

    # Preserve direct coverage of inherited provider provenance without
    # attempting another launch against protected experiment evidence.
    raw["experiment_id"] = "acceptance_reuse_check"
    raw["agents"][0] = {"agent_id": "cassandra_a", "blueprint": str(Path("blueprints/cassandra.yaml").resolve())}
    path.write_text(yaml.safe_dump(raw))
    direct = PopulationExperimentRunner(config=load_population_experiment_config(path), base_dir=tmp_path,
                                       agents_root=tmp_path / "agents")
    with pytest.raises(ValueError, match="provider configuration mismatch"):
        direct.run(dry_run_override=True)
    after_reuse_check = {p.relative_to(tmp_path / "agents"): p.read_bytes()
                         for p in (tmp_path / "agents").rglob("*") if p.is_file()}
    assert before == after_reuse_check


@pytest.mark.parametrize("field,limit", [("max_input_tokens", 10), ("max_output_tokens", 5), ("max_estimated_cost_usd", 0.02)])
def test_known_usage_lower_bound_still_enforces_budget_after_unknown_error(tmp_path, field, limit):
    budget = BudgetController(schema_version="1.0", experiment_id="x", artifact_root=tmp_path, config=BudgetConfig(**{field: limit}))
    context = dict(game_id="g", phase_id="p", agent_id="a", method="reflect")
    budget.record_provider_error(**context, error="unknown billing")
    budget.record_after_call(**context, usage={"prompt_tokens": 10, "completion_tokens": 5, "estimated_cost_usd": 0.02})
    assert budget.total_estimated_cost_usd is None
    assert not budget.check_before_call(**context).allow_provider_call


def test_uncertain_reservation_survives_reopen_and_blocks_over_budget_retry(tmp_path):
    config = BudgetConfig(max_estimated_cost_usd=0.5, max_provider_calls=3)
    budget = BudgetController(schema_version="1.0", experiment_id="recover", artifact_root=tmp_path, config=config)
    class Timeout:
        def generate(self, request):
            raise TimeoutError("read timed out")
    wrapper = BudgetedLLMProvider(inner=Timeout(), budget=budget,
        provider_metadata={"max_output_tokens": 10}, input_cost_per_1k_tokens=1.0,
        output_cost_per_1k_tokens=1.0)
    request = LLMRequest("1.0", "a", "reflect", "short prompt", {"game_id": "g", "phase_id": "p"})
    with pytest.raises(TimeoutError):
        wrapper.generate(request)
    state = budget.status()
    assert len(state["pending_reservations"]) == 1
    assert state["total_estimated_cost_usd"] is None
    reopened = BudgetController(schema_version="1.0", experiment_id="recover", artifact_root=tmp_path, config=config)
    assert reopened.pending_cost_usd > 0
    assert not reopened.reserve_before_call(reservation_id="another", game_id="g", phase_id="p",
        agent_id="a", method="reflect", input_tokens=300, output_tokens=10, cost_usd=0.27).allow_provider_call


def test_budget_state_reopen_rejects_identity_or_config_mismatch(tmp_path):
    BudgetController(schema_version="1.0", experiment_id="recover", artifact_root=tmp_path,
                     config=BudgetConfig(max_provider_calls=2))
    with pytest.raises(ValueError, match="different experiment"):
        BudgetController(schema_version="1.0", experiment_id="other", artifact_root=tmp_path,
                         config=BudgetConfig(max_provider_calls=2))
    with pytest.raises(ValueError, match="different budget configuration"):
        BudgetController(schema_version="1.0", experiment_id="recover", artifact_root=tmp_path,
                         config=BudgetConfig(max_provider_calls=3))


def test_budget_state_missing_pending_aggregates_fails_closed_without_rewrite(tmp_path):
    config = BudgetConfig(max_estimated_cost_usd=1.0)
    budget = BudgetController(schema_version="1.0", experiment_id="corrupt", artifact_root=tmp_path, config=config)
    assert budget.reserve_before_call(reservation_id="r1", game_id="g", phase_id="p", agent_id="a",
        method="reflect", input_tokens=10, output_tokens=10, cost_usd=0.8).allow_provider_call
    path = tmp_path / "budget_state.json"
    state = json.loads(path.read_text())
    del state["pending_cost_usd"]
    damaged = json.dumps(state, sort_keys=True, indent=2)
    path.write_text(damaged)
    with pytest.raises(ValueError, match="incomplete"):
        BudgetController(schema_version="1.0", experiment_id="corrupt", artifact_root=tmp_path, config=config)
    assert path.read_text() == damaged


@pytest.mark.parametrize("field,value", [
    ("pending_input_tokens", -1), ("pending_output_tokens", float("inf")),
    ("pending_cost_usd", -0.1),
])
def test_budget_state_rejects_invalid_or_inconsistent_pending_aggregates(tmp_path, field, value):
    config = BudgetConfig(max_estimated_cost_usd=1.0)
    budget = BudgetController(schema_version="1.0", experiment_id="corrupt", artifact_root=tmp_path, config=config)
    budget.reserve_before_call(reservation_id="r1", game_id="g", phase_id="p", agent_id="a",
        method="reflect", input_tokens=10, output_tokens=10, cost_usd=0.8)
    path = tmp_path / "budget_state.json"
    state = json.loads(path.read_text())
    state[field] = value
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        BudgetController(schema_version="1.0", experiment_id="corrupt", artifact_root=tmp_path, config=config)


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": -10000, "completion_tokens": -10000},
    {"prompt_tokens": True, "completion_tokens": 2},
    {"prompt_tokens": 2, "completion_tokens": 2, "estimated_cost_usd": float("nan")},
    {"prompt_tokens": 2, "input_tokens": 3, "completion_tokens": 2},
    {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 999},
])
def test_malformed_usage_retains_reservation_and_never_reduces_known_cost(tmp_path, usage):
    config = BudgetConfig(max_estimated_cost_usd=1.0)
    budget = BudgetController(schema_version="1.0", experiment_id="bad-usage", artifact_root=tmp_path, config=config)
    budget.reserve_before_call(reservation_id="r1", game_id="g", phase_id="p", agent_id="a",
        method="reflect", input_tokens=20, output_tokens=10, cost_usd=0.4)
    budget.settle_reservation(reservation_id="r1", usage=usage)
    assert budget.known_estimated_cost_usd == 0
    assert budget.pending_reservations["r1"]["status"] == "uncertain"
    assert budget.pending_cost_usd == 0.4
    assert not budget.reserve_before_call(reservation_id="r2", game_id="g", phase_id="p", agent_id="a",
        method="reflect", input_tokens=10, output_tokens=5, cost_usd=0.7).allow_provider_call


@pytest.mark.parametrize("raw", [
    {"max_estimated_cost_usd": "0.01"}, {"max_provider_calls": "1"},
    {"max_provider_calls": True}, {"max_estimated_cost_usd": True},
    {"safe_reserve_usd_per_call": float("inf")},
])
def test_raw_budget_rejects_invalid_supplied_limits(tmp_path, raw):
    with pytest.raises(ValueError):
        BudgetController.from_raw(schema_version="1.0", experiment_id="invalid", artifact_root=tmp_path, raw=raw)


def test_unavailable_primary_route_does_not_consume_budget_before_alternate(tmp_path):
    from llm_diplomacy.agents.llm_provider import ProviderRoute, ProviderUnavailable, build_provider_chain, parse_llm_provider_config
    budget = BudgetController(schema_version="1.0", experiment_id="routes", artifact_root=tmp_path,
                              config=BudgetConfig(max_estimated_cost_usd=0.5, safe_reserve_usd_per_call=0.4))
    config = parse_llm_provider_config({"kind": "responses", "model": "primary", "fallback_providers": [
        {"kind": "responses", "model": "alternate", "max_output_tokens": 10},
    ]}, require_selection=True)
    def builder(route):
        if route.model == "primary":
            raise ValueError("missing configured credential")
        return FakeLLMProvider(model=route.model)
    providers, metadata = build_provider_chain(config, builder=builder)
    wrapped = [BudgetedLLMProvider(inner=item, budget=budget, provider_metadata=metadata[index],
        input_cost_per_1k_tokens=1.0, output_cost_per_1k_tokens=1.0) for index, item in enumerate(providers)]
    route = ProviderRoute(wrapped, metadata)
    request = LLMRequest("1.0", "a", "reflect", "x", {"game_id": "g", "phase_id": "p"})
    with pytest.raises(ProviderUnavailable):
        route.generate_route(request, 0)
    assert budget.total_provider_calls == 0
    assert budget.pending_cost_usd == 0
    response = route.generate_route(request, 1)
    assert response.model == "alternate"
    assert budget.total_provider_calls == 1
    assert budget.pending_cost_usd == 0.267


def test_provider_without_effective_output_ceiling_requires_safe_reserve_or_denies_token_budget(tmp_path):
    from llm_diplomacy.agents.llm_provider import build_provider_chain, parse_llm_provider_config
    config = parse_llm_provider_config({"kind": "responses", "model": "m", "max_output_tokens": 10,
        "supports_max_output_tokens": False, "pricing": {"input_cost_per_1k_tokens": 1.0,
        "output_cost_per_1k_tokens": 1.0}}, require_selection=True)
    providers, metadata = build_provider_chain(config, builder=lambda _cfg: FakeLLMProvider())
    assert metadata[0]["output_ceiling_tokens"] is None
    request = LLMRequest("1.0", "a", "reflect", "x", {"game_id": "g", "phase_id": "p"})
    denied_budget = BudgetController(schema_version="1.0", experiment_id="denied", artifact_root=tmp_path / "denied",
        config=BudgetConfig(max_estimated_cost_usd=1.0))
    denied = BudgetedLLMProvider(inner=providers[0], budget=denied_budget, provider_metadata=metadata[0],
        input_cost_per_1k_tokens=1.0, output_cost_per_1k_tokens=1.0)
    assert denied.generate(request).raw["budget_blocked"]
    assert denied_budget.total_provider_calls == 0

    token_budget = BudgetController(schema_version="1.0", experiment_id="tokens", artifact_root=tmp_path / "tokens",
        config=BudgetConfig(max_output_tokens=100, max_estimated_cost_usd=1.0, safe_reserve_usd_per_call=0.4))
    token_denied = BudgetedLLMProvider(inner=FakeLLMProvider(), budget=token_budget, provider_metadata=metadata[0],
        input_cost_per_1k_tokens=1.0, output_cost_per_1k_tokens=1.0)
    assert token_denied.generate(request).raw["budget_blocked"]
    assert token_budget.total_provider_calls == 0


def test_mock_http_routes_distinct_endpoints_styles_models_and_keys(tmp_path, monkeypatch):
    from llm_diplomacy.agents.llm_provider import build_provider_from_config, parse_llm_provider_config
    requests = []
    class Response:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps(self.payload).encode()
    def http(request, **kwargs):
        requests.append(request)
        body = json.loads(request.data)
        if request.full_url.endswith("/responses"):
            return Response({"model": body["model"] + "-resolved", "output_text": "{}", "usage": {"input_tokens": 3, "output_tokens": 2}})
        return Response({"model": body["model"] + "-resolved", "choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 1}})
    monkeypatch.setattr("urllib.request.urlopen", http)
    monkeypatch.setenv("OFFLINE_KEY_A", "test-a")
    monkeypatch.setenv("OFFLINE_KEY_B", "test-b")
    tracker = ProviderUsageTracker(tmp_path)
    for agent, kind, model, endpoint, env_name in (("a", "responses", "alpha", "https://a.invalid/v1", "OFFLINE_KEY_A"),
                                                 ("b", "openai_compatible", "beta", "https://b.invalid/v2", "OFFLINE_KEY_B")):
        cfg = parse_llm_provider_config({"kind": kind, "model": model, "base_url": endpoint, "api_key_env_var": env_name}, require_selection=True)
        provider = BudgetedLLMProvider(inner=build_provider_from_config(cfg), budget=None, usage_tracker=tracker, provider_metadata=cfg.metadata())
        provider.generate(LLMRequest("1.0", agent, "reflect", "private", {"game_id": "g", "phase_id": "p", "client_request_id": f"attempt-{agent}"}))
    assert [(r.full_url, r.get_header("Authorization").split(), json.loads(r.data)["model"]) for r in requests] == [
        ("https://a.invalid/v1/responses", ["Bearer", "test-a"], "alpha"),
        ("https://b.invalid/v2/chat/completions", ["Bearer", "test-b"], "beta")]
    assert [r.get_header("X-client-request-id") for r in requests] == ["attempt-a", "attempt-b"]
    result = tracker.write_summary()
    assert result["by_agent"]["a"]["observed_resolved_models"] == ["alpha-resolved"]
    assert result["by_agent"]["b"]["input_tokens"] == 4
    text = (tmp_path / "provider_attempts.jsonl").read_text()
    assert "test-a" not in text and "test-b" not in text and "private" not in text


def test_export_preserves_player_provenance_and_reader_projection_excludes_it(tmp_path):
    from llm_diplomacy.reporting.bundle_exporter import export_experiment
    from llm_diplomacy.reporting.report_projector import project_bundle
    root = _make_completed_experiment(tmp_path / "source", ("game_one",))
    path = root / "population_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["providers_by_agent"] = {"alice": {"requested_model": "model-a"}, "bob": {"requested_model": "model-b"}}
    path.write_text(json.dumps(manifest))
    bundle_path = export_experiment(root, tmp_path / "export")[0]
    bundle = json.loads(bundle_path.read_text())
    assert bundle["game"]["per_player_model_provenance"] == manifest["providers_by_agent"]
    projected = project_bundle(bundle, "postgame_public")
    assert "model-a" not in json.dumps(projected.bundle)
    assert "model-b" not in json.dumps(projected.bundle)


@pytest.mark.parametrize("agents", [
    [{"agent_id": "a", "runtime_kind": "llm", "provider_config": {"kind": "fake"}}, {"agent_id": "a"}],
    [{"agent_id": "../unsafe", "runtime_kind": "llm", "provider_config": {"kind": "fake"}}],
    [{"agent_id": "a", "runtime_kind": "scripted", "provider_config": {"kind": "fake"}}],
    [{"agent_id": "a", "runtime_kind": "llm", "provider_config": "fake"}],
])
def test_direct_runner_rejects_invalid_players_before_artifacts(tmp_path, agents):
    cfg = ExperimentConfig(schema_version="1.0", experiment_id="invalid", games=1, agents=agents, rule_engine={}, negotiation={}, seed=1, raw={})
    with pytest.raises(ValueError):
        ScriptedExperimentRunner(config=cfg, base_dir=tmp_path).run()
    assert not (tmp_path / "experiments").exists()
