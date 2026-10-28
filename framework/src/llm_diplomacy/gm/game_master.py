from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_diplomacy.agents.lifecycle import AgentLifecycleStore, CleanupApplyResult
from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.core.events import EventLog, EventRecord
from llm_diplomacy.core.messages import MessageBus, MessageBusConfig
from llm_diplomacy.core.paths import GamePaths, build_game_paths, ensure_game_dirs
from llm_diplomacy.core.snapshots import SnapshotRecord, SnapshotStore


@dataclass(frozen=True)
class GameMasterContext:
    schema_version: str
    experiment_id: str
    game_id: str


class GameMaster:
    """Game-scoped persistence, messaging, and lifecycle services for runners."""

    def __init__(
        self,
        *,
        config: ExperimentConfig,
        base_dir: str | Path,
        game_id: str,
    ) -> None:
        self.config = config
        self.context = GameMasterContext(
            schema_version=config.schema_version,
            experiment_id=config.experiment_id,
            game_id=game_id,
        )
        self.paths: GamePaths = build_game_paths(base_dir, config.experiment_id, game_id)
        ensure_game_dirs(self.paths)
        self.event_log = EventLog(self.paths.events_file, schema_version=config.schema_version)
        self.snapshot_store = SnapshotStore(
            self.paths.snapshots_dir, schema_version=config.schema_version
        )
        self.message_bus = MessageBus(
            MessageBusConfig(
                schema_version=config.schema_version,
                experiment_id=config.experiment_id,
                game_id=game_id,
                private_dir=self.paths.private_messages_dir,
                global_file=self.paths.global_channel_file,
                outbox_staging_dir=self.paths.outbox_staging_dir,
            )
        )
        self.agent_lifecycle = AgentLifecycleStore(
            self.paths.experiment_root / "agents", schema_version=config.schema_version
        )

    def initialize_game_state(self, initial_payload: dict[str, Any] | None = None) -> SnapshotRecord:
        payload = initial_payload or {"state": "uninitialized"}
        snapshot = self.snapshot_store.write(
            experiment_id=self.context.experiment_id,
            game_id=self.context.game_id,
            payload=payload,
            phase_id=None,
            snapshot_id="initial",
            overwrite=False,
        )
        self._emit_lifecycle_event(
            event_type="game_initialized",
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        return snapshot

    def start_game(self) -> EventRecord:
        return self._emit_lifecycle_event(event_type="game_started", payload={})

    def start_phase(self, phase_id: str) -> EventRecord:
        # The runner owns phase execution; this records its lifecycle boundary.
        return self._emit_lifecycle_event(
            event_type="phase_started", phase_id=phase_id, payload={}
        )

    def complete_phase(self, phase_id: str, outcome: dict[str, Any] | None = None) -> EventRecord:
        # The caller supplies committed snapshot references or the terminal result.
        return self._emit_lifecycle_event(
            event_type="phase_completed",
            phase_id=phase_id,
            payload=outcome or {},
        )

    def commit_snapshot(self, phase_id: str | None, payload: dict[str, Any]) -> SnapshotRecord:
        snapshot = self.snapshot_store.write(
            experiment_id=self.context.experiment_id,
            game_id=self.context.game_id,
            phase_id=phase_id,
            payload=payload,
        )
        self._emit_lifecycle_event(
            event_type="snapshot_committed",
            phase_id=phase_id,
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        return snapshot

    def _emit_lifecycle_event(
        self, *, event_type: str, payload: dict[str, Any], phase_id: str | None = None
    ) -> EventRecord:
        return self.event_log.append(
            event_type=event_type,
            experiment_id=self.context.experiment_id,
            game_id=self.context.game_id,
            phase_id=phase_id,
            payload=payload,
        )

    def stage_negotiation_tick_outputs(
        self,
        *,
        phase_id: str,
        tick_index: int,
        agent_id: str,
        messages: list[dict[str, Any]],
    ) -> Path:
        stage_file = self.message_bus.stage_messages(
            phase_id=phase_id,
            tick_index=tick_index,
            agent_id=agent_id,
            messages=messages,
        )
        self._emit_lifecycle_event(
            event_type="negotiation_tick_staged",
            phase_id=phase_id,
            payload={
                "tick_index": tick_index,
                "agent_id": agent_id,
                "staged_count": len(messages),
            },
        )
        return stage_file

    def commit_negotiation_tick(
        self, *, phase_id: str, tick_index: int, active_agent_ids: list[str]
    ) -> list[dict[str, Any]]:
        committed = self.message_bus.commit_tick(
            phase_id=phase_id,
            tick_index=tick_index,
            active_agent_ids=active_agent_ids,
        )
        self._emit_lifecycle_event(
            event_type="negotiation_tick_committed",
            phase_id=phase_id,
            payload={"tick_index": tick_index, "released_count": len(committed)},
        )
        return committed

    def apply_game_end_cleanup(self, cleanup_payloads: list[dict[str, Any]]) -> list[CleanupApplyResult]:
        results: list[CleanupApplyResult] = []
        for row in cleanup_payloads:
            agent_id = str(row.get("agent_id", ""))
            if not agent_id:
                continue
            result = self.agent_lifecycle.apply_game_end_cleanup(
                agent_id=agent_id,
                game_id=self.context.game_id,
                payload=dict(row.get("payload", {})),
            )
            results.append(result)

        self._emit_lifecycle_event(
            event_type="game_end_cleanup_applied",
            payload={
                "applied_count": len([r for r in results if r.applied]),
                "skipped_count": len([r for r in results if not r.applied]),
            },
        )
        return results
