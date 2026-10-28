from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GamePaths:
    experiment_root: Path
    game_root: Path
    state_dir: Path
    snapshots_dir: Path
    logs_dir: Path
    events_file: Path
    metrics_dir: Path
    messages_dir: Path
    private_messages_dir: Path
    global_messages_dir: Path
    outbox_staging_dir: Path
    global_channel_file: Path


def build_game_paths(base_dir: str | Path, experiment_id: str, game_id: str) -> GamePaths:
    base = Path(base_dir)
    experiment_root = base / "experiments" / experiment_id
    game_root = experiment_root / "games" / game_id
    state_dir = game_root / "state"
    snapshots_dir = state_dir / "snapshots"
    logs_dir = game_root / "logs"
    events_file = logs_dir / "events.jsonl"
    metrics_dir = game_root / "metrics"
    messages_dir = game_root / "messages"
    private_messages_dir = messages_dir / "private"
    global_messages_dir = messages_dir / "global"
    outbox_staging_dir = messages_dir / "outbox_staging"
    global_channel_file = global_messages_dir / "global.jsonl"
    return GamePaths(
        experiment_root=experiment_root,
        game_root=game_root,
        state_dir=state_dir,
        snapshots_dir=snapshots_dir,
        logs_dir=logs_dir,
        events_file=events_file,
        metrics_dir=metrics_dir,
        messages_dir=messages_dir,
        private_messages_dir=private_messages_dir,
        global_messages_dir=global_messages_dir,
        outbox_staging_dir=outbox_staging_dir,
        global_channel_file=global_channel_file,
    )


def ensure_game_dirs(paths: GamePaths) -> None:
    for directory in (
        paths.experiment_root,
        paths.game_root,
        paths.state_dir,
        paths.snapshots_dir,
        paths.logs_dir,
        paths.metrics_dir,
        paths.messages_dir,
        paths.private_messages_dir,
        paths.global_messages_dir,
        paths.outbox_staging_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
