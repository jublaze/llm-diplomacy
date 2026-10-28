from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re


@dataclass(frozen=True)
class MessageBusConfig:
    schema_version: str
    experiment_id: str
    game_id: str
    private_dir: Path
    global_file: Path
    outbox_staging_dir: Path


class MessageBus:
    def __init__(self, config: MessageBusConfig) -> None:
        self.config = config
        self.config.private_dir.mkdir(parents=True, exist_ok=True)
        self.config.global_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.outbox_staging_dir.mkdir(parents=True, exist_ok=True)
        self.config.global_file.touch(exist_ok=True)

    def stage_messages(
        self,
        *,
        phase_id: str,
        tick_index: int,
        agent_id: str,
        messages: list[dict[str, Any]],
    ) -> Path:
        stage_file = self._stage_file(phase_id, tick_index, agent_id)
        stage_file.parent.mkdir(parents=True, exist_ok=True)
        normalized = [self._normalize_message(phase_id, tick_index, agent_id, row) for row in messages]
        with stage_file.open("w", encoding="utf-8") as handle:
            for msg in normalized:
                handle.write(json.dumps(msg, sort_keys=True))
                handle.write("\n")
        return stage_file

    def read_staged(self, *, phase_id: str, tick_index: int, agent_id: str) -> list[dict[str, Any]]:
        path = self._stage_file(phase_id, tick_index, agent_id)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def commit_tick(
        self, *, phase_id: str, tick_index: int, active_agent_ids: list[str]
    ) -> list[dict[str, Any]]:
        missing = [
            agent_id
            for agent_id in sorted(active_agent_ids)
            if not self._stage_file(phase_id, tick_index, agent_id).exists()
        ]
        if missing:
            raise ValueError(f"Tick barrier not satisfied. Missing staged outputs: {missing}")

        all_messages: list[dict[str, Any]] = []
        for agent_id in sorted(active_agent_ids):
            all_messages.extend(self.read_staged(phase_id=phase_id, tick_index=tick_index, agent_id=agent_id))
        active_set = set(active_agent_ids)
        all_messages = [
            msg
            for msg in all_messages
            if not (
                isinstance(msg.get("to"), dict)
                and msg["to"].get("type") == "private"
                and self._is_safe_agent_id(str(msg["to"].get("to_agent_id", "")).strip())
                and str(msg["to"].get("to_agent_id", "")).strip() not in active_set
            )
        ]
        all_messages.sort(key=lambda row: (row["message_id"], row["from_agent_id"]))

        for msg in all_messages:
            target = msg.get("to", {})
            if target.get("type") == "global":
                self._append_jsonl(self.config.global_file, msg)
                continue
            if target.get("type") == "private" and isinstance(target.get("to_agent_id"), str):
                to_agent_id = str(target["to_agent_id"]).strip()
                if not self._is_safe_agent_id(to_agent_id):
                    continue
                inbox_file = self.config.private_dir / to_agent_id / "inbox.jsonl"
                inbox_file.parent.mkdir(parents=True, exist_ok=True)
                self._append_jsonl(inbox_file, msg)

        return all_messages

    def _stage_file(self, phase_id: str, tick_index: int, agent_id: str) -> Path:
        return self.config.outbox_staging_dir / phase_id / str(tick_index) / f"{agent_id}.jsonl"

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")

    def _is_safe_agent_id(self, value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value))

    def _normalize_message(
        self, phase_id: str, tick_index: int, agent_id: str, row: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "schema_version": self.config.schema_version,
            "message_id": str(row.get("message_id", f"msg_{agent_id}_{tick_index}")),
            "experiment_id": self.config.experiment_id,
            "game_id": self.config.game_id,
            "phase_id": phase_id,
            "tick": tick_index,
            "from_agent_id": agent_id,
            "to": row.get("to", {"type": "global"}),
            "body": row.get("body", ""),
            "created_at": str(row.get("created_at", "")),
        }
