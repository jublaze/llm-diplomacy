from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GameResult:
    schema_version: str
    experiment_id: str
    game_id: str
    payload: dict[str, Any]


class Leaderboard:
    """Objective game results aggregation."""

    def __init__(self, schema_version: str = "1.0") -> None:
        self.schema_version = schema_version
        self._results: list[GameResult] = []

    def record_game_result(
        self, *, experiment_id: str, game_id: str, payload: dict[str, Any]
    ) -> GameResult:
        result = GameResult(
            schema_version=self.schema_version,
            experiment_id=experiment_id,
            game_id=game_id,
            payload=payload,
        )
        self._results.append(result)
        return result

    def results(self) -> list[GameResult]:
        return list(self._results)

    def overall_summary(self) -> dict[str, Any]:
        wins_by_agent: dict[str, int] = {}
        for result in self._results:
            winners = result.payload.get("winners", [])
            if not isinstance(winners, list):
                continue
            for agent_id in winners:
                wins_by_agent[str(agent_id)] = wins_by_agent.get(str(agent_id), 0) + 1
        return {
            "schema_version": self.schema_version,
            "games_count": len(self._results),
            "wins_by_agent": dict(sorted(wins_by_agent.items())),
        }

    def by_country_summary(self) -> dict[str, Any]:
        by_country: dict[str, dict[str, int]] = {}
        for result in self._results:
            country_results = result.payload.get("country_results", {})
            if not isinstance(country_results, dict):
                continue
            for country, entry in country_results.items():
                country_key = str(country)
                by_country.setdefault(country_key, {"games": 0, "wins": 0})
                by_country[country_key]["games"] += 1
                if isinstance(entry, dict) and entry.get("result") == "win":
                    by_country[country_key]["wins"] += 1
        return {
            "schema_version": self.schema_version,
            "countries": dict(sorted(by_country.items())),
        }

    def write_artifacts(self, leaderboard_dir: str | Path) -> tuple[Path, Path]:
        out_dir = Path(leaderboard_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        overall_path = out_dir / "overall.json"
        by_country_path = out_dir / "by_country.json"
        overall_path.write_text(
            json.dumps(self.overall_summary(), sort_keys=True, indent=2), encoding="utf-8"
        )
        by_country_path.write_text(
            json.dumps(self.by_country_summary(), sort_keys=True, indent=2), encoding="utf-8"
        )
        return overall_path, by_country_path
