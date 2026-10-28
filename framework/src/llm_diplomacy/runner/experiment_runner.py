from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.gm.game_master import GameMaster
from llm_diplomacy.metrics.cost import CostTracker
from llm_diplomacy.metrics.leaderboard import Leaderboard
from llm_diplomacy.observability import ExperimentLogger
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


@dataclass(frozen=True)
class RunSummary:
    schema_version: str
    experiment_id: str
    games_requested: int
    games_completed: int
    leaderboard_overall_path: str
    leaderboard_by_country_path: str
    cost_summary_path: str


class ExperimentRunner:
    def __init__(self, *, config: ExperimentConfig, base_dir: str | Path) -> None:
        self.config = config
        self.base_dir = Path(base_dir)
        self.rule_engine = DiplomacyRuleEngine(schema_version=config.schema_version)
        self.leaderboard = Leaderboard(schema_version=config.schema_version)
        self.cost_tracker = CostTracker(schema_version=config.schema_version)

    def run(self) -> RunSummary:
        experiment_root = self.base_dir / "experiments" / self.config.experiment_id
        experiment_root.mkdir(parents=True, exist_ok=True)
        logger = ExperimentLogger(
            schema_version=self.config.schema_version,
            experiment_id=self.config.experiment_id,
            experiment_root=experiment_root,
        )
        self._write_manifest(experiment_root)
        logger.event("experiment_started", games_requested=self.config.games)

        try:
            for game_index in range(1, self.config.games + 1):
                game_id = f"game_{game_index:03d}"
                logger.event("game_started", game_id=game_id, game_index=game_index)
                gm = GameMaster(config=self.config, base_dir=self.base_dir, game_id=game_id)
                initial = self.rule_engine.initial_state({})
                gm.initialize_game_state(initial)
                gm.start_game()

                result_payload = self._deterministic_game_result(game_index)
                self.leaderboard.record_game_result(
                    experiment_id=self.config.experiment_id,
                    game_id=game_id,
                    payload=result_payload,
                )

                for agent in self.config.agents:
                    agent_id = str(agent.get("agent_id", ""))
                    if not agent_id:
                        continue
                    self.cost_tracker.record_usage(
                        experiment_id=self.config.experiment_id,
                        game_id=game_id,
                        agent_id=agent_id,
                        phase_id=None,
                        usage={"tokens": 0, "estimated_cost": 0.0},
                    )
                gm.apply_game_end_cleanup(
                    [
                        {
                            "agent_id": str(agent.get("agent_id", "")),
                            "payload": {"summary": {"game_id": game_id, "result": result_payload.get("result")}},
                        }
                        for agent in self.config.agents
                        if str(agent.get("agent_id", ""))
                    ]
                )
                gm.complete_phase("game_end", outcome={"schema_version": self.config.schema_version, "result": result_payload})
                logger.event("game_completed", game_id=game_id, outcome=result_payload.get("result"))
        except Exception as exc:
            logger.event("experiment_error", error=f"{exc.__class__.__name__}: {exc}")
            raise

        leaderboard_dir = experiment_root / "leaderboard"
        overall_path, by_country_path = self.leaderboard.write_artifacts(leaderboard_dir)
        cost_path = self.cost_tracker.write_summary(experiment_root / "metrics" / "cost_summary.json")

        summary = RunSummary(
            schema_version=self.config.schema_version,
            experiment_id=self.config.experiment_id,
            games_requested=self.config.games,
            games_completed=self.config.games,
            leaderboard_overall_path=str(overall_path),
            leaderboard_by_country_path=str(by_country_path),
            cost_summary_path=str(cost_path),
        )
        (experiment_root / "run_summary.json").write_text(
            json.dumps(asdict(summary), sort_keys=True, indent=2), encoding="utf-8"
        )
        logger.event("experiment_completed", games_completed=self.config.games)
        return summary

    def _deterministic_game_result(self, game_index: int) -> dict[str, Any]:
        powers = list(self.rule_engine.map_def.powers)
        agent_ids = [str(agent.get("agent_id", "")) for agent in self.config.agents if str(agent.get("agent_id", ""))]
        country_results: dict[str, dict[str, Any]] = {}
        winners: list[str] = []
        if agent_ids:
            winner = agent_ids[(game_index - 1) % len(agent_ids)]
            winners = [winner]
            for idx, power in enumerate(powers):
                assigned = agent_ids[idx % len(agent_ids)]
                country_results[power] = {
                    "agent_id": assigned,
                    "result": "win" if assigned == winner else "loss",
                }
        return {
            "schema_version": self.config.schema_version,
            "result": "completed",
            "winners": winners,
            "country_results": country_results,
        }

    def _write_manifest(self, experiment_root: Path) -> None:
        manifest = {
            "schema_version": self.config.schema_version,
            "experiment_id": self.config.experiment_id,
            "games": self.config.games,
            "agents": self.config.agents,
            "seed": self.config.seed,
        }
        (experiment_root / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
        )
