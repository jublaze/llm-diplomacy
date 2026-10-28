from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CleanupApplyResult:
    schema_version: str
    agent_id: str
    game_id: str
    applied: bool
    reason: str | None


class AgentLifecycleStore:
    def __init__(self, root_dir: str | Path, schema_version: str = "1.0") -> None:
        self.root_dir = Path(root_dir)
        self.schema_version = schema_version
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def register_agent(self, agent_id: str) -> Path:
        agent_dir = self._agent_dir(agent_id)
        agent_dir.mkdir(parents=True, exist_ok=True)
        manifest = agent_dir / "manifest.json"
        if not manifest.exists():
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": self.schema_version,
                        "agent_id": agent_id,
                        "games_played": 0,
                    },
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
        memory = agent_dir / "persistent_memory.json"
        if not memory.exists():
            memory.write_text(
                json.dumps(
                    {"schema_version": self.schema_version, "game_summaries": []},
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
        profile = agent_dir / "public_profile.json"
        if not profile.exists():
            profile.write_text(
                json.dumps(
                    {"schema_version": self.schema_version, "profile": {}},
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
        applied_games = agent_dir / "applied_games.json"
        if not applied_games.exists():
            applied_games.write_text(
                json.dumps({"schema_version": self.schema_version, "game_ids": []}, sort_keys=True, indent=2),
                encoding="utf-8",
            )
        history = agent_dir / "cleanup_history.jsonl"
        history.touch(exist_ok=True)
        return agent_dir

    def apply_game_end_cleanup(
        self, *, agent_id: str, game_id: str, payload: dict[str, Any]
    ) -> CleanupApplyResult:
        agent_dir = self.register_agent(agent_id)
        applied_games_path = agent_dir / "applied_games.json"
        applied_data = json.loads(applied_games_path.read_text(encoding="utf-8"))
        applied_ids = set(applied_data.get("game_ids", []))
        if game_id in applied_ids:
            return CleanupApplyResult(
                schema_version=self.schema_version,
                agent_id=agent_id,
                game_id=game_id,
                applied=False,
                reason="already_applied",
            )

        memory_path = agent_dir / "persistent_memory.json"
        memory_data = json.loads(memory_path.read_text(encoding="utf-8"))
        memory_data.setdefault("game_summaries", []).append(
            {
                "schema_version": self.schema_version,
                "game_id": game_id,
                "summary": payload.get("summary", {}),
            }
        )
        memory_path.write_text(json.dumps(memory_data, sort_keys=True, indent=2), encoding="utf-8")

        if isinstance(payload.get("public_profile_update"), dict):
            profile_path = agent_dir / "public_profile.json"
            profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
            profile_data["profile"] = payload["public_profile_update"]
            profile_path.write_text(json.dumps(profile_data, sort_keys=True, indent=2), encoding="utf-8")

        history_entry = {
            "schema_version": self.schema_version,
            "agent_id": agent_id,
            "game_id": game_id,
            "payload": payload,
        }
        with (agent_dir / "cleanup_history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(history_entry, sort_keys=True))
            handle.write("\n")

        applied_ids.add(game_id)
        applied_data["game_ids"] = sorted(applied_ids)
        applied_games_path.write_text(json.dumps(applied_data, sort_keys=True, indent=2), encoding="utf-8")

        manifest_path = agent_dir / "manifest.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_data["games_played"] = int(manifest_data.get("games_played", 0)) + 1
        manifest_path.write_text(json.dumps(manifest_data, sort_keys=True, indent=2), encoding="utf-8")

        return CleanupApplyResult(
            schema_version=self.schema_version,
            agent_id=agent_id,
            game_id=game_id,
            applied=True,
            reason=None,
        )

    def _agent_dir(self, agent_id: str) -> Path:
        return self.root_dir / agent_id
