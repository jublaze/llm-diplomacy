from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from llm_diplomacy.agents import (
    AgentContext,
    BaselineLLMAgentRuntime,
    ScriptedAgentRuntime,
)
from llm_diplomacy.agents.baseline_llm_runtime import FREE_TEXT_REFLECTION_PROTOCOL, PERSISTENCE_HANDOFF_VERSION
from llm_diplomacy.agents.llm_provider import (
    BudgetedLLMProvider,
    ProviderRoute,
    FakeLLMProvider,
    build_provider_chain,
    build_provider_from_config,
    canonical_provider_config,
    parse_llm_provider_config,
)
from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.guardrails import BudgetController
from llm_diplomacy.gm.game_master import GameMaster
from llm_diplomacy.metrics.cost import CostTracker
from llm_diplomacy.metrics.provider_usage import ProviderUsageTracker
from llm_diplomacy.metrics.leaderboard import Leaderboard
from llm_diplomacy.observability import ExperimentLogger
from llm_diplomacy.observatory.generator import build_game_observatory
from llm_diplomacy.core.prompt_assets import (
    OUTCOME_REVIEW_INSTRUCTION,
    OUTCOME_REVIEW_SHA256,
    OUTCOME_REVIEW_VERSION,
    canonical_diplomacy_primer_path,
    canonical_strategic_imperatives_path,
    canonical_victory_campaign_doctrine_path,
)
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine
from llm_diplomacy.runner.recovery import ExperimentRecovery


@dataclass(frozen=True)
class ScriptedRunSummary:
    schema_version: str
    experiment_id: str
    games_requested: int
    games_completed: int
    leaderboard_overall_path: str
    leaderboard_by_country_path: str
    cost_summary_path: str
    games_started: int = 0
    games_stopped: int = 0
    games_failed: int = 0
    status: str = "completed"


class ScriptedExperimentRunner:
    def __init__(
        self,
        *,
        config: ExperimentConfig,
        base_dir: str | Path,
        max_years: int = 1,
        negotiation_ticks: int = 1,
        recovery: ExperimentRecovery | None = None,
        resume: bool = False,
    ) -> None:
        self.config = config
        self.base_dir = Path(base_dir)
        self.max_years = max_years
        self.negotiation_ticks = negotiation_ticks
        self.recovery = recovery
        self.resume = resume
        self.rule_engine = DiplomacyRuleEngine(schema_version=config.schema_version)
        self.leaderboard = Leaderboard(schema_version=config.schema_version)
        self.cost_tracker = CostTracker(schema_version=config.schema_version)
        self.provider_usage: ProviderUsageTracker | None = None
        self.budget: BudgetController | None = None
        self._stop_experiment_early = False
        self.logger: ExperimentLogger | None = None
        self._budget_warned: set[str] = set()
        self._budget_stop_logged = False
        self._budget_milestones_emitted: set[str] = set()
        self._game_message_stats: dict[str, Any] = {}
        self._game_runtime_stats: dict[str, int] = {}
        self._eliminated_by_country: dict[str, dict[str, Any]] = {}
        self._systemic_failure_checked = False

    def run(self) -> ScriptedRunSummary:
        self._preflight_per_player_providers()
        canonical_experiment_root = self.base_dir / "experiments" / self.config.experiment_id
        experiment_root = (self.recovery.working_root / "experiments" / self.config.experiment_id
                           if self.recovery is not None else canonical_experiment_root)
        experiment_root.mkdir(parents=True, exist_ok=True)
        self.logger = ExperimentLogger(
            schema_version=self.config.schema_version,
            experiment_id=self.config.experiment_id,
            experiment_root=experiment_root,
        )
        self.provider_usage = ProviderUsageTracker(canonical_experiment_root / "metrics")
        if not self.resume or not (experiment_root / "manifest.json").exists():
            self._write_manifest(experiment_root)
        self.budget = BudgetController.from_raw(
            schema_version=self.config.schema_version,
            experiment_id=self.config.experiment_id,
            artifact_root=canonical_experiment_root / "metrics" / "budget",
            raw=(self.config.raw or {}).get("budget"),
        )
        self._emit_event(
            "experiment_started",
            games_requested=self.config.games,
            max_years=self.max_years,
            negotiation_ticks=self.negotiation_ticks,
            provider=self._provider_metadata(),
        )
        self._emit_budget_events()

        games_completed = games_started = games_stopped = games_failed = 0
        run_error = None
        try:
            for game_index in range(1, self.config.games + 1):
                if self._stop_experiment_early:
                    break
                game_id = f"game_{game_index:03d}"
                games_started += 1
                result = self._run_single_game(game_id=game_id, game_index=game_index)
                if result["result"] == "stopped":
                    games_stopped += 1
                else:
                    games_completed += 1
                self._emit_budget_events(game_id=game_id)
                if self.budget is not None and self.budget.stop_experiment:
                    self._stop_experiment_early = True
        except Exception as exc:
            run_error = exc
            games_failed += 1
            self._emit_event(
                "experiment_error",
                error=f"{exc.__class__.__name__}: {exc}",
                games_completed=games_completed,
            )

        leaderboard_dir = experiment_root / "leaderboard"
        overall_path, by_country_path = self.leaderboard.write_artifacts(leaderboard_dir)
        cost_path = self.cost_tracker.write_summary(experiment_root / "metrics" / "cost_summary.json")
        summary = ScriptedRunSummary(
            schema_version=self.config.schema_version,
            experiment_id=self.config.experiment_id,
            games_requested=self.config.games,
            games_completed=games_completed,
            leaderboard_overall_path=self._published_path(overall_path, experiment_root, canonical_experiment_root),
            leaderboard_by_country_path=self._published_path(by_country_path, experiment_root, canonical_experiment_root),
            cost_summary_path=self._published_path(cost_path, experiment_root, canonical_experiment_root),
            games_started=games_started, games_stopped=games_stopped, games_failed=games_failed,
            status="failed" if run_error else "stopped" if games_stopped or games_completed < self.config.games else "completed",
        )
        (experiment_root / "run_summary.json").write_text(
            json.dumps(asdict(summary), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        self.provider_usage.write_summary()
        if run_error is not None:
            raise run_error
        if summary.status == "stopped":
            self._emit_event(
                "experiment_stopped",
                reason=self.budget.stop_reason if self.budget else "incomplete_schedule",
                stop_policy=self.budget.status().get("stop_policy") if self.budget else None,
                completed_games=games_completed,
            )
        else:
            self._emit_event(
                "experiment_completed",
                completed_games=games_completed,
                games_requested=self.config.games,
            )
        if self.recovery is not None:
            self.recovery.commit_tree(
                staged_root=experiment_root,
                canonical_root=canonical_experiment_root,
                cursor={"game_index": 1, "unit": "game_completed"},
                state={"games_completed": games_completed}, applied=["game_001"],
            )
        return summary

    def _run_single_game(self, *, game_id: str, game_index: int) -> dict[str, Any]:
        game_base_dir = self.recovery.working_root if self.recovery is not None else self.base_dir
        game_root = game_base_dir / "experiments" / self.config.experiment_id / "games" / game_id
        replaying = self.resume and self.recovery is not None and (self.recovery.path / "initial_snapshot").is_dir()
        if game_root.exists() and not replaying:
            shutil.rmtree(game_root)
        self._reset_game_stats()
        gm = GameMaster(config=self.config, base_dir=game_base_dir, game_id=game_id)
        scripted_runtime = ScriptedAgentRuntime(
            schema_version=self.config.schema_version,
            runtime_root=gm.paths.game_root / "agents",
            map_def=self.rule_engine.map_def,
            seed=self.config.seed + game_index,
        )
        llm_provider_cfg = parse_llm_provider_config((self.config.raw or {}).get("llm_provider"))
        strict = self._strict_per_player_mode()
        provider_boot_error: str | None = None
        llm_runtimes: dict[str, BaselineLLMAgentRuntime] = {}
        provider_configs = self._provider_configs_by_agent()
        try:
            for agent_id, cfg in provider_configs.items():
                route_configs = (cfg, *cfg.fallback_providers)
                raw_providers, route_metadata = build_provider_chain(cfg, builder=build_provider_from_config)
                providers = [BudgetedLLMProvider(
                    inner=provider, budget=self.budget,
                    usage_tracker=self.provider_usage, provider_metadata=route_metadata[index],
                    input_cost_per_1k_tokens=route_configs[index].input_cost_per_1k_tokens,
                    output_cost_per_1k_tokens=route_configs[index].output_cost_per_1k_tokens,
                ) for index, provider in enumerate(raw_providers)]
                provider = ProviderRoute(providers, route_metadata) if len(providers) > 1 else providers[0]
                runtime = BaselineLLMAgentRuntime(
                    schema_version=self.config.schema_version,
                    runtime_root=gm.paths.game_root / "agents",
                    provider=provider,
                    source_folio_root=self._staged_folio_root((self.config.raw or {}).get("agent_folio_root")),
                    strategic_imperatives_path=(self.config.raw or {}).get("strategic_imperatives_path"),
                    victory_campaign_doctrine_path=(self.config.raw or {}).get("victory_campaign_doctrine_path"),
                    diplomacy_primer_path=(self.config.raw or {}).get("diplomacy_primer_path"),
                    provider_metadata={**route_metadata[0], "fallback_providers": route_metadata[1:]},
                    halt_on_issue=bool(((self.config.raw or {}).get("provider_failure_policy") or {}).get("strict_recovery", False)),
                    recovery=self.recovery,
                    reflection_protocol=str((self.config.raw or {}).get("reflection_protocol", FREE_TEXT_REFLECTION_PROTOCOL)),
                )
                llm_runtimes[agent_id] = runtime
        except Exception as exc:
            if strict:
                raise RuntimeError(f"Provider boot failure for {agent_id}: {exc.__class__.__name__}: {exc}") from exc
            provider_boot_error = f"{exc.__class__.__name__}: {exc}"
            fallback = FakeLLMProvider()
            fallback = BudgetedLLMProvider(inner=fallback, budget=self.budget, usage_tracker=self.provider_usage,
                                           provider_metadata={"provider_name": "fake", "requested_model": "fake-llm", "legacy_boot_substitution": True})
            llm_runtimes = {}
            for row in self.config.agents:
                if str(row.get("runtime_kind", "scripted")).strip().lower() == "llm":
                    llm_runtimes[str(row.get("agent_id"))] = BaselineLLMAgentRuntime(
                        schema_version=self.config.schema_version, runtime_root=gm.paths.game_root / "agents",
                        provider=fallback, source_folio_root=self._staged_folio_root((self.config.raw or {}).get("agent_folio_root")),
                        provider_metadata=llm_provider_cfg.metadata(),
                        halt_on_issue=bool(((self.config.raw or {}).get("provider_failure_policy") or {}).get("strict_recovery", False)),
                        recovery=self.recovery,
                        reflection_protocol=str((self.config.raw or {}).get("reflection_protocol", FREE_TEXT_REFLECTION_PROTOCOL)),
                    )
        llm_runtime = llm_runtimes
        if provider_boot_error is not None:
            (gm.paths.logs_dir / "provider_errors.jsonl").parent.mkdir(parents=True, exist_ok=True)
            with (gm.paths.logs_dir / "provider_errors.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "schema_version": self.config.schema_version,
                            "game_id": game_id,
                            "error": provider_boot_error,
                            "fallback_provider": "fake",
                        },
                        sort_keys=True,
                    )
                )
                handle.write("\n")
            self._emit_event(
                "experiment_error",
                game_id=game_id,
                error=provider_boot_error,
                fallback_provider="fake",
            )
            failure_cfg = (self.config.raw or {}).get("provider_failure_policy", {})
            stop_enabled = not isinstance(failure_cfg, dict) or bool(
                failure_cfg.get("stop_on_systemic_failure", True)
            )
            # Legacy real-provider pilots retain their recorded fake fallback;
            # a fake/stub boot failure is systemic because it has no credential recovery path.
            if stop_enabled and llm_provider_cfg.kind in {"fake", "env_stub"}:
                self._emit_event(
                    "systemic_provider_failure",
                    game_id=game_id,
                    phase_id=None,
                    provider_calls=0,
                    provider_errors=1,
                    provider=llm_provider_cfg.metadata(),
                    error=provider_boot_error,
                )
                raise RuntimeError(f"Systemic provider boot failure: {provider_boot_error}")
        runtime_by_agent: dict[str, str] = {}
        for row in self.config.agents:
            agent_id = str(row.get("agent_id", "")).strip()
            if not agent_id:
                continue
            runtime_kind = str(row.get("runtime_kind", "scripted")).strip().lower()
            if runtime_kind == "llm":
                llm_runtime[agent_id].register_agent(agent_id=agent_id)
                runtime_by_agent[agent_id] = "llm"
            else:
                scripted_runtime.register_agent(
                    agent_id=agent_id,
                    kind=str(row.get("scripted_kind", "hold")),
                )
                runtime_by_agent[agent_id] = "scripted"
        if any(kind == "llm" for kind in runtime_by_agent.values()) and not replaying:
            self._emit_prompt_assets_loaded(llm_runtime=next(iter(llm_runtime.values())), game_id=game_id)

        state = self.rule_engine.initial_state({})
        state["game_id"] = game_id
        if not replaying:
            if self.recovery is not None:
                self.recovery.check_safe_halt()
            gm.initialize_game_state(state)
            gm.start_game()
        assignments = self._power_assignments(game_index=game_index)
        if not self.resume:
            self._emit_event(
                "game_started",
                game_id=game_id,
                game_index=game_index,
                assignments=dict(sorted(assignments.items())),
            )
            if self.recovery is not None:
                self.recovery.save_initial_snapshot()

        stop_game_early = False
        try:
            for year in range(1901, 1901 + self.max_years):
                state = self._run_phase(
                    gm=gm,
                    scripted_runtime=scripted_runtime,
                    llm_runtime=llm_runtime,
                    runtime_by_agent=runtime_by_agent,
                    assignments=assignments,
                    state=state,
                    year=year,
                    season="spring",
                    phase="movement",
                )
                if self.budget is not None and self.budget.consume_stop_game_after_phase(game_id=game_id):
                    stop_game_early = True
                    break
                if self.budget is not None and self.budget.stop_experiment:
                    stop_game_early = True
                    self._stop_experiment_early = True
                    break
                if self._has_dislodged(state):
                    state = self._run_phase(
                        gm=gm,
                        scripted_runtime=scripted_runtime,
                        llm_runtime=llm_runtime,
                        runtime_by_agent=runtime_by_agent,
                        assignments=assignments,
                        state=state,
                        year=year,
                        season="spring",
                        phase="retreat",
                    )
                    if self.budget is not None and self.budget.consume_stop_game_after_phase(game_id=game_id):
                        stop_game_early = True
                        break
                    if self.budget is not None and self.budget.stop_experiment:
                        stop_game_early = True
                        self._stop_experiment_early = True
                        break
                if stop_game_early:
                    break
                state = self._run_phase(
                    gm=gm,
                    scripted_runtime=scripted_runtime,
                    llm_runtime=llm_runtime,
                    runtime_by_agent=runtime_by_agent,
                    assignments=assignments,
                    state=state,
                    year=year,
                    season="fall",
                    phase="movement",
                )
                solo_victor = self._fall_solo_victor(state)
                if self.budget is not None and self.budget.consume_stop_game_after_phase(game_id=game_id):
                    stop_game_early = True
                    if solo_victor is not None:
                        self._stop_experiment_early = True
                        stop_game_early = True
                        break
                    break
                if self.budget is not None and self.budget.stop_experiment:
                    stop_game_early = True
                    self._stop_experiment_early = True
                    if solo_victor is not None:
                        break
                    break
                if self._has_dislodged(state):
                    state = self._run_phase(
                        gm=gm,
                        scripted_runtime=scripted_runtime,
                        llm_runtime=llm_runtime,
                        runtime_by_agent=runtime_by_agent,
                        assignments=assignments,
                        state=state,
                        year=year,
                        season="fall",
                        phase="retreat",
                    )
                    solo_victor = self._fall_solo_victor(state)
                    if self.budget is not None and self.budget.consume_stop_game_after_phase(game_id=game_id):
                        stop_game_early = True
                        if solo_victor is not None:
                            self._stop_experiment_early = True
                            break
                        break
                    if self.budget is not None and self.budget.stop_experiment:
                        stop_game_early = True
                        self._stop_experiment_early = True
                        if solo_victor is not None:
                            break
                        break
                if solo_victor is not None:
                    break
                if stop_game_early:
                    break
                state = self._run_phase(
                    gm=gm,
                    scripted_runtime=scripted_runtime,
                    llm_runtime=llm_runtime,
                    runtime_by_agent=runtime_by_agent,
                    assignments=assignments,
                    state=state,
                    year=year,
                    season="winter",
                    phase="adjustment",
                )
                if self.budget is not None and self.budget.consume_stop_game_after_phase(game_id=game_id):
                    stop_game_early = True
                    break
                if self.budget is not None and self.budget.stop_experiment:
                    stop_game_early = True
                    self._stop_experiment_early = True
                    break
        except Exception as exc:
            self._emit_event(
                "game_error",
                game_id=game_id,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            raise

        result_payload = self._game_result_payload(
            state,
            assignments,
            stopped=(
                stop_game_early
                and self.budget is not None
                and bool(self.budget.stop_reason)
                and self._fall_solo_victor(state) is None
            ),
            stop_reason=self.budget.stop_reason if self.budget is not None else None,
        )
        self.leaderboard.record_game_result(
            experiment_id=self.config.experiment_id,
            game_id=game_id,
            payload=result_payload,
        )

        cleanup_payloads: list[dict[str, Any]] = []
        representative_country = self._representative_country_by_agent(assignments)
        for agent_id in sorted(set(assignments.values())):
            visible_state = self._agent_visible_state(gm=gm, state={**state, "game_id": game_id}, assignments=assignments, agent_id=agent_id)
            ctx = AgentContext(
                schema_version=self.config.schema_version,
                experiment_id=self.config.experiment_id,
                game_id=game_id,
                phase_id="game_end_cleanup",
                agent_id=agent_id,
                country_assignment=representative_country.get(agent_id),
                visible_state=visible_state,
            )
            country = representative_country.get(agent_id)
            eliminated = self._agent_is_eliminated(agent_id=agent_id, assignments=assignments)
            self._emit_event(
                "agent_invocation_started",
                game_id=gm.context.game_id,
                phase_id="game_end_cleanup",
                agent_id=agent_id,
                country=country,
                runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                method="game_end_cleanup",
                postgame_eliminated_cleanup=eliminated,
            )
            try:
                cleanup = self._agent_runtime(scripted_runtime, llm_runtime, runtime_by_agent, agent_id).game_end_cleanup(ctx)
            except Exception as exc:
                self._emit_event(
                    "agent_invocation_failed",
                    game_id=gm.context.game_id,
                    phase_id="game_end_cleanup",
                    agent_id=agent_id,
                    country=country,
                    runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                    method="game_end_cleanup",
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                raise
            self._log_agent_invocation_completed(
                gm=gm,
                phase_id="game_end_cleanup",
                agent_id=agent_id,
                country=country,
                runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                method="game_end_cleanup",
                result=cleanup,
            )
            cleanup_payloads.append({"agent_id": agent_id, "payload": cleanup.summary})
            self.cost_tracker.record_usage(
                experiment_id=self.config.experiment_id,
                game_id=game_id,
                agent_id=agent_id,
                phase_id="game_end_cleanup",
                usage=cleanup.usage,
            )
            if eliminated:
                self._emit_event(
                    "eliminated_agent_final_cleanup_completed",
                    game_id=game_id,
                    phase_id="game_end_cleanup",
                    agent_id=agent_id,
                    elimination=self._elimination_for_agent(agent_id=agent_id, assignments=assignments),
                    final_cleanup_usage=cleanup.usage,
                )
            self._emit_budget_events(game_id=game_id, phase_id="game_end_cleanup")
        if self.recovery is not None:
            self.recovery.check_safe_halt()
        gm.apply_game_end_cleanup(cleanup_payloads)
        if self.recovery is not None:
            self.recovery.check_safe_halt()
        gm.complete_phase("game_end", outcome={"schema_version": self.config.schema_version, "result": result_payload})
        outputs = (self.config.raw or {}).get("outputs", {})
        observatory_enabled = True
        if isinstance(outputs, dict):
            observatory_enabled = bool(outputs.get("observatory", True))
        if observatory_enabled:
            build_game_observatory(gm.paths.game_root)
            self._emit_event(
                "observatory_generated",
                game_id=game_id,
                observatory_path=self._relpath(gm.paths.game_root / "observatory" / "index.html"),
            )
        game_summary = self._build_game_summary(result_payload=result_payload)
        self._emit_event(
            "game_summary",
            game_id=game_id,
            **game_summary,
            log_lines=self._game_summary_lines(game_summary),
        )
        if result_payload.get("result") == "stopped" and self.budget is not None and self.budget.stop_reason:
            self._emit_event(
                "game_stopped",
                game_id=game_id,
                reason=self.budget.stop_reason,
                stop_policy=self.budget.status().get("stop_policy"),
            )
        else:
            self._emit_event(
                "game_completed",
                game_id=game_id,
                outcome=result_payload.get("result"),
                winners=result_payload.get("winners", []),
            )

        return result_payload

    def _run_phase(
        self,
        *,
        gm: GameMaster,
        scripted_runtime: ScriptedAgentRuntime,
        llm_runtime: BaselineLLMAgentRuntime,
        runtime_by_agent: dict[str, str],
        assignments: dict[str, str],
        state: dict[str, Any],
        year: int,
        season: str,
        phase: str,
    ) -> dict[str, Any]:
        phase_id = f"{year}_{season}_{phase}"
        if self.recovery is not None:
            self.recovery.check_safe_halt()
        phase_state = dict(state)
        phase_state["year"] = year
        phase_state["season"] = season
        phase_state["phase"] = phase
        if self.recovery is not None:
            self.recovery.check_safe_halt()
        gm.start_phase(phase_id)
        if self.recovery is not None:
            self.recovery.progress(cursor={"unit": "phase_started", "game_id": gm.context.game_id,
                                           "phase_id": phase_id},
                                   state={"year": year, "season": season, "phase": phase})
        self._emit_event(
            "phase_started",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            season=season,
            phase=phase,
            year=year,
        )

        if phase in {"movement", "adjustment"}:
            self._run_negotiation_tick(
                gm=gm,
                scripted_runtime=scripted_runtime,
                llm_runtime=llm_runtime,
                runtime_by_agent=runtime_by_agent,
                assignments=assignments,
                state=phase_state,
                phase_id=f"{year}_{season}_negotiation",
            )

        submitted: list[dict[str, Any]] = []
        submitted_by_country: dict[str, list[dict[str, Any]]] = {}
        contexts_by_country: dict[str, AgentContext] = {}
        order_sets_by_country: dict[str, Any] = {}
        for country, agent_id in sorted(assignments.items()):
            if self._country_is_eliminated(country):
                self._record_eliminated_skip(
                    gm=gm, phase_id=phase_id, agent_id=agent_id, country=country, method="submit_orders"
                )
                continue
            visible_state = self._agent_visible_state(
                gm=gm,
                state=phase_state,
                assignments=assignments,
                agent_id=agent_id,
            )
            ctx = AgentContext(
                schema_version=self.config.schema_version,
                experiment_id=self.config.experiment_id,
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                country_assignment=country,
                visible_state=visible_state,
            )
            contexts_by_country[country] = ctx
            self._emit_event(
                "agent_invocation_started",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                country=country,
                runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                method="submit_orders",
            )
            try:
                order_set = self._agent_runtime(scripted_runtime, llm_runtime, runtime_by_agent, agent_id).submit_orders(ctx)
            except Exception as exc:
                self._emit_event(
                    "agent_invocation_failed",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                    method="submit_orders",
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                raise
            submitted.extend(order_set.orders)
            submitted_by_country[country] = list(order_set.orders)
            order_sets_by_country[country] = order_set
            self._log_agent_invocation_completed(
                gm=gm,
                phase_id=phase_id,
                agent_id=agent_id,
                country=country,
                runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                method="submit_orders",
                result=order_set,
            )
            self.cost_tracker.record_usage(
                experiment_id=self.config.experiment_id,
                game_id=gm.context.game_id,
                agent_id=agent_id,
                phase_id=phase_id,
                usage=order_set.usage,
            )
            self._emit_budget_events(game_id=gm.context.game_id, phase_id=phase_id)

        validated = self.rule_engine.validate_orders(phase_state, submitted)
        self._emit_event(
            "orders_validated",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            submitted_count=len(submitted),
            validated_count=len(validated.get("validated_orders", []))
            if isinstance(validated, dict)
            else 0,
            invalid_count=len(validated.get("invalid_orders", []))
            if isinstance(validated, dict)
            else 0,
        )
        if bool(((self.config.raw or {}).get("provider_failure_policy") or {}).get("strict_recovery", False)):
            participants = self.rule_engine._participant_units(phase_state, phase)
            participants_by_id = {str(unit.get("unit_id")): unit for unit in participants
                                  if isinstance(unit, dict)}
            unavoidable_disbands: set[str] = set()
            if phase == "retreat":
                submitted_unit_refs = {str(row.get("unit_ref")) for row in submitted
                                       if isinstance(row, dict) and isinstance(row.get("unit_ref"), str)}
                for unit in participants:
                    if (isinstance(unit, dict) and str(unit.get("unit_id", "")) not in submitted_unit_refs
                            and not self._legal_retreat_destinations(phase_state, unit)):
                        unavoidable_disbands.add(str(unit.get("unit_id", "")))

            def correction_targets(current: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], bool]:
                rows = current.get("validated_orders", []) if isinstance(current, dict) else []
                rejected = [row for row in rows if isinstance(row, dict) and row.get("validation") != "accepted"
                            and not (phase == "retreat" and row.get("reason") == "missing_retreat_order"
                                     and row.get("unit_id") in unavoidable_disbands)]
                targets: dict[str, list[dict[str, Any]]] = {}
                for row in rejected:
                    unit = participants_by_id.get(str(row.get("unit_id", "")))
                    country = str((unit.get("power") or unit.get("owner") or "")) if unit else str(row.get("power", ""))
                    if not country or country not in assignments:
                        return {}, True
                    targets.setdefault(country, []).append(row)
                for country, order_set in order_sets_by_country.items():
                    recovery_meta = (order_set.explanation or {}).get("_recovery", {}) if order_set else {}
                    runtime_issue = recovery_meta.get("order_validation_issue") if isinstance(recovery_meta, dict) else None
                    if runtime_issue:
                        if recovery_meta.get("correction_used") and not (self.recovery is not None and self.recovery.repeat_received_invalid):
                            if self.recovery is not None and all(recovery_meta.get(key) for key in
                                                                 ("decision_id", "prompt_hash", "attempt_id")):
                                self.recovery.record_validation(
                                    decision_id=recovery_meta["decision_id"],
                                    prompt_hash=recovery_meta["prompt_hash"],
                                    attempt_id=recovery_meta["attempt_id"], valid=False,
                                    issue="corrective answer still failed runtime order validation",
                                )
                            return {}, True
                        targets.setdefault(country, []).append({"reason": "runtime_legality", "issue": runtime_issue})
                return targets, False

            targets, unattributable = correction_targets(validated)
            corrected_countries: list[str] = []
            # Defaults permit one correction; the opt-in policy repeats within total guards.
            while targets and not unattributable:
                for country in sorted(targets):
                    agent_id = assignments[country]
                    if runtime_by_agent.get(agent_id) != "llm":
                        unattributable = True
                        break
                    order_set = order_sets_by_country.get(country)
                    recovery_meta = (order_set.explanation or {}).get("_recovery", {}) if order_set else {}
                    if (isinstance(recovery_meta, dict) and recovery_meta.get("correction_used")
                            and not (self.recovery is not None and self.recovery.repeat_received_invalid)):
                        if self.recovery is not None and all(recovery_meta.get(key) for key in
                                                             ("decision_id", "prompt_hash", "attempt_id")):
                            self.recovery.record_validation(
                                decision_id=recovery_meta["decision_id"], prompt_hash=recovery_meta["prompt_hash"],
                                attempt_id=recovery_meta["attempt_id"], valid=False,
                                issue="corrective answer still failed engine order validation",
                            )
                        unattributable = True
                        break
                    if (not isinstance(recovery_meta, dict) or not recovery_meta.get("decision_id")
                            or not recovery_meta.get("prompt_hash") or not recovery_meta.get("attempt_id")):
                        unattributable = True
                        break
                    feedback = {
                        "phase": phase_id,
                        **({"parent_attempt_id": recovery_meta["attempt_id"]}
                           if self.recovery is not None and self.recovery.repeat_received_invalid else {}),
                        "engine_errors": targets[country],
                        "runtime_error": ((order_set.explanation or {}).get("_recovery", {})
                                          .get("order_validation_issue")),
                        "rejected_answer": recovery_meta.get("raw_content", ""),
                        "legal_options": recovery_meta.get("legal_order_context", {}),
                    }
                    if self.recovery is not None:
                        self.recovery.record_validation(
                            decision_id=recovery_meta["decision_id"], prompt_hash=recovery_meta["prompt_hash"],
                            attempt_id=recovery_meta["attempt_id"], valid=False,
                            issue="engine rejected or substituted one or more orders",
                        )
                    try:
                        corrected = llm_runtime.submit_orders(contexts_by_country[country],
                                                              correction_feedback=feedback)
                    except Exception:
                        raise
                    submitted = [row for other, rows in submitted_by_country.items() if other != country for row in rows]
                    submitted_by_country[country] = list(corrected.orders)
                    submitted.extend(corrected.orders)
                    order_sets_by_country[country] = corrected
                    corrected_countries.append(country)
                    self._log_agent_invocation_completed(
                        gm=gm, phase_id=phase_id, agent_id=agent_id, country=country,
                        runtime_kind="llm", method="submit_orders", result=corrected,
                    )
                    self.cost_tracker.record_usage(
                        experiment_id=self.config.experiment_id, game_id=gm.context.game_id,
                        agent_id=agent_id, phase_id=phase_id, usage=corrected.usage,
                    )
                    self._emit_budget_events(game_id=gm.context.game_id, phase_id=phase_id)
                if targets and not unattributable:
                    validated = self.rule_engine.validate_orders(phase_state, submitted)
                if self.recovery is None or not self.recovery.repeat_received_invalid:
                    break
                targets, unattributable = correction_targets(validated)
            validated_rows = validated.get("validated_orders", []) if isinstance(validated, dict) else []
            invalid_rows = [row for row in validated_rows if not isinstance(row, dict)
                            or row.get("validation") != "accepted"
                            if not (phase == "retreat" and isinstance(row, dict)
                                    and row.get("reason") == "missing_retreat_order"
                                    and row.get("unit_id") in unavoidable_disbands)]
            legal_explicit_retreat = True
            if phase == "retreat":
                for row in validated_rows:
                    if not isinstance(row, dict) or row.get("validation") != "accepted":
                        continue
                    effective = row.get("effective_order")
                    payload = effective.get("payload") if isinstance(effective, dict) else None
                    unit = participants_by_id.get(str(row.get("unit_id", "")))
                    if (isinstance(effective, dict) and effective.get("type") == "RETREAT"
                            and isinstance(payload, dict) and unit is not None
                            and payload.get("to") not in self._legal_retreat_destinations(phase_state, unit)):
                        legal_explicit_retreat = False
            for country in corrected_countries:
                corrected_set = order_sets_by_country[country]
                recovery_meta = (corrected_set.explanation or {}).get("_recovery", {})
                if not isinstance(recovery_meta, dict):
                    continue
                country_rejected = any(
                    not isinstance(row, dict) or row.get("validation") != "accepted"
                    for row in validated_rows
                    if (not isinstance(row, dict)
                        or str((participants_by_id.get(str(row.get("unit_id", ""))) or {}).get("power")
                               or (participants_by_id.get(str(row.get("unit_id", ""))) or {}).get("owner", "")) == country)
                ) or not legal_explicit_retreat
                if self.recovery is not None:
                    self.recovery.record_validation(
                        decision_id=recovery_meta["decision_id"], prompt_hash=recovery_meta["prompt_hash"],
                        attempt_id=recovery_meta["attempt_id"], valid=not country_rejected,
                        issue="corrected orders remain invalid" if country_rejected else None,
                    )
            valid_unit_ids = set(participants_by_id)
            submitted_refs: list[str] = []
            build_targets: list[tuple[str, str]] = []
            malformed_reference = False
            for row in submitted:
                if not isinstance(row, dict):
                    malformed_reference = True
                    continue
                order_type = str(row.get("type", "")).upper()
                if phase == "adjustment" and order_type == "BUILD":
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    province, power = payload.get("province"), row.get("power")
                    if not isinstance(province, str) or not province or not isinstance(power, str) or not power:
                        malformed_reference = True
                    else:
                        build_targets.append((power, province))
                    continue
                if phase == "adjustment" and order_type == "DISBAND":
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    ref = payload.get("unit_ref")
                else:
                    ref = row.get("unit_ref")
                if phase == "retreat" and order_type == "RETREAT" and ref is None:
                    if not isinstance(row.get("power"), str) or not any(
                            str(unit.get("power", "")) == row.get("power") for unit in participants_by_id.values()):
                        malformed_reference = True
                elif not isinstance(ref, str) or not ref:
                    malformed_reference = True
                else:
                    submitted_refs.append(ref)
            structural_problem = (
                malformed_reference or len(submitted_refs) != len(set(submitted_refs))
                or len(build_targets) != len(set(build_targets))
                or any(ref not in valid_unit_ids for ref in submitted_refs)
            )
            if invalid_rows or unattributable or structural_problem or not legal_explicit_retreat:
                if self.recovery is not None:
                    self.recovery.halt(reason="orders_rejected_or_substituted")
                raise RuntimeError("Recovery required before adjudication: invalid or substituted orders")
        if self.recovery is not None:
            self.recovery.check_safe_halt()
        resolution = self.rule_engine.resolve_phase(phase_state, validated)
        output_state = resolution["output_state"]
        if season == "fall" and (
            (phase == "movement" and not self._has_dislodged(output_state))
            or phase == "retreat"
        ):
            output_state = self._apply_fall_ownership(output_state)
            resolution["output_state"] = output_state
        board_summary = self._board_resolution_summary(
            phase_id=phase_id,
            input_state=phase_state,
            output_state=output_state,
            resolution=resolution,
            submitted_orders=submitted,
            validated_orders=validated,
        )
        output_state["latest_public_resolution"] = board_summary
        self._record_new_eliminations(
            game_id=gm.context.game_id,
            phase_id=phase_id,
            state=output_state,
            assignments=assignments,
        )
        snapshot = gm.snapshot_store.write(
            experiment_id=self.config.experiment_id,
            game_id=gm.context.game_id,
            phase_id=phase_id,
            snapshot_id=f"{phase_id}_state",
            payload=output_state,
            overwrite=True,
        )
        gm.event_log.append(
            event_type="snapshot_committed",
            experiment_id=self.config.experiment_id,
            game_id=gm.context.game_id,
            phase_id=phase_id,
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        self._write_phase_artifacts(
            gm=gm,
            phase_id=phase_id,
            submitted=submitted,
            validated=validated,
            resolution=resolution,
        )
        resolution_artifact = gm.paths.game_root / "phases" / phase_id / "resolution" / "resolution.json"
        self._emit_event(
            "board_resolution_summary",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            phase=phase,
            summary=board_summary,
            resolution_artifact_path=self._relpath(resolution_artifact),
            log_lines=self._board_summary_lines(board_summary),
        )
        self._emit_event(
            "phase_resolved",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            valid_orders=len(validated.get("validated_orders", []))
            if isinstance(validated, dict)
            else 0,
            invalid_orders=len(validated.get("invalid_orders", []))
            if isinstance(validated, dict)
            else 0,
            output_state_snapshot=self._relpath(resolution_artifact),
        )

        representative_country = self._representative_country_by_agent(assignments)
        should_reflect = self._fall_solo_victor(output_state) is None
        for agent_id in sorted(set(assignments.values())) if should_reflect else ():
            if self._agent_is_eliminated(agent_id=agent_id, assignments=assignments):
                self._record_eliminated_skip(
                    gm=gm,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=representative_country.get(agent_id),
                    method="reflect",
                )
                continue
            visible_state = self._agent_visible_state(
                gm=gm,
                state=output_state,
                assignments=assignments,
                agent_id=agent_id,
            )
            ctx = AgentContext(
                schema_version=self.config.schema_version,
                experiment_id=self.config.experiment_id,
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                country_assignment=representative_country.get(agent_id),
                visible_state=visible_state,
            )
            country = representative_country.get(agent_id)
            self._emit_event(
                "agent_invocation_started",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                country=country,
                runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                method="reflect",
            )
            try:
                reflection = self._agent_runtime(scripted_runtime, llm_runtime, runtime_by_agent, agent_id).reflect(ctx)
            except Exception as exc:
                self._emit_event(
                    "agent_invocation_failed",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                    method="reflect",
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                raise
            self._log_agent_invocation_completed(
                gm=gm,
                phase_id=phase_id,
                agent_id=agent_id,
                country=country,
                runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                method="reflect",
                result=reflection,
            )
            self.cost_tracker.record_usage(
                experiment_id=self.config.experiment_id,
                game_id=gm.context.game_id,
                agent_id=agent_id,
                phase_id=f"{phase_id}_reflect",
                usage=reflection.usage,
            )
            self._emit_budget_events(game_id=gm.context.game_id, phase_id=phase_id)
            reflection_file = gm.paths.game_root / "phases" / phase_id / "reflection" / f"{agent_id}.json"
            reflection_file.parent.mkdir(parents=True, exist_ok=True)
            reflection_file.write_text(
                json.dumps(
                    {
                        **asdict(reflection),
                        "outcome_review_target_phase_id": self._public_resolution_phase_id(visible_state),
                    },
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )

        if self.recovery is not None:
            self.recovery.check_safe_halt()
        gm.complete_phase(
            phase_id,
            outcome={
                "schema_version": self.config.schema_version,
                "snapshot_id": snapshot.snapshot_id,
                "phase": phase,
            },
        )
        self._emit_event(
            "phase_completed",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            snapshot_id=snapshot.snapshot_id,
            phase=phase,
        )
        if self.recovery is not None:
            self.recovery.progress(cursor={"unit": "phase_completed", "game_id": gm.context.game_id,
                                           "phase_id": phase_id},
                                   state={"year": year, "season": season, "phase": phase,
                                          "board_hash": hashlib.sha256(json.dumps(output_state, sort_keys=True).encode("utf-8")).hexdigest()})
        return output_state

    def _legal_retreat_destinations(self, state: dict[str, Any], unit: dict[str, Any]) -> list[str]:
        """Match the engine resolver without considering other retreats' choices."""
        active_occupancy = {
            str(row.get("province", "")) for row in state.get("units", [])
            if isinstance(row, dict) and row.get("status", "active") == "active"
        }
        standoffs = {str(value) for value in state.get("standoff_provinces", []) if isinstance(value, str)}
        candidates = [node for province_id, province in self.rule_engine.province_by_id.items()
                      for node in (province_id, *province.coasts)]
        legal = []
        for node in candidates:
            if not self.rule_engine._is_legal_move(unit, str(unit.get("province", "")), node):
                continue
            destination = self.rule_engine._node_to_province(node)
            if destination in active_occupancy or destination in standoffs:
                continue
            if (destination == unit.get("dislodged_from")
                    and not unit.get("dislodged_by_convoy", False)):
                continue
            legal.append(node)
        return sorted(set(legal))

    def _run_negotiation_tick(
        self,
        *,
        gm: GameMaster,
        scripted_runtime: ScriptedAgentRuntime,
        llm_runtime: BaselineLLMAgentRuntime,
        runtime_by_agent: dict[str, str],
        assignments: dict[str, str],
        state: dict[str, Any],
        phase_id: str,
    ) -> None:
        representatives = self._representative_country_by_agent(assignments)
        self._emit_event(
            "phase_started",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            phase="negotiation",
        )
        for tick in range(1, self.negotiation_ticks + 1):
            active_agent_ids = self._active_runtime_participants(assignments=assignments)
            self._emit_event(
                "negotiation_tick_participants",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                tick=tick,
                active_agent_ids=active_agent_ids,
                active_participant_count=len(active_agent_ids),
            )
            for agent_id in sorted(set(assignments.values())):
                if agent_id not in active_agent_ids:
                    self._record_eliminated_skip(
                        gm=gm,
                        phase_id=phase_id,
                        agent_id=agent_id,
                        country=representatives.get(agent_id),
                        method="negotiate_tick",
                        tick=tick,
                    )
            tick_llm_calls = 0
            tick_llm_provider_errors = 0
            for agent_id in active_agent_ids:
                visible_state = self._agent_visible_state(
                    gm=gm,
                    state=state,
                    assignments=assignments,
                    agent_id=agent_id,
                )
                ctx = AgentContext(
                    schema_version=self.config.schema_version,
                    experiment_id=self.config.experiment_id,
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country_assignment=representatives.get(agent_id),
                    visible_state={**visible_state, **({"_recovery_occurrence": f"tick-{tick}"}
                                   if self.recovery is not None else {})},
                )
                country = representatives.get(agent_id)
                self._emit_event(
                    "agent_invocation_started",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                    method="negotiate_tick",
                    tick=tick,
                )
                try:
                    tick_result = self._agent_runtime(scripted_runtime, llm_runtime, runtime_by_agent, agent_id).negotiate_tick(ctx)
                except Exception as exc:
                    self._emit_event(
                        "agent_invocation_failed",
                        game_id=gm.context.game_id,
                        phase_id=phase_id,
                        agent_id=agent_id,
                        country=country,
                        runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                        method="negotiate_tick",
                        tick=tick,
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
                    raise
                if self.recovery is not None:
                    self.recovery.check_safe_halt()
                gm.stage_negotiation_tick_outputs(
                    phase_id=phase_id,
                    tick_index=tick,
                    agent_id=agent_id,
                    messages=tick_result.messages,
                )
                self._log_agent_invocation_completed(
                    gm=gm,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    runtime_kind=runtime_by_agent.get(agent_id, "scripted"),
                    method="negotiate_tick",
                    result=tick_result,
                    tick=tick,
                )
                if runtime_by_agent.get(agent_id) == "llm":
                    tick_llm_calls += 1
                    _output_path, tick_payload = self._read_phase_output_payload(
                        gm=gm,
                        agent_id=agent_id,
                        phase_id=phase_id,
                        method="negotiate_tick",
                    )
                    tick_meta = tick_payload.get("llm_meta", {}) if isinstance(tick_payload, dict) else {}
                    if isinstance(tick_meta, dict) and tick_meta.get("provider_error"):
                        tick_llm_provider_errors += 1
                self.cost_tracker.record_usage(
                    experiment_id=self.config.experiment_id,
                    game_id=gm.context.game_id,
                    agent_id=agent_id,
                    phase_id=f"{phase_id}_tick_{tick}",
                    usage=tick_result.usage,
                )
                self._emit_budget_events(game_id=gm.context.game_id, phase_id=phase_id)
            if tick == 1 and not self._systemic_failure_checked:
                self._systemic_failure_checked = True
                failure_cfg = (self.config.raw or {}).get("provider_failure_policy", {})
                stop_enabled = not isinstance(failure_cfg, dict) or bool(
                    failure_cfg.get("stop_on_systemic_failure", True)
                )
                if stop_enabled and tick_llm_calls > 0 and tick_llm_provider_errors == tick_llm_calls:
                    self._stop_experiment_early = True
                    self._emit_event(
                        "systemic_provider_failure",
                        game_id=gm.context.game_id,
                        phase_id=phase_id,
                        tick=tick,
                        provider_calls=tick_llm_calls,
                        provider_errors=tick_llm_provider_errors,
                        provider=self._provider_metadata(),
                    )
                    raise RuntimeError(
                        "Systemic provider failure: all LLM invocations in the first negotiation tick failed."
                    )
            if self.recovery is not None:
                self.recovery.check_safe_halt()
            committed = gm.commit_negotiation_tick(
                phase_id=phase_id,
                tick_index=tick,
                active_agent_ids=active_agent_ids,
            )
            released_private = 0
            released_global = 0
            for row in committed:
                target = row.get("to", {}) if isinstance(row, dict) else {}
                if not isinstance(target, dict):
                    continue
                from_agent_id = str(row.get("from_agent_id", ""))
                if str(target.get("type", "")) == "global":
                    released_global += 1
                    self._record_message_stat(
                        from_agent_id=from_agent_id,
                        target_type="global",
                        to_agent_id=None,
                    )
                elif str(target.get("type", "")) == "private":
                    released_private += 1
                    to_agent_id = str(target.get("to_agent_id", ""))
                    self._record_message_stat(
                        from_agent_id=from_agent_id,
                        target_type="private",
                        to_agent_id=to_agent_id,
                    )
            self._emit_event(
                "message_committed",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                tick=tick,
                released_count=len(committed),
                released_global=released_global,
                released_private=released_private,
                global_artifact_path=self._relpath(gm.paths.global_channel_file),
                private_artifact_root=self._relpath(gm.paths.private_messages_dir),
            )
        self._emit_event(
            "phase_completed",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            phase="negotiation",
        )

    def _write_phase_artifacts(
        self,
        *,
        gm: GameMaster,
        phase_id: str,
        submitted: list[dict[str, Any]],
        validated: dict[str, Any],
        resolution: dict[str, Any],
    ) -> None:
        phase_dir = gm.paths.game_root / "phases" / phase_id
        submitted_path = phase_dir / "orders" / "submitted_orders.json"
        validated_path = phase_dir / "orders" / "validated_orders.json"
        resolution_dir = phase_dir / "resolution"
        resolution_path = resolution_dir / "resolution.json"
        resolution_dir.mkdir(parents=True, exist_ok=True)
        submitted_path.parent.mkdir(parents=True, exist_ok=True)

        submitted_payload = {
            "schema_version": self.config.schema_version,
            "phase_id": phase_id,
            "orders": sorted(submitted, key=lambda row: str(row.get("order_id", ""))),
        }
        validated_payload = {
            "schema_version": self.config.schema_version,
            "phase_id": phase_id,
            "orders": validated.get("validated_orders", []),
        }
        submitted_path.write_text(json.dumps(submitted_payload, sort_keys=True, indent=2), encoding="utf-8")
        validated_path.write_text(json.dumps(validated_payload, sort_keys=True, indent=2), encoding="utf-8")
        resolution_path.write_text(json.dumps(resolution, sort_keys=True, indent=2), encoding="utf-8")

    def _power_assignments(self, *, game_index: int) -> dict[str, str]:
        powers = list(self.rule_engine.map_def.powers)
        agent_ids = [str(row.get("agent_id", "")) for row in self.config.agents if str(row.get("agent_id", ""))]
        if not agent_ids:
            raise ValueError("ScriptedExperimentRunner requires at least one configured agent_id.")
        games_cfg = (self.config.raw or {}).get("games")
        assignment_mode = "fixed"
        seeded_base = self.config.seed
        if isinstance(games_cfg, dict):
            assignment_mode = str(games_cfg.get("country_assignment", "fixed")).strip().lower()
            if isinstance(games_cfg.get("seed"), int):
                seeded_base = int(games_cfg["seed"])

        ordered_agents = list(agent_ids)
        if assignment_mode == "round_robin" and ordered_agents:
            shift = (game_index - 1) % len(ordered_agents)
            ordered_agents = ordered_agents[shift:] + ordered_agents[:shift]
        elif assignment_mode == "seeded_shuffle":
            rng = random.Random(seeded_base + game_index - 1)
            rng.shuffle(ordered_agents)
        elif assignment_mode != "fixed":
            raise ValueError(f"Unsupported country assignment mode: {assignment_mode}")

        return {power: ordered_agents[idx % len(ordered_agents)] for idx, power in enumerate(powers)}

    def _representative_country_by_agent(self, assignments: dict[str, str]) -> dict[str, str]:
        by_agent: dict[str, str] = {}
        for country, agent_id in sorted(assignments.items()):
            by_agent.setdefault(agent_id, country)
        return by_agent

    def _has_dislodged(self, state: dict[str, Any]) -> bool:
        return any(bool(unit.get("must_retreat")) or unit.get("status") == "dislodged" for unit in state.get("units", []))

    def _fall_solo_victor(self, state: dict[str, Any]) -> str | None:
        if state.get("season") != "fall" or state.get("phase") not in {"movement", "retreat"}:
            return None
        # Fall ownership is provisional until every dislodged unit has resolved
        # its retreat or disband. Do not terminate on a movement snapshot that
        # still contains pending retreats.
        if self._has_dislodged(state):
            return None
        ownership = state.get("supply_ownership", {})
        if not isinstance(ownership, dict):
            return None
        counts: dict[str, int] = {}
        for owner in ownership.values():
            if isinstance(owner, str) and owner:
                counts[owner] = counts.get(owner, 0) + 1
        winners = sorted(power for power, centers in counts.items() if centers >= 18)
        return winners[0] if len(winners) == 1 else None

    def _record_new_eliminations(
        self,
        *,
        game_id: str,
        phase_id: str,
        state: dict[str, Any],
        assignments: dict[str, str],
    ) -> None:
        """Record only completed-adjudication eliminations; centers never decide this."""
        live_owners = {
            str(unit.get("owner", ""))
            for unit in state.get("units", [])
            if isinstance(unit, dict) and unit.get("status") in {"active", "dislodged"}
        }
        for country, agent_id in sorted(assignments.items()):
            if country in live_owners or country in self._eliminated_by_country:
                continue
            record = {
                "lifecycle_version": "elimination_aware_runtime_v1",
                "elimination_phase_id": phase_id,
                "reason": "authoritative_adjudicated_state_zero_active_units",
                "agent_id": agent_id,
                "country": country,
                "legacy_artifact": False,
                "live_provider_calls_skipped": 0,
                "estimated_avoided_calls": 0,
                "estimated_avoided_cost_usd": None,
            }
            self._eliminated_by_country[country] = record
            if self.provider_usage is not None and self._agent_is_eliminated(agent_id=assignments[country], assignments=assignments):
                self.provider_usage.eliminated.add((game_id, assignments[country]))
            self._emit_event("power_eliminated", game_id=game_id, phase_id=phase_id, elimination=record)

    def _country_is_eliminated(self, country: str) -> bool:
        return country in self._eliminated_by_country

    def _agent_is_eliminated(self, *, agent_id: str, assignments: dict[str, str]) -> bool:
        owned = [country for country, owner in assignments.items() if owner == agent_id]
        return bool(owned) and all(self._country_is_eliminated(country) for country in owned)

    def _active_runtime_participants(self, *, assignments: dict[str, str]) -> list[str]:
        """Single game-scoped participant set for dispatch and negotiation barriers."""
        return [
            agent_id
            for agent_id in sorted(set(assignments.values()))
            if not self._agent_is_eliminated(agent_id=agent_id, assignments=assignments)
        ]

    def _elimination_for_agent(self, *, agent_id: str, assignments: dict[str, str]) -> list[dict[str, Any]]:
        return [self._eliminated_by_country[c] for c, owner in sorted(assignments.items()) if owner == agent_id and c in self._eliminated_by_country]

    def _record_eliminated_skip(
        self,
        *,
        gm: GameMaster,
        phase_id: str,
        agent_id: str,
        country: str | None,
        method: str,
        tick: int | None = None,
    ) -> None:
        if country and country in self._eliminated_by_country:
            records = [self._eliminated_by_country[country]]
        else:
            records = self._elimination_for_agent(agent_id=agent_id, assignments={})
        if self.provider_usage is not None:
            self.provider_usage.append({"kind": "eliminated_skip", "game_id": gm.context.game_id, "phase_id": phase_id, "agent_id": agent_id, "method": method})
        for record in records:
            record["estimated_avoided_calls"] = int(record.get("estimated_avoided_calls", 0)) + 1
            record["live_provider_calls_skipped"] = int(record.get("live_provider_calls_skipped", 0)) + 1
        self._emit_event(
            "agent_invocation_skipped_eliminated",
            game_id=gm.context.game_id,
            phase_id=phase_id,
            agent_id=agent_id,
            country=country,
            method=method,
            tick=tick,
            elimination_lifecycle_version="elimination_aware_runtime_v1",
            legacy_artifact=False,
            skipped_provider_call=True,
            elimination_records=records,
        )

    def _apply_fall_ownership(self, state: dict[str, Any]) -> dict[str, Any]:
        updated = dict(state)
        ownership = dict(updated.get("supply_ownership", {}))
        province_by_id = {province.id: province for province in self.rule_engine.map_def.provinces}
        for unit in updated.get("units", []):
            if unit.get("status") != "active":
                continue
            province = str(unit.get("province", ""))
            province_meta = province_by_id.get(province)
            if province_meta is None or not province_meta.supply_center:
                continue
            ownership[province] = str(unit.get("owner", ""))
        updated["supply_ownership"] = ownership
        return updated

    def _game_result_payload(
        self,
        state: dict[str, Any],
        assignments: dict[str, str],
        *,
        stopped: bool = False,
        stop_reason: str | None = None,
    ) -> dict[str, Any]:
        ownership = state.get("supply_ownership", {}) or {}
        country_results: dict[str, dict[str, Any]] = {}
        for power, agent_id in sorted(assignments.items()):
            owned = sum(owner == power for owner in ownership.values())
            country_results[power] = {"agent_id": agent_id, "owned_centers": owned}
        solo_power = self._fall_solo_victor(state)
        solo_row = country_results.get(solo_power) if solo_power else None
        solo_victor = (
            {"power": solo_power, "agent_id": solo_row["agent_id"], "owned_centers": solo_row["owned_centers"]}
            if solo_row else None
        )
        sorted_countries = sorted(
            country_results.items(),
            key=lambda row: (-row[1]["owned_centers"], row[0]),
        )
        leader_centers = sorted_countries[0][1]["owned_centers"] if sorted_countries else 0
        cutoff_leaders = [
            {"power": power, **entry}
            for power, entry in sorted_countries
            if entry["owned_centers"] == leader_centers
        ] if not stopped and solo_victor is None else []
        for power, entry in country_results.items():
            if solo_victor is not None:
                entry["result"] = "win" if power == solo_power else "not_won"
            elif cutoff_leaders and power in {row["power"] for row in cutoff_leaders}:
                entry["result"] = "leader_at_cutoff"
            else:
                entry["result"] = "not_won" if not stopped else "incomplete"
        winners = [solo_victor["agent_id"]] if solo_victor else []
        result = "solo_victory" if solo_victor else "stopped" if stopped else "completed"
        payload = {
            "schema_version": self.config.schema_version,
            "result": result,
            "termination_reason": "solo_victory" if solo_victor else stop_reason if stopped else "scheduled_max_year",
            "budget_fallback_used": bool(self.budget and self.budget.force_fallback),
            "winners": winners,
            "country_results": country_results,
            "cutoff_leaders": cutoff_leaders,
            "solo_victor": solo_victor,
        }
        if solo_victor:
            payload["final_phase_id"] = f"{state['year']}_{state['season']}_{state['phase']}"
        if stopped:
            payload["partial"] = True
            payload["stop_reason"] = stop_reason
        return payload

    def _write_manifest(self, experiment_root: Path) -> None:
        manifest = {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "games": self.config.games,
            "agents": self.config.agents,
            "seed": self.config.seed,
            "runner": "scripted",
            "max_years": self.max_years,
            "provider": self._provider_metadata(),
        }
        (experiment_root / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _agent_runtime(
        self,
        scripted_runtime: ScriptedAgentRuntime,
        llm_runtime: BaselineLLMAgentRuntime | dict[str, BaselineLLMAgentRuntime],
        runtime_by_agent: dict[str, str],
        agent_id: str,
    ) -> Any:
        if runtime_by_agent.get(agent_id) != "llm":
            return scripted_runtime
        if isinstance(llm_runtime, dict):
            return llm_runtime[agent_id]
        return llm_runtime

    def _strict_per_player_mode(self) -> bool:
        global_provider = (self.config.raw or {}).get("llm_provider")
        native_global = isinstance(global_provider, dict) and str(global_provider.get("kind", "")).strip().lower() == "codex_cli"
        native_player = any(
            isinstance(row.get("provider_config"), dict)
            and str(row["provider_config"].get("kind", "")).strip().lower() == "codex_cli"
            for row in self.config.agents
        )
        return native_global or native_player or bool((self.config.raw or {}).get("per_player_provider_mode")) or any(
            row.get("provider_config") is not None for row in self.config.agents
        )

    def _provider_configs_by_agent(self) -> dict[str, Any]:
        global_raw = (self.config.raw or {}).get("llm_provider")
        configs: dict[str, Any] = {}
        for row in self.config.agents:
            if str(row.get("runtime_kind", "scripted")).strip().lower() != "llm":
                continue
            agent_id = str(row.get("agent_id", "")).strip()
            raw = row.get("provider_config") if isinstance(row.get("provider_config"), dict) else global_raw
            configs[agent_id] = parse_llm_provider_config(
                raw, require_selection=isinstance(row.get("provider_config"), dict)
            )
        return configs

    def _staged_folio_root(self, value: Any) -> str | None:
        if value is None:
            return None
        if self.recovery is None:
            return str(value)
        candidate = Path(str(value)).resolve()
        working = self.recovery.working_root.resolve()
        if candidate == working or working in candidate.parents:
            return str(candidate)
        canonical = (self.base_dir / "experiments" / self.config.experiment_id).resolve()
        try:
            relative = candidate.relative_to(canonical)
        except ValueError as exc:
            raise ValueError("strict recovery requires agent_folio_root inside the experiment so it can be staged") from exc
        return str((self.recovery.working_root / "experiments" / self.config.experiment_id / relative).resolve())

    def _preflight_per_player_providers(self) -> None:
        if not self._strict_per_player_mode():
            return
        seen = set()
        for row in self.config.agents:
            agent_id = row.get("agent_id", "")
            if not isinstance(agent_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", agent_id):
                raise ValueError("Unsafe player agent_id")
            if agent_id in seen:
                raise ValueError(f"Duplicate player agent_id: {agent_id}")
            seen.add(agent_id)
            raw = row.get("provider_config")
            if raw is not None:
                if not isinstance(raw, dict):
                    raise ValueError("provider_config must be an object")
                if str(row.get("runtime_kind", "scripted")).lower() != "llm":
                    raise ValueError("provider_config is not allowed on scripted players")
                parse_llm_provider_config(raw, require_selection=True)
        for agent_id, cfg in self._provider_configs_by_agent().items():
            try:
                build_provider_chain(cfg, builder=build_provider_from_config)
            except Exception as exc:
                raise RuntimeError(
                    f"Provider preflight failed for {agent_id}: {exc.__class__.__name__}: {exc}"
                ) from exc

    def _agent_visible_state(
        self,
        *,
        gm: GameMaster,
        state: dict[str, Any],
        assignments: dict[str, str],
        agent_id: str,
    ) -> dict[str, Any]:
        visible = dict(state)
        visible["game_id"] = gm.context.game_id
        visible["power_to_agent"] = dict(sorted(assignments.items()))
        visible["agent_to_power"] = {value: key for key, value in assignments.items()}
        visible["private_messages"] = self._read_inbox_for_agent(gm, agent_id)
        visible["global_messages"] = self._read_global_messages(gm)
        return visible

    def _read_inbox_for_agent(self, gm: GameMaster, agent_id: str) -> list[dict[str, Any]]:
        inbox = gm.paths.private_messages_dir / agent_id / "inbox.jsonl"
        if not inbox.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in inbox.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _read_global_messages(self, gm: GameMaster) -> list[dict[str, Any]]:
        path = gm.paths.global_channel_file
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _emit_event(self, event_type: str, **fields: Any) -> None:
        if self.logger is None:
            return
        self.logger.event(event_type, **fields)

    def _relpath(self, path: str | Path) -> str:
        if self.logger is None:
            return str(path).replace("\\", "/")
        return self.logger.relpath(path)

    def _published_path(self, path: Path, staged_root: Path, canonical_root: Path) -> str:
        if self.recovery is None:
            return str(path)
        return str((canonical_root / path.resolve().relative_to(staged_root.resolve())).resolve())

    def _emit_prompt_assets_loaded(self, *, llm_runtime: BaselineLLMAgentRuntime, game_id: str) -> None:
        strategic_path = llm_runtime.context_builder.strategic_imperatives_path
        doctrine_path = llm_runtime.context_builder.victory_campaign_doctrine_path
        primer_path = llm_runtime.context_builder.diplomacy_primer_path
        strategic_meta = self._prompt_asset_file_metadata(path=strategic_path)
        doctrine_meta = self._prompt_asset_file_metadata(path=doctrine_path)
        primer_meta = self._prompt_asset_file_metadata(path=primer_path)
        self._emit_event(
            "prompt_assets_loaded",
            game_id=game_id,
            prompt_profile="default",
            prompt_assets={
                "strategic_imperatives": strategic_meta,
                "victory_campaign_doctrine": doctrine_meta,
                "diplomacy_primer": primer_meta,
                "outcome_review": {
                    "outcome_review_version": OUTCOME_REVIEW_VERSION,
                    "sha256": OUTCOME_REVIEW_SHA256,
                },
                "persistent_memory_handoff": {
                    "version": PERSISTENCE_HANDOFF_VERSION,
                    "canonical_source": "agent_folio_root/<agent_id>/persistent_memory.md",
                    "runtime_audit_copy": "games/<game_id>/agents/<agent_id>/folio/persistent_memory.md",
                },
            },
        )

    def _read_phase_output_payload(
        self,
        *,
        gm: GameMaster,
        agent_id: str,
        phase_id: str,
        method: str,
    ) -> tuple[Path, dict[str, Any]]:
        path = gm.paths.game_root / "agents" / agent_id / "runtime" / "phase_outputs" / f"{phase_id}_{method}.json"
        if not path.exists():
            return path, {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return path, payload
        return path, {}

    def _log_agent_invocation_completed(
        self,
        *,
        gm: GameMaster,
        phase_id: str,
        agent_id: str,
        country: str | None,
        runtime_kind: str,
        method: str,
        result: Any,
        tick: int | None = None,
    ) -> None:
        output_path, output_payload = self._read_phase_output_payload(
            gm=gm,
            agent_id=agent_id,
            phase_id=phase_id,
            method=method,
        )
        llm_meta = output_payload.get("llm_meta", {}) if isinstance(output_payload, dict) else {}
        if not isinstance(llm_meta, dict):
            llm_meta = {}
        is_llm = runtime_kind == "llm"
        prompt = llm_meta.get("prompt") if is_llm else None
        prompt_size_chars = llm_meta.get("prompt_size_chars") if is_llm else None
        estimated_input_tokens = llm_meta.get("estimated_input_tokens") if is_llm else None
        prompt_hash = None
        checks = None
        prompt_assets = None
        if isinstance(prompt, str):
            prompt_hash = f"sha256:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}"
            checks = self._prompt_checks(prompt)
            prompt_assets = self._prompt_assets_in_prompt(
                prompt_text=prompt,
                llm_meta=llm_meta,
            )
        output_artifact_path = self._relpath(output_path)
        fallback_applied = bool(llm_meta.get("fallback_applied", False))
        parse_error = bool(llm_meta.get("parse_error", False))
        provider_error = llm_meta.get("provider_error")
        if fallback_applied:
            self._game_runtime_stats["fallbacks"] = int(self._game_runtime_stats.get("fallbacks", 0)) + 1
        if parse_error:
            self._game_runtime_stats["parse_failures"] = int(self._game_runtime_stats.get("parse_failures", 0)) + 1
        if is_llm:
            by_method = self._game_runtime_stats.setdefault("by_method", {})
            if not isinstance(by_method, dict):
                by_method = {}
                self._game_runtime_stats["by_method"] = by_method
            method_stats = by_method.setdefault(
                method,
                {
                    "calls": 0,
                    "first_attempt_valid": 0,
                    "repair_success": 0,
                    "fallbacks": 0,
                    "parse_failures": 0,
                    "schema_failures": 0,
                    "truncated_outputs": 0,
                    "outputs_trimmed": 0,
                },
            )
            if isinstance(method_stats, dict):
                method_stats["calls"] = int(method_stats.get("calls", 0)) + 1
                retry_count = int(llm_meta.get("retry_count", 0)) if isinstance(llm_meta.get("retry_count"), int) else 0
                if not fallback_applied and retry_count == 0:
                    method_stats["first_attempt_valid"] = int(method_stats.get("first_attempt_valid", 0)) + 1
                elif not fallback_applied and retry_count > 0:
                    method_stats["repair_success"] = int(method_stats.get("repair_success", 0)) + 1
                if fallback_applied:
                    method_stats["fallbacks"] = int(method_stats.get("fallbacks", 0)) + 1
                if parse_error:
                    method_stats["parse_failures"] = int(method_stats.get("parse_failures", 0)) + 1
                if llm_meta.get("validation_error_category"):
                    method_stats["schema_failures"] = int(method_stats.get("schema_failures", 0)) + 1
                if bool(llm_meta.get("likely_truncated", False)):
                    method_stats["truncated_outputs"] = int(method_stats.get("truncated_outputs", 0)) + 1
                if bool(llm_meta.get("schema_limit_trimmed", False)):
                    method_stats["outputs_trimmed"] = int(method_stats.get("outputs_trimmed", 0)) + 1
        usage = result.usage if hasattr(result, "usage") and isinstance(result.usage, dict) else {}
        invocation_fields: dict[str, Any] = {
            "game_id": gm.context.game_id,
            "phase_id": phase_id,
            "agent_id": agent_id,
            "country": country,
            "runtime_kind": runtime_kind,
            "method": method,
            "tick": tick,
            "ok": bool(getattr(result, "ok", True)),
            "fallback": fallback_applied,
            "parse_error": parse_error,
            "repair_count": int(llm_meta.get("retry_count", 0))
            if isinstance(llm_meta.get("retry_count"), int)
            else 0,
            "provider_model": llm_meta.get("provider_model"),
            "provider_error": provider_error,
            "provider_error_details": llm_meta.get("provider_error_details"),
            "provider": llm_meta.get("provider_metadata", self._provider_metadata()),
            "usage": usage,
            "output_artifact_path": output_artifact_path,
            "log_lines": self._invocation_log_lines(
                method=method,
                prompt_chars=prompt_size_chars if isinstance(prompt_size_chars, int) else None,
                estimated_input_tokens=estimated_input_tokens if isinstance(estimated_input_tokens, int) else None,
                fallback=fallback_applied,
                parse_error=parse_error,
                output_artifact_path=output_artifact_path,
            ),
        }
        if is_llm:
            invocation_fields["prompt_chars"] = prompt_size_chars if isinstance(prompt_size_chars, int) else None
            invocation_fields["estimated_input_tokens"] = (
                estimated_input_tokens if isinstance(estimated_input_tokens, int) else None
            )
            invocation_fields["prompt_hash"] = prompt_hash
            invocation_fields["prompt_checks"] = checks if isinstance(checks, dict) else None
            invocation_fields["prompt_assets"] = prompt_assets if isinstance(prompt_assets, dict) else None
        if is_llm and self.provider_usage is not None:
            lifecycle_id = "/".join((gm.context.game_id, phase_id, str(tick if tick is not None else "once"), agent_id, method))
            self.provider_usage.append({"kind": "lifecycle", "lifecycle_id": lifecycle_id,
                                        "game_id": gm.context.game_id, "phase_id": phase_id,
                                        "agent_id": agent_id, "method": method,
                                        "fallback": fallback_applied or llm_meta.get("provider_model") == "budget_fallback"})
        self._emit_event("agent_invocation_completed", **invocation_fields)
        if is_llm and isinstance(prompt, str):
            self._emit_event(
                "prompt_check",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                runtime_kind=runtime_kind,
                method=method,
                tick=tick,
                prompt_chars=len(prompt),
                prompt_hash=prompt_hash,
                estimated_input_tokens=estimated_input_tokens if isinstance(estimated_input_tokens, int) else None,
                checks=checks if isinstance(checks, dict) else {},
                prompt_assets=prompt_assets if isinstance(prompt_assets, dict) else {},
                prompt_profile="default",
                output_artifact_path=output_artifact_path,
            )

        if method == "negotiate_tick":
            message_validation = llm_meta.get("message_validation", {})
            parsed_payload = llm_meta.get("parsed_payload", {})
            if not isinstance(parsed_payload, dict):
                parsed_payload = {}
            parsed_messages = parsed_payload.get("messages", [])
            message_rows = parsed_messages if isinstance(parsed_messages, list) else (
                result.messages if hasattr(result, "messages") and isinstance(result.messages, list) else []
            )
            message_summaries = self._summarize_messages(
                messages=message_rows,
                communication_plan=parsed_payload.get("communication_plan", {}),
            )
            if isinstance(message_validation, dict):
                dropped = message_validation.get("dropped_messages", [])
                if isinstance(dropped, list):
                    for row in dropped:
                        if not isinstance(row, dict):
                            continue
                        self._emit_event(
                            "message_dropped",
                            game_id=gm.context.game_id,
                            phase_id=phase_id,
                            tick=tick,
                            agent_id=agent_id,
                            country=country,
                            reason=str(row.get("reason", "other")),
                            to_agent_id=row.get("to_agent_id"),
                            output_artifact_path=output_artifact_path,
                        )
                self._emit_event(
                    "messages_generated",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    tick=tick,
                    agent_id=agent_id,
                    country=country,
                    generated_count=message_validation.get("generated_count", 0),
                    dropped_count=message_validation.get("dropped_count", 0),
                    message_summaries=message_summaries,
                    output_artifact_path=output_artifact_path,
                    log_lines=self._message_log_lines(message_summaries),
                )
            relationship_analysis = parsed_payload.get("relationship_analysis")
            if isinstance(relationship_analysis, list):
                relationship_snapshot = self._summarize_relationship_analysis(relationship_analysis)
                if relationship_snapshot:
                    self._emit_event(
                        "relationship_snapshot",
                        game_id=gm.context.game_id,
                        phase_id=phase_id,
                        tick=tick,
                        agent_id=agent_id,
                        country=country,
                        relationships=relationship_snapshot,
                        output_artifact_path=output_artifact_path,
                        log_lines=self._relationship_log_lines(relationship_snapshot),
                    )
        if method == "submit_orders":
            order_grounding = llm_meta.get("order_grounding", {})
            parsed_payload = llm_meta.get("parsed_payload", {})
            if not isinstance(parsed_payload, dict):
                parsed_payload = {}
            if isinstance(order_grounding, dict):
                submitted_orders = order_grounding.get("submitted_orders", [])
                effective_orders = order_grounding.get("effective_orders", [])
                invalid_reasons = order_grounding.get("invalid_reasons", [])
                order_summaries = self._summarize_orders(
                    effective_orders=effective_orders if isinstance(effective_orders, list) else [],
                    parsed_payload=parsed_payload,
                )
                self._emit_event(
                    "orders_generated",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    submitted_count=len(submitted_orders) if isinstance(submitted_orders, list) else 0,
                    effective_count=len(effective_orders) if isinstance(effective_orders, list) else 0,
                    order_summaries=order_summaries,
                    output_artifact_path=output_artifact_path,
                    log_lines=self._order_log_lines(order_summaries),
                )
                self._emit_event(
                    "orders_validated",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    invalid_reasons=invalid_reasons if isinstance(invalid_reasons, list) else [],
                    output_artifact_path=output_artifact_path,
                )
                retry_count = int(llm_meta.get("retry_count", 0)) if isinstance(llm_meta.get("retry_count"), int) else 0
                if retry_count > 0:
                    self._emit_event(
                        "orders_repaired",
                        game_id=gm.context.game_id,
                        phase_id=phase_id,
                        agent_id=agent_id,
                        country=country,
                        repair_attempts=retry_count,
                        repair_success=bool(llm_meta.get("retry_success", False)),
                        output_artifact_path=output_artifact_path,
                    )
                if bool(llm_meta.get("fallback_applied", False)):
                    self._emit_event(
                        "orders_fallback_applied",
                        game_id=gm.context.game_id,
                        phase_id=phase_id,
                        agent_id=agent_id,
                        country=country,
                        invalid_reasons=invalid_reasons if isinstance(invalid_reasons, list) else [],
                        output_artifact_path=output_artifact_path,
                    )
        if method == "reflect":
            reflection_summary = self._summarize_reflection(result)
            self._emit_event(
                "reflection_generated",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                country=country,
                reflection_summary=reflection_summary,
                output_artifact_path=output_artifact_path,
                log_lines=self._reflection_log_lines(reflection_summary),
            )
            reflection_failure = llm_meta.get("reflection_failure", {})
            if isinstance(reflection_failure, dict) and reflection_failure:
                self._emit_event(
                    "reflection_fallback_applied",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    parse_error_category=reflection_failure.get("parse_error_category"),
                    schema_validation_error_category=reflection_failure.get("schema_validation_error_category"),
                    likely_truncated=bool(reflection_failure.get("likely_truncated", False)),
                    repair_attempted=bool(reflection_failure.get("repair_attempted", False)),
                    repair_succeeded=bool(reflection_failure.get("repair_succeeded", False)),
                    output_artifact_path=output_artifact_path,
                )
            self._emit_event(
                "reflection_validated",
                game_id=gm.context.game_id,
                phase_id=phase_id,
                agent_id=agent_id,
                country=country,
                fallback=fallback_applied,
                parse_error=parse_error,
                output_artifact_path=output_artifact_path,
            )
            retry_count = int(llm_meta.get("retry_count", 0)) if isinstance(llm_meta.get("retry_count"), int) else 0
            if retry_count > 0:
                self._emit_event(
                    "reflection_repaired",
                    game_id=gm.context.game_id,
                    phase_id=phase_id,
                    agent_id=agent_id,
                    country=country,
                    repair_attempts=retry_count,
                    repair_success=bool(llm_meta.get("retry_success", False)),
                    output_artifact_path=output_artifact_path,
                )

    def _provider_metadata(self) -> dict[str, Any]:
        try:
            configs = self._provider_configs_by_agent()
        except Exception as exc:
            return {"configuration_error": f"{exc.__class__.__name__}: {exc}"}
        if not configs:
            return parse_llm_provider_config((self.config.raw or {}).get("llm_provider")).metadata()
        providers_by_agent = {agent_id: cfg.metadata() for agent_id, cfg in sorted(configs.items())}
        canonical = {json.dumps(canonical_provider_config({
            "kind": cfg.kind, "model": cfg.model, "base_url": cfg.base_url,
            "api_key_env_var": cfg.api_key_env_var, "api_style": cfg.api_style,
            "provider_name": cfg.provider_name, "model_profile": cfg.model_profile,
            "temperature": cfg.temperature, "max_output_tokens": cfg.max_output_tokens,
            "timeout_seconds": cfg.timeout_seconds, "reasoning_effort": cfg.reasoning_effort,
            "reasoning_effort_param_style": cfg.reasoning_effort_param_style,
            "supports_temperature": cfg.supports_temperature,
            "supports_reasoning_effort": cfg.supports_reasoning_effort,
            "supports_max_output_tokens": cfg.supports_max_output_tokens,
            "pricing": {"input_cost_per_1k_tokens": cfg.input_cost_per_1k_tokens, "output_cost_per_1k_tokens": cfg.output_cost_per_1k_tokens},
        }), sort_keys=True) for cfg in configs.values()}
        if len(canonical) == 1:
            metadata = next(iter(providers_by_agent.values()))
            return {**metadata, "provider_mode": "single", "providers_by_agent": providers_by_agent}
        return {
            "provider_mode": "mixed", "provider_name": "mixed", "api_style": "mixed",
            "requested_model_profile": None, "requested_model": None, "resolved_model": None,
            "providers_by_agent": providers_by_agent,
        }

    def _invocation_log_lines(
        self,
        *,
        method: str,
        prompt_chars: int | None,
        estimated_input_tokens: int | None,
        fallback: bool,
        parse_error: bool,
        output_artifact_path: str,
    ) -> list[str]:
        lines = [f"method={method} artifact={output_artifact_path}"]
        if isinstance(prompt_chars, int):
            lines.append(f"prompt_chars={prompt_chars}")
        if isinstance(estimated_input_tokens, int):
            lines.append(f"estimated_input_tokens={estimated_input_tokens}")
        if fallback:
            lines.append("fallback_applied=true")
        if parse_error:
            lines.append("parse_error=true")
        return lines

    def _summarize_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        communication_plan: Any,
    ) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        goal = ""
        if isinstance(communication_plan, dict):
            goal = str(communication_plan.get("goal", "")).strip()
        for row in messages:
            if not isinstance(row, dict):
                continue
            body = str(row.get("body", "")).strip()
            to_raw = row.get("to")
            target: dict[str, Any] = to_raw if isinstance(to_raw, dict) else {}
            if isinstance(to_raw, str) and to_raw.strip():
                target_type = "private"
                recipient = to_raw.strip()
            else:
                target_type = str(target.get("type", "global")).strip().lower()
                recipient = (
                    str(target.get("to_agent_id", "")).strip()
                    if target_type == "private"
                    else "global"
                )
            summary = body.splitlines()[0].strip() if body else ""
            if len(summary) > 120:
                summary = summary[:117].rstrip() + "..."
            purpose = self._infer_message_purpose(body=body, default_goal=goal)
            summaries.append(
                {
                    "recipient": recipient,
                    "message_type": target_type,
                    "purpose": purpose,
                    "summary": summary,
                }
            )
        return summaries

    def _infer_message_purpose(self, *, body: str, default_goal: str) -> str:
        lowered = body.lower()
        if "dmz" in lowered or "demilitar" in lowered:
            return "dmz_negotiation"
        if "support" in lowered or "hold" in lowered or "move" in lowered:
            return "tactical_coordination"
        if "?" in body or "what" in lowered or "where" in lowered:
            return "information_request"
        if "warning" in lowered or "rumor" in lowered or "heard" in lowered:
            return "information_share"
        if default_goal:
            brief = default_goal.strip()
            return brief[:64] + ("..." if len(brief) > 64 else "")
        return "relationship_maintenance"

    def _message_log_lines(self, message_summaries: list[dict[str, str]]) -> list[str]:
        if not message_summaries:
            return ["no messages generated"]
        lines: list[str] = []
        for row in message_summaries[:6]:
            recipient = row.get("recipient", "global")
            purpose = row.get("purpose", "")
            summary = row.get("summary", "")
            lines.append(f"to={recipient} purpose={purpose} summary={summary}")
        if len(message_summaries) > 6:
            lines.append(f"... +{len(message_summaries) - 6} more messages")
        return lines

    def _summarize_relationship_analysis(self, rows: list[Any]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            agent = str(row.get("agent_id", "")).strip()
            if not agent:
                continue
            out.append(
                {
                    "agent_id": agent,
                    "current_relationship": str(row.get("current_relationship", "")).strip(),
                    "what_they_may_want": str(row.get("what_they_may_want", "")).strip(),
                    "what_i_want_from_them": str(row.get("what_i_want_from_them", "")).strip(),
                    "next_conversation_move": str(row.get("next_conversation_move", "")).strip(),
                }
            )
        return out

    def _relationship_log_lines(self, rows: list[dict[str, str]]) -> list[str]:
        if not rows:
            return ["relationship analysis unavailable"]
        lines: list[str] = []
        for row in rows[:5]:
            lines.append(
                "agent="
                + row.get("agent_id", "")
                + " relationship="
                + row.get("current_relationship", "")
                + " next="
                + row.get("next_conversation_move", "")
            )
        if len(rows) > 5:
            lines.append(f"... +{len(rows) - 5} more relationships")
        return lines

    def _summarize_orders(
        self,
        *,
        effective_orders: list[dict[str, Any]],
        parsed_payload: dict[str, Any],
    ) -> list[dict[str, str]]:
        rationale = parsed_payload.get("order_rationale", {})
        reason = ""
        if isinstance(rationale, dict):
            reason = str(rationale.get("why_this_advances_strategy", "")).strip()
        summaries: list[dict[str, str]] = []
        for row in effective_orders:
            if not isinstance(row, dict):
                continue
            summaries.append(
                {
                    "order": self._render_order(row),
                    "reason": reason,
                }
            )
        return summaries

    def _render_order(self, row: dict[str, Any]) -> str:
        order_type = str(row.get("type", "")).upper()
        payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
        if order_type == "HOLD":
            at = str(payload.get("at", "")).strip()
            return f"{row.get('unit_ref', '')} HOLD {at}".strip()
        if order_type == "MOVE":
            to = str(payload.get("to", "")).strip()
            return f"{row.get('unit_ref', '')} -> {to}".strip()
        if order_type == "SUPPORT":
            return f"{row.get('unit_ref', '')} SUPPORT"
        if order_type == "CONVOY":
            return f"{row.get('unit_ref', '')} CONVOY"
        if order_type == "BUILD":
            province = str(payload.get("province", "")).strip()
            unit_type = str(payload.get("unit_type", "")).strip()
            return f"BUILD {unit_type} {province}".strip()
        if order_type == "DISBAND":
            return f"DISBAND {payload.get('unit_ref', '')}".strip()
        return f"{row.get('unit_ref', '')} {order_type}".strip()

    def _order_log_lines(self, order_summaries: list[dict[str, str]]) -> list[str]:
        if not order_summaries:
            return ["no effective orders"]
        lines: list[str] = []
        for row in order_summaries[:8]:
            lines.append(f"order={row.get('order', '')} reason={row.get('reason', '')}")
        if len(order_summaries) > 8:
            lines.append(f"... +{len(order_summaries) - 8} more orders")
        return lines

    def _summarize_reflection(self, result: Any) -> dict[str, Any]:
        reflection = result.reflection if hasattr(result, "reflection") and isinstance(result.reflection, dict) else {}
        objective_update = reflection.get("objective_update", {})
        if not isinstance(objective_update, dict):
            objective_update = {}
        relationship_updates = reflection.get("relationship_updates", [])
        if not isinstance(relationship_updates, list):
            relationship_updates = []
        action_lessons = reflection.get("action_lessons", [])
        if not isinstance(action_lessons, list):
            action_lessons = []
        memory_candidates = reflection.get("memory_candidates", [])
        if not isinstance(memory_candidates, list):
            memory_candidates = []
        belief_rows = reflection.get("belief_updates", []) if isinstance(reflection.get("belief_updates"), list) else []
        belief_summary = str(reflection.get("belief_summary", "")).strip()
        beliefs: list[str] = []
        for row in belief_rows[:6]:
            if not isinstance(row, dict):
                continue
            subject = str(row.get("subject", "")).strip()
            prior_belief = str(row.get("prior_belief", "")).strip()
            new_belief = str(row.get("new_belief", row.get("belief", ""))).strip()
            evidence = str(row.get("evidence", row.get("basis", ""))).strip()
            bits = [bit for bit in [subject, prior_belief, new_belief, evidence] if bit]
            if bits:
                beliefs.append(" | ".join(bits))
        strategic_intent = reflection.get("strategic_intent", {})
        short_term = ""
        medium_term = ""
        if isinstance(strategic_intent, dict):
            short_term = str(strategic_intent.get("short_term", "")).strip()
            medium_term = str(strategic_intent.get("medium_term", "")).strip()
        explanation = result.explanation if hasattr(result, "explanation") and isinstance(result.explanation, dict) else {}
        return {
            "reflection_text": str(reflection.get("reflection_text", "")),
            "phase_summary": str(reflection.get("phase_summary", "")).strip(),
            "current_objective": str(objective_update.get("current_objective", "")).strip(),
            "belief_changes": beliefs,
            "strategic_intent_short_term": short_term,
            "strategic_intent_medium_term": medium_term,
            "memory_update": str(reflection.get("memory_update", "")).strip(),
            "relationship_updates_count": len([row for row in relationship_updates if isinstance(row, dict)]),
            "action_lessons_count": len([row for row in action_lessons if isinstance(row, str)]),
            "memory_candidates_count": len([row for row in memory_candidates if isinstance(row, str)]),
            "belief_updates_count": len([row for row in belief_rows if isinstance(row, dict)]),
            "belief_summary": belief_summary,
            "belief_summary_word_count": len(belief_summary.split()),
            "belief_summary_status": str(reflection.get("belief_summary_status", "")).strip(),
            "has_objective_update": bool(str(objective_update.get("current_objective", "")).strip()),
            "explanation_summary": str(explanation.get("summary", "")).strip(),
        }

    def _reflection_log_lines(self, summary: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        reflection_text = str(summary.get("reflection_text", ""))
        if reflection_text:
            lines.append("reflection_text=" + reflection_text[:4000])
        beliefs = summary.get("belief_changes", [])
        if isinstance(beliefs, list) and beliefs:
            lines.append("belief_changes=" + "; ".join(str(row) for row in beliefs[:3]))
        belief_summary = str(summary.get("belief_summary", "")).strip()
        if belief_summary:
            lines.append("belief_summary=" + belief_summary)
        memory_update = str(summary.get("memory_update", "")).strip()
        if memory_update:
            lines.append("memory_update=" + memory_update)
        short_term = str(summary.get("strategic_intent_short_term", "")).strip()
        if short_term:
            lines.append("short_term=" + short_term)
        phase_summary = str(summary.get("phase_summary", "")).strip()
        if phase_summary:
            lines.append("phase_summary=" + phase_summary)
        if not lines:
            lines.append("reflection summary unavailable")
        return lines

    def _board_resolution_summary(
        self,
        *,
        phase_id: str,
        input_state: dict[str, Any],
        output_state: dict[str, Any],
        resolution: dict[str, Any],
        submitted_orders: list[dict[str, Any]],
        validated_orders: dict[str, Any],
    ) -> dict[str, Any]:
        phase = str(resolution.get("phase", ""))
        before_centers = input_state.get("supply_ownership", {}) if isinstance(input_state.get("supply_ownership"), dict) else {}
        after_centers = output_state.get("supply_ownership", {}) if isinstance(output_state.get("supply_ownership"), dict) else {}
        unit_owner: dict[str, str] = {}
        for row in input_state.get("units", []):
            if isinstance(row, dict):
                unit_owner[str(row.get("unit_id", ""))] = str(row.get("owner", ""))
        for row in output_state.get("units", []):
            if isinstance(row, dict):
                unit_owner[str(row.get("unit_id", ""))] = str(row.get("owner", ""))
        phase_origin_by_unit = {
            str(row.get("unit_id", "")): str(row.get("province", ""))
            for row in input_state.get("units", [])
            if isinstance(row, dict)
        }
        submitted_by_unit = {
            str(row.get("unit_ref", "")): row.get("payload", {})
            for row in submitted_orders
            if isinstance(row, dict) and isinstance(row.get("payload"), dict)
        }
        validated_by_unit = {
            str(row.get("unit_id", "")): row.get("effective_order", {})
            for row in validated_orders.get("validated_orders", [])
            if isinstance(row, dict) and isinstance(row.get("effective_order"), dict)
        }
        units_by_id = {
            str(row.get("unit_id", "")): row
            for row in input_state.get("units", [])
            if isinstance(row, dict)
        }

        def locations(row: dict[str, Any]) -> tuple[str, str]:
            unit_id = str(row.get("unit_id", ""))
            payload = submitted_by_unit.get(unit_id, {})
            effective = validated_by_unit.get(unit_id, {})
            effective_payload = effective.get("payload", {}) if isinstance(effective, dict) else {}
            if not isinstance(effective_payload, dict):
                effective_payload = {}
            start = str(row.get("from") or payload.get("from") or effective_payload.get("from") or payload.get("at") or phase_origin_by_unit.get(unit_id, ""))
            end = str(row.get("to") or payload.get("to") or effective_payload.get("to") or payload.get("at") or start)
            return start, end

        movement_outcomes = resolution.get("movement_outcomes", [])
        retreat_outcomes = resolution.get("retreat_outcomes", [])
        adjustment_outcomes = resolution.get("adjustment_outcomes", [])
        moves: list[dict[str, str]] = []
        if isinstance(movement_outcomes, list):
            for row in movement_outcomes:
                if not isinstance(row, dict):
                    continue
                origin, destination = locations(row)
                moves.append(
                    {
                        "power": unit_owner.get(str(row.get("unit_id", "")), ""),
                        "unit_id": str(row.get("unit_id", "")),
                        "from": origin,
                        "to": destination,
                        "result": str(row.get("result", "")),
                    }
                )
        retreats: list[dict[str, str]] = []
        if isinstance(retreat_outcomes, list):
            for row in retreat_outcomes:
                if not isinstance(row, dict):
                    continue
                origin, destination = locations(row)
                retreats.append(
                    {
                        "power": unit_owner.get(str(row.get("unit_id", "")), ""),
                        "unit_id": str(row.get("unit_id", "")),
                        "from": origin,
                        "to": destination,
                        "result": str(row.get("result", "")),
                    }
                )
        adjustments: list[dict[str, str]] = []
        if isinstance(adjustment_outcomes, list):
            for row in adjustment_outcomes:
                if not isinstance(row, dict):
                    continue
                adjustments.append(
                    {
                        "power": str(row.get("power", "")),
                        "unit_id": str(row.get("unit_id", "")),
                        "result": str(row.get("result", "")),
                    }
                )
        center_changes: list[dict[str, Any]] = []
        all_centers = sorted(set(before_centers.keys()) | set(after_centers.keys()))
        for province in all_centers:
            from_owner = before_centers.get(province)
            to_owner = after_centers.get(province)
            if from_owner != to_owner:
                center_changes.append(
                    {
                        "province": province,
                        "from_owner": from_owner,
                        "to_owner": to_owner,
                    }
                )
        public_final_orders = self._public_final_order_record(
            validated_orders=validated_orders.get("validated_orders", []),
            units_by_id=units_by_id,
        )
        return {
            "phase_id": phase_id,
            "phase": phase,
            "moves": moves,
            "retreats": retreats,
            "adjustments": adjustments,
            "center_changes": center_changes,
            "standoff_provinces": output_state.get("standoff_provinces", []),
            "public_final_orders": public_final_orders,
        }

    @staticmethod
    def _public_resolution_phase_id(state: dict[str, Any]) -> str | None:
        resolution = state.get("latest_public_resolution", {})
        if not isinstance(resolution, dict):
            return None
        phase_id = resolution.get("phase_id")
        return str(phase_id) if isinstance(phase_id, str) and phase_id.strip() else None

    def _public_final_order_record(
        self,
        *,
        validated_orders: Any,
        units_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Render only final effective orders in public board terms after adjudication."""
        if not isinstance(validated_orders, list):
            return []
        record: list[dict[str, Any]] = []
        for row in sorted((item for item in validated_orders if isinstance(item, dict)), key=lambda item: str(item.get("unit_id", ""))):
            unit_id = str(row.get("unit_id", ""))
            effective = row.get("effective_order")
            unit = units_by_id.get(unit_id, {})
            if not isinstance(effective, dict):
                if unit:
                    unit_label = self._public_unit_label(unit)
                    record.append(
                        {
                            "power": str(unit.get("owner", "")),
                            "unit": unit_label,
                            "unit_id": unit_id,
                            "order_type": "NO_EFFECTIVE_ORDER",
                            "payload": {},
                            "notation": f"{unit_label} NO EFFECTIVE ORDER",
                            "effective_status": "no_effective_order",
                        }
                    )
                continue
            payload = effective.get("payload", {})
            payload = payload if isinstance(payload, dict) else {}
            power = str(effective.get("power", unit.get("owner", "")))
            order_type = str(effective.get("type", "")).upper()
            unit_label = self._public_unit_label(unit) if unit else str(effective.get("unit_ref", unit_id) or "ADJUSTMENT")
            record.append(
                {
                    "power": power,
                    "unit": unit_label,
                    "unit_id": unit_id,
                    "order_type": order_type,
                    "payload": payload,
                    "notation": self._public_order_notation(
                        unit_label=unit_label,
                        order_type=order_type,
                        payload=payload,
                        units_by_id=units_by_id,
                    ),
                    "effective_status": "defaulted" if str(row.get("validation", "")) == "substituted" else "submitted",
                }
            )
        return record

    @staticmethod
    def _public_unit_label(unit: dict[str, Any]) -> str:
        unit_type = str(unit.get("type", ""))
        province = str(unit.get("province", ""))
        coast_value = unit.get("coast")
        coast = coast_value.strip() if isinstance(coast_value, str) else ""
        return f"{unit_type} {province}{f'/{coast}' if coast else ''}".strip()

    def _public_order_notation(
        self,
        *,
        unit_label: str,
        order_type: str,
        payload: dict[str, Any],
        units_by_id: dict[str, dict[str, Any]],
    ) -> str:
        if order_type == "HOLD":
            return f"{unit_label} H"
        if order_type in {"MOVE", "RETREAT"}:
            destination = str(payload.get("to", ""))
            suffix = " VIA CONVOY" if order_type == "MOVE" and payload.get("via_convoy") else ""
            return f"{unit_label}â€“{destination}{suffix}"
        if order_type == "SUPPORT_HOLD":
            target = str(payload.get("target", ""))
            target_unit = next((unit for unit in units_by_id.values() if str(unit.get("province", "")) == target), {})
            return f"{unit_label} S {self._public_unit_label(target_unit) if target_unit else target}"
        if order_type == "SUPPORT_MOVE":
            origin = str(payload.get("supported_from", ""))
            destination = str(payload.get("supported_to", ""))
            target_unit = next((unit for unit in units_by_id.values() if str(unit.get("province", "")) == origin), {})
            target_label = self._public_unit_label(target_unit) if target_unit else origin
            return f"{unit_label} S {target_label}â€“{destination}"
        if order_type == "CONVOY":
            return f"{unit_label} C A {payload.get('army_from', '')}â€“{payload.get('army_to', '')}"
        if order_type == "BUILD":
            return f"BUILD {payload.get('unit_type', '')} {payload.get('province', '')}".strip()
        if order_type == "DISBAND":
            return f"DISBAND {payload.get('unit_ref', '')}".strip()
        return f"{unit_label} {order_type}".strip()

    def _board_summary_lines(self, summary: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        moves = summary.get("moves", [])
        if isinstance(moves, list):
            moved_rows = [row for row in moves if isinstance(row, dict) and row.get("result") == "moved"]
            if moved_rows:
                for row in moved_rows[:6]:
                    lines.append(
                        f"{row.get('power', '')}: {row.get('unit_id', '')} {row.get('from', '')}->{row.get('to', '')}"
                    )
        center_changes = summary.get("center_changes", [])
        if isinstance(center_changes, list) and center_changes:
            for row in center_changes[:6]:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    f"center {row.get('province', '')}: {row.get('from_owner', None)} -> {row.get('to_owner', None)}"
                )
        adjustments = summary.get("adjustments", [])
        if isinstance(adjustments, list) and adjustments:
            lines.append(f"adjustments={len(adjustments)}")
        if not lines:
            lines.append("no major board changes")
        return lines

    def _reset_game_stats(self) -> None:
        self._game_message_stats = {
            "by_agent": {},
            "private_pairs": {},
            "global_messages": 0,
            "private_messages": 0,
        }
        self._game_runtime_stats = {"fallbacks": 0, "parse_failures": 0, "by_method": {}}
        self._eliminated_by_country = {}

    def _record_message_stat(self, *, from_agent_id: str, target_type: str, to_agent_id: str | None) -> None:
        if not isinstance(self._game_message_stats, dict):
            self._reset_game_stats()
        by_agent = self._game_message_stats.setdefault("by_agent", {})
        if not isinstance(by_agent, dict):
            by_agent = {}
            self._game_message_stats["by_agent"] = by_agent
        row = by_agent.setdefault(from_agent_id, {"global": 0, "private": 0, "total": 0})
        if not isinstance(row, dict):
            row = {"global": 0, "private": 0, "total": 0}
            by_agent[from_agent_id] = row
        if target_type == "private":
            row["private"] = int(row.get("private", 0)) + 1
            self._game_message_stats["private_messages"] = int(self._game_message_stats.get("private_messages", 0)) + 1
            if to_agent_id:
                a = from_agent_id.strip()
                b = to_agent_id.strip()
                pair_key = "__".join(sorted([a, b]))
                private_pairs = self._game_message_stats.setdefault("private_pairs", {})
                if isinstance(private_pairs, dict) and a and b and a != b:
                    private_pairs[pair_key] = int(private_pairs.get(pair_key, 0)) + 1
        else:
            row["global"] = int(row.get("global", 0)) + 1
            self._game_message_stats["global_messages"] = int(self._game_message_stats.get("global_messages", 0)) + 1
        row["total"] = int(row.get("total", 0)) + 1

    def _build_game_summary(self, *, result_payload: dict[str, Any]) -> dict[str, Any]:
        country_results = result_payload.get("country_results", {})
        leader_country = None
        leader_agent_id = None
        leader_centers = -1
        if isinstance(country_results, dict):
            for country, row in sorted(country_results.items()):
                if not isinstance(row, dict):
                    continue
                centers = int(row.get("owned_centers", 0))
                if centers > leader_centers:
                    leader_centers = centers
                    leader_country = str(country)
                    leader_agent_id = str(row.get("agent_id", ""))
        by_agent = self._game_message_stats.get("by_agent", {}) if isinstance(self._game_message_stats, dict) else {}
        most_active = None
        most_active_count = -1
        if isinstance(by_agent, dict):
            for agent_id, row in sorted(by_agent.items()):
                if not isinstance(row, dict):
                    continue
                total = int(row.get("total", 0))
                if total > most_active_count:
                    most_active = str(agent_id)
                    most_active_count = total
        private_pairs = self._game_message_stats.get("private_pairs", {}) if isinstance(self._game_message_stats, dict) else {}
        top_pair = None
        top_pair_count = 0
        if isinstance(private_pairs, dict):
            for pair, count in sorted(private_pairs.items()):
                pair_count = int(count)
                if pair_count > top_pair_count:
                    top_pair = str(pair)
                    top_pair_count = pair_count
        runtime_by_method = self._game_runtime_stats.get("by_method", {}) if isinstance(self._game_runtime_stats, dict) else {}
        negotiation = runtime_by_method.get("negotiate_tick", {}) if isinstance(runtime_by_method, dict) else {}
        negotiation = negotiation if isinstance(negotiation, dict) else {}
        reflection_runtime = runtime_by_method.get("reflect", {}) if isinstance(runtime_by_method, dict) else {}
        reflection_runtime = reflection_runtime if isinstance(reflection_runtime, dict) else {}
        negotiation_calls = int(negotiation.get("calls", 0))
        negotiation_fallbacks = int(negotiation.get("fallbacks", 0))
        return {
            "leader_country": leader_country,
            "leader_agent_id": leader_agent_id,
            "leader_centers": leader_centers if leader_centers >= 0 else None,
            "most_active_diplomat": most_active,
            "private_messages": int(self._game_message_stats.get("private_messages", 0))
            if isinstance(self._game_message_stats, dict)
            else 0,
            "global_messages": int(self._game_message_stats.get("global_messages", 0))
            if isinstance(self._game_message_stats, dict)
            else 0,
            "largest_private_pair": top_pair,
            "largest_private_pair_count": top_pair_count,
            "fallbacks": int(self._game_runtime_stats.get("fallbacks", 0)),
            "parse_failures": int(self._game_runtime_stats.get("parse_failures", 0)),
            "negotiation_calls": negotiation_calls,
            "negotiation_parse_failures": int(negotiation.get("parse_failures", 0)),
            "negotiation_schema_failures": int(negotiation.get("schema_failures", 0)),
            "negotiation_truncated_outputs": int(negotiation.get("truncated_outputs", 0)),
            "negotiation_fallbacks": negotiation_fallbacks,
            "negotiation_fallback_rate": round(negotiation_fallbacks / negotiation_calls, 4)
            if negotiation_calls
            else 0.0,
            "negotiation_outputs_trimmed": int(negotiation.get("outputs_trimmed", 0)),
            "reflection_first_attempt_valid": int(reflection_runtime.get("first_attempt_valid", 0)),
            "reflection_repair_success": int(reflection_runtime.get("repair_success", 0)),
            "reflection_fallbacks": int(reflection_runtime.get("fallbacks", 0)),
            "result": result_payload.get("result", "completed"),
            "termination_reason": result_payload.get("termination_reason"),
            "solo_victor": result_payload.get("solo_victor"),
            "cutoff_leaders": result_payload.get("cutoff_leaders", []),
        }

    def _game_summary_lines(self, summary: dict[str, Any]) -> list[str]:
        return [
            f"result={summary.get('result')} termination_reason={summary.get('termination_reason')} solo_victor={summary.get('solo_victor')}",
            f"leader={summary.get('leader_country')} ({summary.get('leader_centers')}) agent={summary.get('leader_agent_id')}",
            f"cutoff_leaders={summary.get('cutoff_leaders')}",
            f"cutoff_leaders={summary.get('cutoff_leaders')}",
            f"most_active_diplomat={summary.get('most_active_diplomat')}",
            f"private_messages={summary.get('private_messages')} global_messages={summary.get('global_messages')}",
            f"largest_private_pair={summary.get('largest_private_pair')} count={summary.get('largest_private_pair_count')}",
            f"fallbacks={summary.get('fallbacks')} parse_failures={summary.get('parse_failures')}",
            "negotiation_calls={calls} parse_failures={parse_failures} schema_failures={schema_failures} "
            "truncated={truncated} fallbacks={fallbacks} fallback_rate={fallback_rate} trimmed={trimmed}".format(
                calls=summary.get("negotiation_calls"),
                parse_failures=summary.get("negotiation_parse_failures"),
                schema_failures=summary.get("negotiation_schema_failures"),
                truncated=summary.get("negotiation_truncated_outputs"),
                fallbacks=summary.get("negotiation_fallbacks"),
                fallback_rate=summary.get("negotiation_fallback_rate"),
                trimmed=summary.get("negotiation_outputs_trimmed"),
            ),
        ]

    def _prompt_checks(self, prompt: str) -> dict[str, bool]:
        lowered = prompt.lower()
        return {
            "contains_strategic_imperatives": "# strategic imperatives" in lowered,
            "contains_victory_campaign_doctrine": "# victory and campaign doctrine" in lowered,
            "contains_diplomacy_primer": "# diplomacy primer" in lowered,
            "contains_rules_reference": "diplomacy rules reference" in lowered,
            "contains_rules_reference_version": "diplomacy_rules_reference_v5_convoyed_army_support_public_orders" in lowered,
            "contains_outcome_review_instruction": OUTCOME_REVIEW_INSTRUCTION.lower() in lowered,
            "contains_engine_authority": "engine-generated legal-order context" in lowered,
            "contains_support_mechanics": "## support orders" in lowered and "support-cut rules" in lowered,
            "contains_convoy_mechanics": "## convoys" in lowered and "via_convoy" in lowered,
        }

    def _prompt_asset_file_metadata(self, *, path: Path) -> dict[str, Any]:
        exists = path.exists()
        text = ""
        if exists:
            text = path.read_text(encoding="utf-8").strip()
        digest = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}" if text else None
        metadata = {
            "path": self._relpath(path),
            "exists": exists,
            "char_length": len(text) if text else 0,
            "sha256": digest,
        }
        for line in text.splitlines():
            match = re.fullmatch(r"`?([a-z_]+_version):\s*([^`]+?)`?", line.strip())
            if match:
                metadata[match.group(1)] = match.group(2).strip()
        return metadata

    def _prompt_assets_in_prompt(
        self,
        *,
        prompt_text: str,
        llm_meta: dict[str, Any],
    ) -> dict[str, Any]:
        asset_paths = llm_meta.get("prompt_assets", {}) if isinstance(llm_meta, dict) else {}
        strategic_path = Path(
            str(asset_paths.get("strategic_imperatives_path", ""))
        ) if isinstance(asset_paths, dict) and asset_paths.get("strategic_imperatives_path") else canonical_strategic_imperatives_path()
        doctrine_path = Path(
            str(asset_paths.get("victory_campaign_doctrine_path", ""))
        ) if isinstance(asset_paths, dict) and asset_paths.get("victory_campaign_doctrine_path") else canonical_victory_campaign_doctrine_path()
        primer_path = Path(
            str(asset_paths.get("diplomacy_primer_path", ""))
        ) if isinstance(asset_paths, dict) and asset_paths.get("diplomacy_primer_path") else canonical_diplomacy_primer_path()
        strategic_meta = self._prompt_asset_file_metadata(path=strategic_path)
        doctrine_meta = self._prompt_asset_file_metadata(path=doctrine_path)
        primer_meta = self._prompt_asset_file_metadata(path=primer_path)
        strategic_text = strategic_path.read_text(encoding="utf-8").strip() if strategic_path.exists() else ""
        doctrine_text = doctrine_path.read_text(encoding="utf-8").strip() if doctrine_path.exists() else ""
        primer_text = primer_path.read_text(encoding="utf-8").strip() if primer_path.exists() else ""
        strategic_meta["included_in_prompt"] = bool(strategic_text and strategic_text in prompt_text)
        doctrine_meta["included_in_prompt"] = bool(doctrine_text and doctrine_text in prompt_text)
        outcome_review_meta = {
            "outcome_review_version": OUTCOME_REVIEW_VERSION,
            "sha256": OUTCOME_REVIEW_SHA256,
            "included_in_prompt": OUTCOME_REVIEW_INSTRUCTION in prompt_text,
        }
        primer_meta["included_in_prompt"] = bool(primer_text and primer_text in prompt_text)
        return {
            "prompt_profile": "default",
            "strategic_imperatives": strategic_meta,
            "victory_campaign_doctrine": doctrine_meta,
            "outcome_review": outcome_review_meta,
            "diplomacy_primer": primer_meta,
        }

    def _emit_budget_events(self, *, game_id: str | None = None, phase_id: str | None = None) -> None:
        if self.budget is None:
            return
        status = self.budget.status()
        self._emit_event(
            "budget_state",
            game_id=game_id,
            phase_id=phase_id,
            provider_calls=status.get("total_provider_calls"),
            input_tokens=status.get("total_input_tokens"),
            output_tokens=status.get("total_output_tokens"),
            estimated_cost_usd=status.get("total_estimated_cost_usd"),
            stop_policy=status.get("stop_policy"),
            stop_reason=status.get("stop_reason"),
        )
        self._emit_budget_milestones(status=status, game_id=game_id, phase_id=phase_id)

        warning_threshold = 0.8
        checks = [
            ("provider_calls", status.get("max_provider_calls"), status.get("total_provider_calls")),
            ("input_tokens", status.get("max_input_tokens"), status.get("total_input_tokens")),
            ("output_tokens", status.get("max_output_tokens"), status.get("total_output_tokens")),
            ("estimated_cost_usd", status.get("max_estimated_cost_usd"), status.get("total_estimated_cost_usd")),
        ]
        for metric, max_value, total_value in checks:
            if metric in self._budget_warned:
                continue
            if not isinstance(max_value, (int, float)) or not isinstance(total_value, (int, float)):
                continue
            if max_value <= 0:
                continue
            ratio = float(total_value) / float(max_value)
            if warning_threshold <= ratio < 1.0:
                self._budget_warned.add(metric)
                self._emit_event(
                    "budget_warning",
                    game_id=game_id,
                    phase_id=phase_id,
                    metric=metric,
                    usage=float(total_value),
                    limit=float(max_value),
                    ratio=round(ratio, 4),
                )
        if status.get("stop_reason") and not self._budget_stop_logged:
            self._budget_stop_logged = True
            self._emit_event(
                "budget_stop_triggered",
                game_id=game_id,
                phase_id=phase_id,
                stop_reason=status.get("stop_reason"),
                stop_policy=status.get("stop_policy"),
                provider_calls=status.get("total_provider_calls"),
                input_tokens=status.get("total_input_tokens"),
                output_tokens=status.get("total_output_tokens"),
                estimated_cost_usd=status.get("total_estimated_cost_usd"),
            )

    def _emit_budget_milestones(
        self,
        *,
        status: dict[str, Any],
        game_id: str | None,
        phase_id: str | None,
    ) -> None:
        thresholds: list[tuple[float, str]] = [
            (0.10, "10%"),
            (0.25, "25%"),
            (0.50, "50%"),
            (0.75, "75%"),
            (0.90, "90%"),
            (0.95, "95%"),
            (1.00, "100%"),
        ]
        checks = [
            ("provider_calls", status.get("max_provider_calls"), status.get("total_provider_calls")),
            ("input_tokens", status.get("max_input_tokens"), status.get("total_input_tokens")),
            ("output_tokens", status.get("max_output_tokens"), status.get("total_output_tokens")),
            ("estimated_cost_usd", status.get("max_estimated_cost_usd"), status.get("total_estimated_cost_usd")),
        ]
        for metric, max_value, total_value in checks:
            if not isinstance(max_value, (int, float)) or not isinstance(total_value, (int, float)):
                continue
            if max_value <= 0:
                continue
            ratio = float(total_value) / float(max_value)
            for threshold, label in thresholds:
                key = f"{metric}:{label}"
                if key in self._budget_milestones_emitted:
                    continue
                if ratio >= threshold:
                    self._budget_milestones_emitted.add(key)
                    self._emit_event(
                        "budget_milestone",
                        game_id=game_id,
                        phase_id=phase_id,
                        metric=metric,
                        milestone=label,
                        usage=float(total_value),
                        limit=float(max_value),
                        ratio=round(ratio, 4),
                    )
