from __future__ import annotations

import hashlib
import json
import random
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llm_diplomacy.agents.llm_provider import (
    build_provider_chain,
    build_provider_from_config,
    canonical_provider_config,
    parse_llm_provider_config,
)
from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.factory import AgentFactory, load_blueprint, validate_agent_folio
from llm_diplomacy.agents.baseline_llm_runtime import PERSISTENCE_HANDOFF_VERSION
from llm_diplomacy.observatory import (
    strategic_consistency_experiment_markdown,
    summarize_experiment_strategic_consistency,
)
from llm_diplomacy.runner import ScriptedExperimentRunner
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, canonical_hash

from .config import PopulationExperimentConfig, PopulationPlayer

POWERS = ["austria", "england", "france", "germany", "italy", "russia", "turkey"]


@dataclass(frozen=True)
class PopulationRunResult:
    experiment_root: Path
    manifest_path: Path
    summary_json_path: Path
    summary_md_path: Path
    dry_run_report_path: Path | None = None
    pilot_report_json_path: Path | None = None
    pilot_report_md_path: Path | None = None


class PopulationExperimentRunner:
    def __init__(
        self,
        *,
        config: PopulationExperimentConfig,
        base_dir: str | Path = ".",
        agents_root: str | Path = "agents",
    ) -> None:
        self.config = config
        self.base_dir = Path(base_dir)
        self.agents_root = Path(agents_root)
        self._configured_agents_root = self.agents_root
        self._active_recovery: ExperimentRecovery | None = None
        self.factory = AgentFactory(schema_version=config.schema_version)

    def run(self, *, dry_run_override: bool | None = None, resume: bool = False,
            decision_action: str | None = None, decision_id: str | None = None,
            attempt_id: str | None = None, prompt_hash: str | None = None) -> PopulationRunResult:
        dry_run = self.config.dry_run if dry_run_override is None else bool(dry_run_override)
        self.agents_root = self._configured_agents_root
        self._active_recovery = None
        strict_recovery = bool(((self.config.raw.get("provider_failure_policy") or {})
                                if isinstance(self.config.raw.get("provider_failure_policy"), dict) else {})
                               .get("strict_recovery", False))
        if dry_run:
            if resume:
                raise ValueError("dry-run cannot resume an experiment")
            experiment_root = self.base_dir / "experiments" / self.config.experiment_id
            if strict_recovery:
                try:
                    self.agents_root.resolve().relative_to(experiment_root.resolve())
                except ValueError as exc:
                    raise ValueError("strict recovery dry-run requires --agents-root inside the experiment directory") from exc
            lock = RecoveryLock(experiment_root / "recovery" / "writer.lock")
            with lock:
                existing = [name for name in ("population_manifest.json", "run_summary.json", "games",
                                                "population_summary.json", "pilot_report.json")
                            if (experiment_root / name).exists()]
                recovery_evidence = any((experiment_root / "recovery" / name).exists()
                                        for name in ("checkpoint.json", "journal.jsonl", "commit_manifest.json"))
                if existing or recovery_evidence:
                    raise FileExistsError("dry-run refuses to mutate an experiment with existing run or recovery evidence")
                if strict_recovery and self.config.agent_state_mode == "fresh":
                    ids = {player.agent_id or load_blueprint(player.blueprint).agent_id for player in self.config.players}
                    if any((self.agents_root / agent_id).exists() for agent_id in ids):
                        raise FileExistsError("strict dry-run refuses to replace existing agent folios")
                return self._run_impl(dry_run_override=dry_run_override, resume=False, recovery=None)
        if resume and not strict_recovery:
            raise ValueError("resume requires provider_failure_policy.strict_recovery: true")
        if strict_recovery and self.config.game_count != 1:
            raise ValueError("strict recovery currently supports exactly one game per experiment")
        experiment_root = self.base_dir / "experiments" / self.config.experiment_id
        if not resume:
            existing = [name for name in ("population_manifest.json", "run_summary.json", "games",
                                            "population_summary.json", "pilot_report.json")
                        if (experiment_root / name).exists()]
            dry_run_only = False
            manifest_path = experiment_root / "population_manifest.json"
            dry_report_path = experiment_root / "dry_run_report.json"
            if manifest_path.is_file() and dry_report_path.is_file():
                try:
                    dry_run_only = bool(json.loads(manifest_path.read_text(encoding="utf-8")).get("dry_run"))
                except (OSError, json.JSONDecodeError, AttributeError):
                    dry_run_only = False
            if existing and not (dry_run_only and set(existing) <= {"population_manifest.json"}):
                raise FileExistsError(f"experiment evidence exists; use explicit --resume: {existing}")
        if not strict_recovery:
            with RecoveryLock(experiment_root / "recovery" / "writer.lock"):
                return self._run_impl(dry_run_override=dry_run_override, resume=False, recovery=None)
        if strict_recovery:
            try:
                self.agents_root.resolve().relative_to(experiment_root.resolve())
            except ValueError as exc:
                raise ValueError("strict recovery requires --agents-root inside the experiment directory") from exc
        recovery = self._recovery_store(resume=resume)
        lock = RecoveryLock(recovery.path / "writer.lock")
        with lock:
            checkpoint = recovery.prepare()
            if decision_action:
                if not resume or not decision_id or not attempt_id:
                    raise ValueError("a recovery decision requires --resume, --decision-id and --attempt-id")
                if decision_action in {"retry_unknown", "abandon_unknown"}:
                    if not prompt_hash:
                        raise ValueError("unknown recovery requires the original prompt hash")
                    recovery.resolve_unknown(
                        decision_id=decision_id, attempt_id=attempt_id, prompt_hash=prompt_hash,
                        choice="retry_after_review" if decision_action == "retry_unknown" else "abandon_decision",
                    )
                elif decision_action == "retry_invalid":
                    if not prompt_hash:
                        raise ValueError("retry_invalid requires the response prompt hash")
                    recovery.retry_cached_response(decision_id=decision_id, prompt_hash=prompt_hash,
                                                   attempt_id=attempt_id, choice="retry_after_review")
                else:
                    raise ValueError(f"unknown recovery decision action: {decision_action}")
            if resume:
                # Resume is the explicit operator action that clears a prior
                # cooperative stop before finishing any pending transaction.
                if (recovery.path / "commit_manifest.json").exists():
                    recovery.validate_pending_commit(canonical_root=experiment_root)
                recovery.clear_safe_halt()
                finished_manifest = recovery.finish_pending_commit(canonical_root=experiment_root)
                if finished_manifest is not None:
                    checkpoint = recovery._read_checkpoint()
            cursor_unit = checkpoint.get("cursor", {}).get("unit") if checkpoint else None
            completed = cursor_unit in {"game_completed", "population_reports_completed"}
            reports_completed = cursor_unit == "population_reports_completed"
            if not completed:
                recovery.prepare_working_tree()
            elif recovery.working_root.is_dir():
                # A report commit may have been interrupted after the game commit.
                # Its manifest is finished above; a completed tree remains intact.
                pass
            self._active_recovery = recovery
            staged_experiment_root = recovery.working_root / "experiments" / self.config.experiment_id
            effective_resume = resume and not recovery.needs_bootstrap
            try:
                agents_relative = self.agents_root.resolve().relative_to(experiment_root.resolve())
            except ValueError as exc:
                raise ValueError("strict recovery requires --agents-root inside the experiment directory") from exc
            self.agents_root = staged_experiment_root / agents_relative
            if not resume and (staged_experiment_root / "dry_run_report.json").exists():
                (staged_experiment_root / "dry_run_report.json").unlink()
            return self._run_impl(dry_run_override=dry_run_override, resume=effective_resume, recovery=recovery,
                                  recovery_completed=completed, reports_completed=reports_completed)

    def _run_impl(self, *, dry_run_override: bool | None, resume: bool,
                  recovery: ExperimentRecovery | None, recovery_completed: bool = False,
                  reports_completed: bool = False) -> PopulationRunResult:
        started_at = _utc_now_iso()
        started_perf = time.perf_counter()
        dry_run = self.config.dry_run if dry_run_override is None else bool(dry_run_override)
        prepared_agents = self._prepare_agents(dry_run=dry_run, resume=resume)
        canonical_experiment_root = self.base_dir / "experiments" / self.config.experiment_id
        experiment_root = (recovery.working_root / "experiments" / self.config.experiment_id
                           if recovery is not None else canonical_experiment_root)
        experiment_root.mkdir(parents=True, exist_ok=True)
        manifest_path = experiment_root / "population_manifest.json"
        summary_json_path = experiment_root / "population_summary.json"
        summary_md_path = experiment_root / "population_summary.md"

        manifest = self._build_manifest(prepared_agents, dry_run=dry_run)
        if resume:
            if not manifest_path.exists():
                raise ValueError("resume requires the original population manifest")
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for row in manifest.get("agents", []):
                if isinstance(row, dict):
                    old = next((item for item in existing_manifest.get("agents", [])
                                if isinstance(item, dict) and item.get("agent_id") == row.get("agent_id")), None)
                    if old is not None:
                        row["generated"] = old.get("generated", row.get("generated"))
            original_manifest = existing_manifest
            if recovery is not None:
                existing_manifest = recovery.migrate_input_budget_manifest(existing_manifest)
            if existing_manifest != manifest:
                raise ValueError("resume population manifest does not match the current plan")
            if existing_manifest != original_manifest:
                manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        else:
            manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")

        if dry_run:
            dry_run_report = self._build_dry_run_report(prepared_agents)
            dry_run_report["started_at"] = started_at
            dry_run_report["completed_at"] = _utc_now_iso()
            dry_run_report["elapsed_seconds"] = round(time.perf_counter() - started_perf, 3)
            dry_run_report_path = experiment_root / "dry_run_report.json"
            dry_run_report_path.write_text(json.dumps(dry_run_report, sort_keys=True, indent=2), encoding="utf-8")
            return PopulationRunResult(
                experiment_root=experiment_root,
                manifest_path=manifest_path,
                summary_json_path=summary_json_path,
                summary_md_path=summary_md_path,
                dry_run_report_path=dry_run_report_path,
            )

        if recovery is not None and reports_completed:
            return PopulationRunResult(
                experiment_root=canonical_experiment_root,
                manifest_path=canonical_experiment_root / "population_manifest.json",
                summary_json_path=canonical_experiment_root / "population_summary.json",
                summary_md_path=canonical_experiment_root / "population_summary.md",
                pilot_report_json_path=canonical_experiment_root / "pilot_report.json",
                pilot_report_md_path=canonical_experiment_root / "pilot_report.md",
            )

        exp_cfg = self._build_experiment_config(prepared_agents)
        max_years = max(1, self.config.max_year - 1900)
        if not recovery_completed:
            ScriptedExperimentRunner(
                config=exp_cfg,
                base_dir=self.base_dir,
                max_years=max_years,
                negotiation_ticks=int(exp_cfg.negotiation.get("max_ticks", 1)),
                recovery=recovery,
                resume=resume,
            ).run()

        summary: dict[str, Any] = {}
        if recovery is not None:
            recovery.check_safe_halt()
        metrics_root = canonical_experiment_root if recovery is not None else experiment_root
        completed_at = _utc_now_iso()
        elapsed_seconds = round(time.perf_counter() - started_perf, 3)
        if self.config.summary:
            summary = self._build_summary(
                experiment_root,
                started_at=started_at,
                completed_at=completed_at,
                elapsed_seconds=elapsed_seconds,
                metrics_root=metrics_root,
            )
            summary = _rewrite_stage_paths(summary, experiment_root, canonical_experiment_root)
            summary_json_path.write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
            summary_md_path.write_text(self._summary_markdown(summary), encoding="utf-8")
        belief_evidence_summary = summarize_experiment_strategic_consistency(experiment_root)
        belief_evidence_summary_json_path = experiment_root / "belief_evidence_observability_summary.json"
        belief_evidence_summary_md_path = experiment_root / "belief_evidence_observability_summary.md"
        belief_evidence_summary_json_path.write_text(
            json.dumps(belief_evidence_summary, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        belief_evidence_summary_md_path.write_text(
            strategic_consistency_experiment_markdown(belief_evidence_summary),
            encoding="utf-8",
        )

        pilot_report = self._build_pilot_report(
            experiment_root,
            summary,
            started_at=started_at,
            completed_at=completed_at,
            elapsed_seconds=elapsed_seconds,
            metrics_root=metrics_root,
        )
        pilot_report = _rewrite_stage_paths(pilot_report, experiment_root, canonical_experiment_root)
        pilot_report_json_path = experiment_root / "pilot_report.json"
        pilot_report_md_path = experiment_root / "pilot_report.md"
        pilot_report_json_path.write_text(json.dumps(pilot_report, sort_keys=True, indent=2), encoding="utf-8")
        pilot_report_md_path.write_text(self._pilot_report_markdown(pilot_report), encoding="utf-8")

        if recovery is not None:
            recovery.commit_tree(
                staged_root=experiment_root,
                canonical_root=canonical_experiment_root,
                cursor={"game_index": 1, "unit": "population_reports_completed"},
                state={"games_completed": 1, "reports_completed": True},
                applied=["game_001", "population_reports"],
            )

        return PopulationRunResult(
            experiment_root=canonical_experiment_root,
            manifest_path=canonical_experiment_root / manifest_path.relative_to(experiment_root),
            summary_json_path=canonical_experiment_root / summary_json_path.relative_to(experiment_root),
            summary_md_path=canonical_experiment_root / summary_md_path.relative_to(experiment_root),
            pilot_report_json_path=canonical_experiment_root / pilot_report_json_path.relative_to(experiment_root),
            pilot_report_md_path=canonical_experiment_root / pilot_report_md_path.relative_to(experiment_root),
        )

    def _prepare_agents(self, *, dry_run: bool, resume: bool = False) -> dict[str, Any]:
        prepared: list[dict[str, Any]] = []
        runner_agents: list[dict[str, Any]] = []
        llm_count = 0
        scripted_count = 0
        runtime_cfg = self.config.raw.get("runtime", {})
        llm_agent_ids: set[str] | None = None
        if isinstance(runtime_cfg, dict) and isinstance(runtime_cfg.get("llm_agent_ids"), list):
            llm_agent_ids = {str(row).strip() for row in runtime_cfg.get("llm_agent_ids", []) if str(row).strip()}
        normalized: list[tuple[PopulationPlayer, Any, str]] = []
        effective_ids: set[str] = set()
        for player in self.config.players:
            bp = load_blueprint(player.blueprint)
            effective_id = player.agent_id or bp.agent_id
            if effective_id in effective_ids:
                raise ValueError(f"Duplicate effective player agent_id: {effective_id}")
            effective_ids.add(effective_id)
            normalized.append((player, bp, effective_id))

        if llm_agent_ids is not None and not llm_agent_ids <= effective_ids:
            raise ValueError(f"Unknown llm_agent_ids: {sorted(llm_agent_ids - effective_ids)}")
        if self.config.agent_state_mode not in {"fresh", "reuse"}:
            raise ValueError(f"Unsupported agent_state_mode: {self.config.agent_state_mode}")
        strict = any(player.object_form for player, _, _ in normalized)
        # Validate every player before mutating any folio.
        for player, _bp, effective_id in normalized:
            runtime_kind = self._runtime_kind(effective_id, llm_agent_ids)
            if player.provider_config is not None and runtime_kind != "llm":
                raise ValueError(f"provider_config is not allowed on scripted player: {effective_id}")
            if runtime_kind == "llm" and (strict or player.provider_config is not None):
                cfg = parse_llm_provider_config(self._player_provider(player), require_selection=True)
                if not dry_run:
                    build_provider_chain(cfg, builder=build_provider_from_config)
            if self.config.agent_state_mode == "reuse" and player.object_form:
                agent_dir = self.agents_root / effective_id
                if agent_dir.exists():
                    self._verify_object_reuse(agent_dir, player, _bp, player.blueprint, effective_id)

        self.agents_root.mkdir(parents=True, exist_ok=True)
        for player, bp, effective_id in normalized:
            blueprint_path = player.blueprint
            agent_dir = self.agents_root / effective_id
            mode = self.config.agent_state_mode
            if mode not in {"fresh", "reuse"}:
                raise ValueError(f"Unsupported agent_state_mode: {mode}")
            generated = False
            if mode == "fresh":
                if resume:
                    if not agent_dir.exists():
                        raise ValueError(f"Cannot resume: prepared agent folio is missing for {effective_id}.")
                else:
                    if agent_dir.exists():
                        shutil.rmtree(agent_dir)
                    self.factory.create_agent(
                        bp, agents_root=self.agents_root, instance_id=effective_id,
                        source_blueprint_path=blueprint_path,
                        provider_provenance=self._player_provider(player),
                    )
                    generated = True
            else:
                if not agent_dir.exists():
                    self.factory.create_agent(
                        bp, agents_root=self.agents_root, instance_id=effective_id,
                        source_blueprint_path=blueprint_path,
                        provider_provenance=self._player_provider(player),
                    )
                    generated = True
                elif player.object_form:
                    self._verify_object_reuse(agent_dir, player, bp, blueprint_path, effective_id)
            report = validate_agent_folio(agent_dir)
            if not report.ok:
                raise ValueError(f"Generated folio invalid for {effective_id}: {report.errors}")
            runtime_kind = self._runtime_kind(effective_id, llm_agent_ids)
            if runtime_kind == "llm":
                llm_count += 1
            else:
                scripted_count += 1
            prepared.append(
                {
                    "agent_id": effective_id,
                    "source_agent_id": bp.agent_id,
                    "display_name": bp.display_name,
                    "runtime": bp.runtime,
                    "runtime_kind": runtime_kind,
                    "blueprint_path": blueprint_path,
                    "blueprint_sha256": _sha256(Path(blueprint_path)),
                    "blueprint_schema_version": bp.schema_version,
                    "generated": generated,
                    "folio_path": self._logical_agent_path(agent_dir),
                    "provider_config": self._player_provider(player),
                }
            )
            runner_agents.append({
                "agent_id": effective_id,
                "runtime_kind": runtime_kind,
                "provider_config": player.provider_config,
                "provider_config_explicit": bool(player.provider_config is not None),
            })
        return {
            "prepared": prepared,
            "runner_agents": runner_agents,
            "llm_count": llm_count,
            "scripted_count": scripted_count,
        }

    def _recovery_identity(self) -> dict[str, Any]:
        raw = json.loads(json.dumps(self.config.raw))
        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: scrub(row) for key, row in value.items()
                        if key.lower() not in {"api_key", "token", "secret", "password"}}
            if isinstance(value, list):
                return [scrub(row) for row in value]
            return value
        raw = scrub(raw)
        blueprints = []
        for player in self.config.players:
            path = Path(player.blueprint)
            blueprints.append({"path": str(path.resolve()), "sha256": _sha256(path)})
        source_root = Path(__file__).parents[1]
        code_hashes = {
            path.relative_to(source_root).as_posix(): _sha256(path)
            for path in sorted(source_root.rglob("*"))
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        }
        repository_root = source_root.parents[1]
        execution_data = {}
        for label, root in (("maps", repository_root / "maps"),
                            ("model_profiles", repository_root / "config" / "model_profiles")):
            if root.is_dir():
                execution_data[label] = {
                    path.relative_to(repository_root).as_posix(): _sha256(path)
                    for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
                }
        routes = [self._player_provider(player) for player in self.config.players]
        from llm_diplomacy.core.prompt_assets import (canonical_diplomacy_primer_path,
            canonical_strategic_imperatives_path, canonical_victory_campaign_doctrine_path)
        assets = {
            "strategic_imperatives": self.config.raw.get("strategic_imperatives_path") or str(canonical_strategic_imperatives_path()),
            "victory_campaign_doctrine": self.config.raw.get("victory_campaign_doctrine_path") or str(canonical_victory_campaign_doctrine_path()),
            "diplomacy_primer": self.config.raw.get("diplomacy_primer_path") or str(canonical_diplomacy_primer_path()),
        }
        asset_hashes = {key: {"path": str(Path(value).resolve()),
                              "sha256": _sha256(Path(value)) if Path(value).is_file() else None}
                        for key, value in assets.items()}
        receipt_identity = {}
        if isinstance(raw.get('memory_handoff_receipt'),str):
            receipt_path = Path(raw['memory_handoff_receipt']).resolve()
            root = (self.base_dir/'experiments'/self.config.experiment_id).resolve()
            if root not in receipt_path.parents or receipt_path.is_symlink():
                raise ValueError('Memory handoff receipt must be inside this experiment')
            receipt_identity['memory_handoff_receipt_sha256'] = _sha256(receipt_path)
        return {**receipt_identity,"experiment_id": self.config.experiment_id, "schema_version": self.config.schema_version,
                "agents_root": str(self._configured_agents_root.resolve()),
                "configuration_sha256": canonical_hash(raw), "blueprints": blueprints,
                "routes_sha256": canonical_hash(routes), "code_sha256": canonical_hash(code_hashes),
                "execution_data_sha256": canonical_hash(execution_data),
                "prompt_assets": asset_hashes}

    def _logical_agent_path(self, agent_dir: Path) -> str:
        if self._active_recovery is None:
            return str(agent_dir)
        staged_root = (self._active_recovery.working_root / "experiments" / self.config.experiment_id).resolve()
        try:
            relative = agent_dir.resolve().relative_to(staged_root)
        except ValueError:
            return str(agent_dir)
        return str((self.base_dir / "experiments" / self.config.experiment_id / relative).resolve())

    def _recovery_store(self, *, resume: bool = True) -> ExperimentRecovery:
        experiment_root = self.base_dir / "experiments" / self.config.experiment_id
        identity = self._recovery_identity()
        correction_policy = (self.config.raw.get("provider_failure_policy") or {}).get("received_invalid_policy", "halt")
        if correction_policy not in {"halt", "repeat_with_feedback_v1"}:
            raise ValueError("Unsupported received_invalid_policy")
        if correction_policy == "repeat_with_feedback_v1":
            budget = self.config.raw.get("budget", {})
            if any(not isinstance(budget.get(key), (int, float)) or isinstance(budget.get(key), bool)
                   or not 0 < budget[key] < float("inf") for key in
                   ("max_provider_calls", "max_input_tokens", "max_output_tokens", "max_estimated_cost_usd")):
                raise ValueError("repeat corrections require finite positive total budgets")
            for player in self.config.players:
                provider = self._player_provider(player)
                if provider.get("kind") in {"fake", "env_stub"}:
                    continue
                if provider.get("api_style") != "responses" or provider.get("service_tier") != "default" or provider.get("prompt_cache_mode") != "explicit":
                    raise ValueError("repeat corrections require Standard Responses with explicit-only cache mode")
        return ExperimentRecovery(experiment_root, identity=identity, resume=resume,
            repeat_received_invalid=correction_policy == "repeat_with_feedback_v1",
            max_wall_seconds=(self.config.raw.get("provider_failure_policy") or {}).get("max_wall_seconds"),
            deadline_utc=(self.config.raw.get("provider_failure_policy") or {}).get("deadline_utc"))

    def recovery_status(self) -> dict[str, Any]:
        return self._recovery_store().status()

    def request_safe_halt(self, reason: str = "operator_request") -> None:
        recovery = self._recovery_store()
        recovery.prepare()
        recovery.request_safe_halt(reason)

    def _runtime_kind(self, agent_id: str, llm_agent_ids: set[str] | None) -> str:
        if llm_agent_ids is not None:
            return "llm" if agent_id in llm_agent_ids else "scripted"
        return "llm" if self.config.runtime_kind == "baseline_llm" else "scripted"

    def _player_provider(self, player: PopulationPlayer) -> dict[str, Any]:
        return canonical_provider_config(player.provider_config if player.provider_config is not None else self._global_provider_raw(), require_selection=True)

    def _verify_object_reuse(self, agent_dir: Path, player: PopulationPlayer, bp: Any, blueprint_path: str, effective_id: str) -> None:
        manifest_path = agent_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"Cannot reuse {effective_id}: missing provenance; use fresh state or another player ID.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        provenance = manifest.get("provenance") if isinstance(manifest, dict) else None
        if not isinstance(provenance, dict):
            raise ValueError(f"Cannot reuse {effective_id}: missing provenance; use fresh state or another player ID.")
        expected_soul = self.factory._soul(bp)
        checks = {
            "effective_agent_id": effective_id,
            "source_blueprint_id": bp.agent_id,
            "source_blueprint_sha256": _sha256(Path(blueprint_path)),
            "soul_sha256": hashlib.sha256(expected_soul.encode("utf-8")).hexdigest(),
        }
        for key, expected in checks.items():
            if provenance.get(key) != expected:
                raise ValueError(f"Cannot reuse {effective_id}: provenance mismatch; use fresh state or another player ID.")
        expected_provider = self._player_provider(player)
        if provenance.get("provider_config") != expected_provider:
            self._verify_provider_control_handoff(agent_dir,effective_id,provenance.get("provider_config"),expected_provider)

    def _verify_provider_control_handoff(self, agent_dir, agent_id, previous, current):
        receipt_name = self.config.raw.get("memory_handoff_receipt")
        if not isinstance(receipt_name,str):
            raise ValueError(f"Cannot reuse {agent_id}: provider configuration mismatch; use fresh state or another player ID.")
        root = (self.base_dir/'experiments'/self.config.experiment_id).resolve()
        receipt_path = Path(receipt_name).resolve()
        if root not in receipt_path.parents or receipt_path.is_symlink():
            raise ValueError('Memory handoff receipt must be an ordinary file inside this experiment')
        receipt = json.loads(receipt_path.read_text())
        body = {key:value for key,value in receipt.items() if key != 'sha256'}
        if receipt.get('sha256') != canonical_hash(body) or receipt.get('kind') != 'standard_no_cache_provider_control_handoff_v1':
            raise ValueError('Memory provider-control handoff receipt integrity mismatch')
        if current.get('service_tier') != 'default' or current.get('prompt_cache_mode') != 'explicit' or {k:v for k,v in current.items() if k not in {'service_tier','prompt_cache_mode'}} != previous:
            raise ValueError('Memory handoff may change only Standard/no-cache request controls')
        record = receipt.get('players',{}).get(agent_id,{})
        if record.get('from_provider_sha256') != canonical_hash(previous) or record.get('to_provider_sha256') != canonical_hash(current):
            raise ValueError('Memory handoff provider binding mismatch')
        source = Path(receipt['source_root'])/'agents'/agent_id
        for name, expected in record.get('files',{}).items():
            if name not in {'manifest.json','soul.md','public_profile.md','persistent_memory.md'} or _sha256(source/name) != expected:
                raise ValueError('Memory handoff source hash mismatch')
            initial = self._active_recovery is None or self._active_recovery.attempt_count_total() == 0
            if (initial or name in {'manifest.json','soul.md'}) and _sha256(agent_dir/name) != expected:
                raise ValueError('Memory handoff copied file hash mismatch')
        if set(record.get('files',{})) != {'manifest.json','soul.md','public_profile.md','persistent_memory.md'}:
            raise ValueError('Memory handoff requires all four copied files')

    def _build_experiment_config(self, prepared_agents: dict[str, Any]) -> ExperimentConfig:
        llm_provider_raw: Any
        if isinstance((self.config.raw.get("runtime") or {}).get("provider_config"), dict):
            llm_provider_raw = dict((self.config.raw.get("runtime") or {}).get("provider_config"))
        elif isinstance(self.config.raw.get("llm_provider"), (dict, str)):
            llm_provider_raw = self.config.raw.get("llm_provider")
        else:
            llm_provider_raw = {"kind": self.config.provider_kind}

        max_ticks = 1
        if isinstance(self.config.raw.get("runtime"), dict) and isinstance(self.config.raw["runtime"].get("max_ticks"), int):
            max_ticks = max(1, int(self.config.raw["runtime"]["max_ticks"]))

        raw_payload: dict[str, Any] = {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "games": {
                "count": self.config.game_count,
                "max_year": self.config.max_year,
                "country_assignment": self.config.country_assignment,
                "seed": self.config.seed,
            },
            "outputs": {
                "observatory": self.config.observatory,
                "summary": self.config.summary,
            },
            "budget": self.config.budget,
            "llm_provider": llm_provider_raw,
            "agent_folio_root": str(self.agents_root),
        }
        if any(row.get("provider_config") is not None for row in prepared_agents["runner_agents"]):
            raw_payload["per_player_provider_mode"] = True
        strategic_path = self.config.raw.get("strategic_imperatives_path")
        if strategic_path is not None:
            raw_payload["strategic_imperatives_path"] = str(strategic_path)
        doctrine_path = self.config.raw.get("victory_campaign_doctrine_path")
        if doctrine_path is not None:
            raw_payload["victory_campaign_doctrine_path"] = str(doctrine_path)
        primer_path = self.config.raw.get("diplomacy_primer_path")
        if primer_path is not None:
            raw_payload["diplomacy_primer_path"] = str(primer_path)
        failure_policy = self.config.raw.get("provider_failure_policy")
        if isinstance(failure_policy, dict):
            raw_payload["provider_failure_policy"] = dict(failure_policy)
        reflection_protocol = self.config.raw.get("reflection_protocol")
        if reflection_protocol is not None:
            raw_payload["reflection_protocol"] = str(reflection_protocol)

        return ExperimentConfig(
            schema_version=self.config.schema_version,
            experiment_id=self.config.experiment_id,
            games=self.config.game_count,
            agents=prepared_agents["runner_agents"],
            rule_engine={"name": "diplomacy"},
            negotiation={"max_ticks": max_ticks},
            seed=self.config.seed,
            raw=raw_payload,
        )

    def _build_manifest(self, prepared_agents: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        provider_metadata = self._provider_metadata()
        providers_by_agent = self._providers_by_agent(prepared_agents["prepared"])
        return {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "agent_state_mode": self.config.agent_state_mode,
            "persistent_memory_handoff": {
                "version": PERSISTENCE_HANDOFF_VERSION,
                "canonical_source": "agents/<agent_id>/persistent_memory.md",
                "enabled_for_reuse": self.config.agent_state_mode == "reuse",
            },
            "dry_run": dry_run,
            "runtime": {
                "kind": self.config.runtime_kind,
                "provider": self.config.provider_kind,
                "llm_agent_ids": (self.config.raw.get("runtime") or {}).get("llm_agent_ids", None),
                **provider_metadata,
            },
            "model_provider": provider_metadata,
            "providers_by_agent": providers_by_agent,
            "provider_mode": _provider_mode(providers_by_agent),
            "games": {
                "count": self.config.game_count,
                "max_year": self.config.max_year,
                "country_assignment": self.config.country_assignment,
                "seed": self.config.seed,
            },
            "budget": self.config.budget,
            "agents": prepared_agents["prepared"],
        }

    def _build_dry_run_report(self, prepared_agents: dict[str, Any]) -> dict[str, Any]:
        max_ticks = 1
        if isinstance(self.config.raw.get("runtime"), dict) and isinstance(self.config.raw["runtime"].get("max_ticks"), int):
            max_ticks = max(1, int(self.config.raw["runtime"]["max_ticks"]))
        assignments = self._planned_assignments(prepared_agents["runner_agents"])
        llm_agents = prepared_agents["llm_count"]
        max_years = max(1, self.config.max_year - 1900)
        max_phases_per_year = 5
        negotiation_phases_per_year = 3
        max_provider_calls_per_llm_per_game = (
            (max_phases_per_year * 2)  # submit_orders + reflect
            + (negotiation_phases_per_year * max_ticks)  # negotiate_tick
            + 1  # cleanup
        ) * max_years
        estimated_provider_calls = max_provider_calls_per_llm_per_game * llm_agents * self.config.game_count
        return {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "dry_run": True,
            "provider_calls_executed": 0,
            "providers_by_agent": self._providers_by_agent(prepared_agents["prepared"]),
            "planned_country_assignments": assignments,
            "estimated_max_provider_calls": estimated_provider_calls,
            "inputs": {
                "games": self.config.game_count,
                "max_year": self.config.max_year,
                "max_ticks": max_ticks,
                "llm_agents": llm_agents,
                "scripted_agents": prepared_agents["scripted_count"],
                "country_assignment_mode": self.config.country_assignment,
                "budget": self.config.budget,
            },
        }

    def _providers_by_agent(self, prepared: list[dict[str, Any]]) -> dict[str, Any]:
        global_provider = canonical_provider_config(self._global_provider_raw())
        out: dict[str, Any] = {}
        for row in prepared:
            if row.get("runtime_kind") != "llm":
                continue
            raw = row.get("provider_config")
            out[str(row["agent_id"])] = canonical_provider_config(raw, require_selection=True) if raw is not None else global_provider
        return dict(sorted(out.items()))

    def _planned_assignments(self, runner_agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        agent_ids = [str(row.get("agent_id", "")) for row in runner_agents if str(row.get("agent_id", ""))]
        if not agent_ids:
            return []
        rows: list[dict[str, Any]] = []
        for game_index in range(1, self.config.game_count + 1):
            ordered = list(agent_ids)
            mode = self.config.country_assignment.strip().lower()
            if mode == "round_robin":
                shift = (game_index - 1) % len(ordered)
                ordered = ordered[shift:] + ordered[:shift]
            elif mode == "seeded_shuffle":
                rng = random.Random(self.config.seed + game_index - 1)
                rng.shuffle(ordered)
            elif mode != "fixed":
                raise ValueError(f"Unsupported country_assignment mode: {self.config.country_assignment}")
            assignment = {power: ordered[idx % len(ordered)] for idx, power in enumerate(POWERS)}
            rows.append({"game_id": f"game_{game_index:03d}", "assignment": assignment})
        return rows

    def _build_summary(
        self,
        experiment_root: Path,
        *,
        started_at: str,
        completed_at: str,
        elapsed_seconds: float,
        metrics_root: Path | None = None,
    ) -> dict[str, Any]:
        metrics_root = metrics_root or experiment_root
        games_root = experiment_root / "games"
        games = sorted([p for p in games_root.iterdir() if p.is_dir()], key=lambda p: p.name) if games_root.exists() else []
        game_rows: list[dict[str, Any]] = []
        by_agent: dict[str, dict[str, Any]] = {}
        by_country: dict[str, dict[str, Any]] = {}
        fallback_counts: dict[str, int] = {}
        message_counts = {"global": 0, "private": 0}
        for game_dir in games:
            result = _load_game_result(game_dir)
            if not result:
                result = {"result": "incomplete", "partial": True}
            assignments = _country_assignments(result)
            message_counts["global"] += _count_jsonl(game_dir / "messages" / "global" / "global.jsonl")
            private_root = game_dir / "messages" / "private"
            private_count = 0
            if private_root.exists():
                for inbox in private_root.rglob("inbox.jsonl"):
                    private_count += _count_jsonl(inbox)
            message_counts["private"] += private_count
            game_rows.append(
                {
                    "game_id": game_dir.name,
                    "country_assignment": assignments,
                    "runtime_kind": self.config.runtime_kind,
                    "provider_kind": self.config.provider_kind,
                    "max_year": self.config.max_year,
                    "outcome": result,
                    "output_path": str(game_dir),
                    "observatory_path": str(game_dir / "observatory" / "index.html"),
                    "reporter_summary_path": str(
                        Path("games") / game_dir.name / "observatory" / "reports" / "reporter_summary.json"
                    ),
                }
            )

            country_results = result.get("country_results", {}) if isinstance(result, dict) else {}
            for country, entry in sorted(country_results.items()):
                if not isinstance(entry, dict):
                    continue
                agent_id = str(entry.get("agent_id", ""))
                owned = int(entry.get("owned_centers", 0)) if isinstance(entry.get("owned_centers", 0), int) else 0
                by_agent.setdefault(agent_id, {"games": 0, "wins": 0, "owned_centers_total": 0})
                by_agent[agent_id]["games"] += 1
                by_agent[agent_id]["owned_centers_total"] += owned
                if agent_id in result.get("winners", []):
                    by_agent[agent_id]["wins"] += 1

                by_country.setdefault(str(country), {"games": 0, "owned_centers_total": 0})
                by_country[str(country)]["games"] += 1
                by_country[str(country)]["owned_centers_total"] += owned

            for agent_dir in sorted((game_dir / "agents").glob("*")):
                outputs_dir = agent_dir / "runtime" / "phase_outputs"
                if not outputs_dir.exists():
                    continue
                for out_file in outputs_dir.glob("*.json"):
                    payload = json.loads(out_file.read_text(encoding="utf-8"))
                    llm_meta = payload.get("llm_meta", {}) if isinstance(payload, dict) else {}
                    if isinstance(llm_meta, dict) and bool(llm_meta.get("fallback_applied", False)):
                        fallback_counts[agent_dir.name] = fallback_counts.get(agent_dir.name, 0) + 1

        cost = {}
        cost_path = metrics_root / "metrics" / "cost_summary.json"
        if cost_path.exists():
            cost = json.loads(cost_path.read_text(encoding="utf-8"))
        provider_metadata = self._provider_metadata(metrics_root)
        usage_path = metrics_root / "metrics" / "provider_usage.json"
        provider_usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
        return {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": elapsed_seconds,
            **provider_metadata,
            "model_provider": provider_metadata,
            "games_run": len(game_rows),
            "games_completed": sum(row["outcome"].get("result") in {"completed", "solo_victory", "draw"} and not row["outcome"].get("partial") for row in game_rows),
            "games_stopped": sum(row["outcome"].get("result") == "stopped" for row in game_rows),
            "games_incomplete": sum(row["outcome"].get("result") != "stopped" and (row["outcome"].get("result") not in {"completed", "solo_victory", "draw"} or bool(row["outcome"].get("partial"))) for row in game_rows),
            "games": game_rows,
            "agent_outcomes": dict(sorted(by_agent.items())),
            "by_country_outcomes": dict(sorted(by_country.items())),
            "message_counts": message_counts,
            "cost_totals": cost,
            "provider_usage": provider_usage,
            "provider_usage_report": "metrics/provider_usage.md",
            "fallback_counts": dict(sorted(fallback_counts.items())),
            "observatory_links": [row["observatory_path"] for row in game_rows],
            "reporter_summary_links": [row["reporter_summary_path"] for row in game_rows],
            "belief_evidence_observability_summary_json": "belief_evidence_observability_summary.json",
            "belief_evidence_observability_summary_md": "belief_evidence_observability_summary.md",
        }

    def _build_pilot_report(
        self,
        experiment_root: Path,
        summary: dict[str, Any],
        *,
        started_at: str,
        completed_at: str,
        elapsed_seconds: float,
        metrics_root: Path | None = None,
    ) -> dict[str, Any]:
        metrics_root = metrics_root or experiment_root
        budget_state = {}
        budget_state_path = metrics_root / "metrics" / "budget" / "budget_state.json"
        if budget_state_path.exists():
            budget_state = json.loads(budget_state_path.read_text(encoding="utf-8"))

        fallback_count = 0
        parse_failure_count = 0
        provider_error_count = 0
        largest_prompt: dict[str, Any] | None = None
        for out_file in (experiment_root / "games").rglob("*.json"):
            if out_file.parent.name != "phase_outputs":
                continue
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            llm_meta = payload.get("llm_meta", {}) if isinstance(payload, dict) else {}
            if not isinstance(llm_meta, dict):
                continue
            if bool(llm_meta.get("fallback_applied", False)):
                fallback_count += 1
            if bool(llm_meta.get("parse_error", False)):
                parse_failure_count += 1
            if isinstance(llm_meta.get("provider_error"), str) and llm_meta.get("provider_error"):
                provider_error_count += 1
            prompt_chars = llm_meta.get("prompt_size_chars")
            if isinstance(prompt_chars, int):
                candidate = {
                    "path": str(out_file),
                    "prompt_size_chars": prompt_chars,
                    "estimated_input_tokens": llm_meta.get("estimated_input_tokens"),
                    "method": payload.get("method") if isinstance(payload, dict) else None,
                    "context_warnings": llm_meta.get("context_warnings", []),
                }
                if largest_prompt is None or int(candidate["prompt_size_chars"]) > int(largest_prompt.get("prompt_size_chars", 0)):
                    largest_prompt = candidate

        observatory_paths = []
        if isinstance(summary, dict):
            observatory_paths = list(summary.get("observatory_links", []))
        bilateral = self._build_bilateral_analysis(experiment_root, summary)
        usage_path = metrics_root / "metrics" / "provider_usage.json"
        provider_usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
        totals = provider_usage.get("totals", {})
        provider_metadata = self._provider_metadata(metrics_root)
        return {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": elapsed_seconds,
            **provider_metadata,
            "model_provider": provider_metadata,
            "provider_calls": totals.get("provider_calls"),
            "input_tokens": totals.get("input_tokens"),
            "output_tokens": totals.get("output_tokens"),
            "estimated_cost_usd": totals.get("estimated_cost_usd"),
            "fallback_count": fallback_count,
            "parse_failure_count": parse_failure_count,
            "provider_error_count": totals.get("provider_errors", provider_error_count),
            "provider_usage": provider_usage,
            "budget_stop_reason": budget_state.get("stop_reason"),
            "budget_stop_policy": budget_state.get("stop_policy"),
            "observatory_paths": observatory_paths,
            "game_outcomes": [row.get("outcome", {}) for row in (summary.get("games", []) if isinstance(summary, dict) else []) if isinstance(row, dict)],
            "largest_prompt": largest_prompt,
            "bilateral_diplomacy_analysis": bilateral,
        }

    def _global_provider_raw(self) -> Any:
        runtime = self.config.raw.get("runtime", {})
        if isinstance(runtime, dict) and isinstance(runtime.get("provider_config"), dict):
            return runtime["provider_config"]
        return self.config.raw.get("llm_provider", {"kind": self.config.provider_kind})

    def _provider_metadata(self, experiment_root: Path | None = None) -> dict[str, Any]:
        runtime = self.config.raw.get("runtime", {})
        selected = runtime.get("llm_agent_ids") if isinstance(runtime, dict) else None
        selected = set(selected) if isinstance(selected, list) else None
        configured = {}
        for player in self.config.players:
            agent_id = player.agent_id or load_blueprint(player.blueprint).agent_id
            if self._runtime_kind(agent_id, selected) == "llm":
                configured[agent_id] = self._player_provider(player)
        mode = _provider_mode(configured)
        if mode == "mixed":
            metadata = {"provider_name": "mixed", "api_style": "mixed", "requested_model": None,
                        "resolved_model": None, "requested_model_profile": None, "base_url": None}
        else:
            raw = next(iter(configured.values()), self._global_provider_raw())
            metadata = parse_llm_provider_config(raw).metadata()
        providers = {agent: {**raw, **parse_llm_provider_config(raw).metadata()} for agent, raw in configured.items()}
        if experiment_root is not None:
            usage_path = experiment_root / "metrics" / "provider_usage.json"
            if usage_path.exists():
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
                for agent, row in providers.items():
                    totals = usage.get("by_agent", {}).get(agent, {})
                    models = totals.get("observed_resolved_models", [])
                    row.update(observed_resolved_models=models, resolved_model=models[0] if len(models) == 1 else None)
                all_models = usage.get("totals", {}).get("observed_resolved_models", [])
                metadata["resolved_model"] = all_models[0] if mode == "single" and len(all_models) == 1 else None
        return {**metadata, "provider_mode": mode, "providers_by_agent": dict(sorted(providers.items()))}

    def _summary_markdown(self, summary: dict[str, Any]) -> str:
        lines = [
            f"# Population Summary: {summary.get('experiment_id', '')}",
            "",
            f"- started_at: {summary.get('started_at')}",
            f"- completed_at: {summary.get('completed_at')}",
            f"- elapsed_seconds: {summary.get('elapsed_seconds')}",
            f"- games_run: {summary.get('games_run', 0)}",
            f"- games_completed: {summary.get('games_completed', 0)}",
            f"- games_stopped: {summary.get('games_stopped', 0)}",
            f"- games_incomplete: {summary.get('games_incomplete', 0)}",
            f"- message_counts: {summary.get('message_counts', {})}",
            "",
            "## Agent Outcomes",
        ]
        for agent_id, row in sorted((summary.get("agent_outcomes") or {}).items()):
            lines.append(
                f"- {agent_id}: games={row.get('games', 0)}, wins={row.get('wins', 0)}, "
                f"owned_centers_total={row.get('owned_centers_total', 0)}"
            )
        lines.append("")
        lines.append("[Provider request, repair, and cost breakdown](metrics/provider_usage.md)")
        lines.append("")
        lines.append("## Fallback Counts")
        for agent_id, count in sorted((summary.get("fallback_counts") or {}).items()):
            lines.append(f"- {agent_id}: {count}")
        lines.append("")
        lines.append("## Game Outcomes")
        games = summary.get("games", [])
        if isinstance(games, list) and games:
            for row in games:
                if not isinstance(row, dict):
                    continue
                game_id = str(row.get("game_id", ""))
                outcome = row.get("outcome", {})
                if not isinstance(outcome, dict):
                    outcome = {}
                result = str(outcome.get("result", "unknown"))
                partial = bool(outcome.get("partial", False))
                stop_reason = outcome.get("stop_reason")
                suffix = f" (partial, stop_reason={stop_reason})" if partial else ""
                lines.append(f"- {game_id}: {result}{suffix}")
        else:
            lines.append("- (no games)")
        lines.append("")
        lines.append("## Observatory Links")
        for path in summary.get("observatory_links", []):
            lines.append(f"- {path}")
        lines.append("")
        lines.append("## Belief and Evidence Observability")
        lines.append("- belief_evidence_observability_summary_json: belief_evidence_observability_summary.json")
        lines.append("- belief_evidence_observability_summary_md: belief_evidence_observability_summary.md")
        lines.append("")
        return "\n".join(lines)

    def _pilot_report_markdown(self, pilot: dict[str, Any]) -> str:
        bilateral = pilot.get("bilateral_diplomacy_analysis", {})
        game_outcomes = pilot.get("game_outcomes", [])
        outcome_lines = ["## Game Outcomes"]
        if isinstance(game_outcomes, list) and game_outcomes:
            for idx, row in enumerate(game_outcomes, start=1):
                if not isinstance(row, dict):
                    continue
                result = str(row.get("result", "unknown"))
                partial = bool(row.get("partial", False))
                stop_reason = row.get("stop_reason")
                suffix = f" (partial, stop_reason={stop_reason})" if partial else ""
                outcome_lines.append(f"- game_{idx:03d}: {result}{suffix}")
        else:
            outcome_lines.append("- (no game outcomes)")
        return "\n".join(
            [
                f"# Pilot Report: {pilot.get('experiment_id', '')}",
                "",
                f"- started_at: {pilot.get('started_at')}",
                f"- completed_at: {pilot.get('completed_at')}",
                f"- elapsed_seconds: {pilot.get('elapsed_seconds')}",
                f"- provider_calls: {pilot.get('provider_calls')}",
                f"- input_tokens: {pilot.get('input_tokens')}",
                f"- output_tokens: {pilot.get('output_tokens')}",
                f"- estimated_cost_usd: {pilot.get('estimated_cost_usd')}",
                f"- fallback_count: {pilot.get('fallback_count')}",
                f"- parse_failure_count: {pilot.get('parse_failure_count')}",
                f"- provider_error_count: {pilot.get('provider_error_count')}",
                f"- budget_stop_reason: {pilot.get('budget_stop_reason')}",
                f"- largest_prompt: {pilot.get('largest_prompt')}",
                "",
                "## Bilateral Diplomacy Analysis",
                f"- private_messages_occurred: {bilateral.get('private_messages_occurred', False)}",
                f"- initiators_of_private: {bilateral.get('initiators_of_private', [])}",
                f"- private_pairs: {bilateral.get('private_pairs', [])}",
                f"- agents_only_global: {bilateral.get('agents_only_global', [])}",
                f"- private_reply_pairs: {bilateral.get('private_reply_pairs', [])}",
                f"- communication_in_reflections: {bilateral.get('communication_in_reflections', {})}",
                f"- communication_affecting_orders: {bilateral.get('communication_affecting_orders', {})}",
                "",
                *outcome_lines,
                "",
                "## Observatory Paths",
                *[f"- {row}" for row in pilot.get("observatory_paths", [])],
                "",
            ]
        )

    def _build_bilateral_analysis(self, experiment_root: Path, summary: dict[str, Any]) -> dict[str, Any]:
        games = summary.get("games", []) if isinstance(summary, dict) else []
        initiators: set[str] = set()
        private_pairs: set[str] = set()
        private_reply_pairs: set[str] = set()
        agents_only_global: set[str] = set()
        reflection_any = False
        orders_any = False
        for row in games:
            if not isinstance(row, dict):
                continue
            game_id = str(row.get("game_id", "")).strip()
            if not game_id:
                continue
            metrics_path = experiment_root / "games" / game_id / "observatory" / "diplomacy_metrics.json"
            if not metrics_path.exists():
                continue
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            for agent in metrics.get("per_agent", []):
                if not isinstance(agent, dict):
                    continue
                if int(agent.get("private_messages_sent", 0)) > 0:
                    initiators.add(str(agent.get("agent_id", "")))
            for pair in (metrics.get("private_messages_by_pair", {}) or {}).keys():
                private_pairs.add(str(pair))
            for pair in metrics.get("private_pairs_with_replies", []) or []:
                private_reply_pairs.add(str(pair))
            for agent in metrics.get("agents_only_global", []) or []:
                agents_only_global.add(str(agent))
            reflection = metrics.get("communication_in_reflections_heuristic", {})
            if isinstance(reflection, dict):
                reflection_any = reflection_any or bool(reflection.get("any", False))
            orders = metrics.get("communication_affecting_orders_heuristic", {})
            if isinstance(orders, dict):
                orders_any = orders_any or bool(orders.get("any", False))
        return {
            "private_messages_occurred": bool(private_pairs),
            "initiators_of_private": sorted([row for row in initiators if row]),
            "private_pairs": sorted([row for row in private_pairs if row]),
            "agents_only_global": sorted([row for row in agents_only_global if row]),
            "private_reply_pairs": sorted([row for row in private_reply_pairs if row]),
            "communication_in_reflections": {"any": reflection_any},
            "communication_affecting_orders": {"any": orders_any},
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_stage_paths(value: Any, stage_root: Path, canonical_root: Path) -> Any:
    stage = str(stage_root.resolve())
    canonical = str(canonical_root.resolve())
    if isinstance(value, dict):
        return {key: _rewrite_stage_paths(row, stage_root, canonical_root) for key, row in value.items()}
    if isinstance(value, list):
        return [_rewrite_stage_paths(row, stage_root, canonical_root) for row in value]
    if isinstance(value, str):
        return value.replace(stage, canonical).replace(stage.replace("\\", "/"), canonical.replace("\\", "/"))
    return value


def _load_game_result(game_dir: Path) -> dict[str, Any]:
    events = game_dir / "logs" / "events.jsonl"
    if not events.exists():
        return {}
    rows = []
    for line in events.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    for row in reversed(rows):
        if row.get("event_type") == "phase_completed" and row.get("phase_id") == "game_end":
            payload = row.get("payload", {})
            if isinstance(payload, dict):
                result = payload.get("result", {})
                if isinstance(result, dict):
                    return result
    return {}


def _country_assignments(result: dict[str, Any]) -> dict[str, str]:
    by_country = result.get("country_results", {}) if isinstance(result, dict) else {}
    out: dict[str, str] = {}
    if not isinstance(by_country, dict):
        return out
    for country, row in sorted(by_country.items()):
        if isinstance(row, dict):
            out[str(country)] = str(row.get("agent_id", ""))
    return out


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _provider_mode(providers_by_agent: dict[str, Any]) -> str:
    fingerprints = {json.dumps(row, sort_keys=True) for row in providers_by_agent.values()}
    return "mixed" if len(fingerprints) > 1 else "single"
