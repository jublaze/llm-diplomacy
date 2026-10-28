from __future__ import annotations

import json
import os
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from llm_diplomacy.core.prompt_assets import (
    DEFAULT_STRATEGIC_IMPERATIVES,
    canonical_strategic_imperatives_path,
    read_prompt_asset,
)

from .loader import load_blueprint
from .models import AgentBlueprint
from .validator import AgentFolioValidationReport, validate_agent_folio


class AgentFactory:
    def __init__(self, *, schema_version: str = "1.0") -> None:
        self.schema_version = schema_version

    def create_agent(
        self,
        blueprint: AgentBlueprint | str | Path,
        *,
        agents_root: str | Path = "agents",
        instance_id: str | None = None,
        source_blueprint_path: str | Path | None = None,
        provider_provenance: dict[str, Any] | None = None,
    ) -> Path:
        bp = load_blueprint(blueprint) if isinstance(blueprint, (str, Path)) else blueprint
        agents_root_path = Path(agents_root)
        effective_agent_id = instance_id or bp.agent_id
        root = agents_root_path / effective_agent_id
        shared_imperatives = agents_root_path.parent / "strategic_imperatives.md"
        strategic_relpath = os.path.relpath(shared_imperatives, start=root).replace("\\", "/")
        shared_imperatives.parent.mkdir(parents=True, exist_ok=True)
        if not shared_imperatives.exists():
            shared_imperatives.write_text(
                read_prompt_asset(
                    canonical_strategic_imperatives_path(),
                    fallback=DEFAULT_STRATEGIC_IMPERATIVES,
                ),
                encoding="utf-8",
            )
        root.mkdir(parents=True, exist_ok=True)
        (root / "memory").mkdir(parents=True, exist_ok=True)
        (root / "games").mkdir(parents=True, exist_ok=True)

        soul = self._soul(bp)
        manifest = self._manifest(
            bp,
            effective_agent_id=effective_agent_id,
            strategic_imperatives_relpath=strategic_relpath,
            source_blueprint_path=source_blueprint_path,
            soul_sha256=hashlib.sha256(soul.encode("utf-8")).hexdigest(),
            provider_provenance=provider_provenance,
        )
        (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        (root / "soul.md").write_text(soul, encoding="utf-8")
        (root / "persistent_memory.md").write_text(self._persistent_memory(bp, effective_agent_id), encoding="utf-8")
        (root / "public_profile.md").write_text(self._public_profile(bp), encoding="utf-8")
        (root / "memory" / "README.md").write_text(
            "In-game memory artifacts will be written per game.\n",
            encoding="utf-8",
        )
        return root

    def create_and_validate(
        self,
        blueprint: AgentBlueprint | str | Path,
        *,
        agents_root: str | Path = "agents",
    ) -> tuple[Path, AgentFolioValidationReport]:
        path = self.create_agent(blueprint, agents_root=agents_root)
        return path, validate_agent_folio(path)

    def _manifest(
        self,
        blueprint: AgentBlueprint,
        *,
        effective_agent_id: str,
        strategic_imperatives_relpath: str,
        source_blueprint_path: str | Path | None,
        soul_sha256: str,
        provider_provenance: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source_path = Path(source_blueprint_path) if source_blueprint_path is not None else None
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path is not None and source_path.exists() else None
        manifest = {
            "schema_version": self.schema_version,
            "agent_id": effective_agent_id,
            "source_agent_id": blueprint.agent_id,
            "display_name": blueprint.display_name,
            "runtime": {
                "kind": "agent_runtime_profile",
                "profile": blueprint.runtime,
            },
            "supported_capabilities": [
                "negotiate_tick",
                "submit_orders",
                "reflect",
                "game_end_cleanup",
            ],
            "folio_paths": {
                "soul": "soul.md",
                "persistent_memory": "persistent_memory.md",
                "public_profile": "public_profile.md",
                "memory": "memory/",
                "games": "games/",
                "strategic_imperatives": strategic_imperatives_relpath,
            },
            "description": blueprint.description,
            "notes": blueprint.notes,
            "blueprint_snapshot": asdict(blueprint.identity),
            "provenance": {
                "effective_agent_id": effective_agent_id,
                "source_blueprint_id": blueprint.agent_id,
                "source_blueprint_path": str(source_path) if source_path is not None else None,
                "source_blueprint_sha256": source_sha,
                "soul_sha256": soul_sha256,
                "provider_config": provider_provenance,
            },
        }
        return manifest

    def _soul(self, blueprint: AgentBlueprint) -> str:
        identity = blueprint.identity
        priorities = "\n".join(f"- {row}" for row in identity.priorities)
        return (
            f"# Soul: {blueprint.display_name}\n\n"
            f"You believe: {identity.worldview}\n\n"
            f"You prefer alliances shaped by this principle: {identity.alliance_philosophy}\n\n"
            f"You communicate this way: {identity.communication_philosophy}\n\n"
            f"You handle information this way: {identity.information_philosophy}\n\n"
            f"You evaluate evidence this way: {identity.evidence_philosophy}\n\n"
            f"You plan on a {identity.planning_horizon} horizon.\n\n"
            "You seek outcomes aligned with these priorities:\n"
            f"{priorities}\n\n"
            "You avoid incoherent behavior that breaks your enduring identity.\n"
        )

    def _public_profile(self, blueprint: AgentBlueprint) -> str:
        identity = blueprint.identity
        return (
            f"# Public Profile: {blueprint.display_name}\n\n"
            f"{blueprint.description}\n\n"
            f"Communication style: {identity.communication_philosophy}\n\n"
            f"Diplomatic orientation: {identity.alliance_philosophy}\n\n"
            "Reputation sought: reliable strategic counterpart with consistent principles.\n"
        )

    def _persistent_memory(self, blueprint: AgentBlueprint, effective_agent_id: str) -> str:
        return (
            "# Persistent Memory\n\n"
            "This file begins intentionally sparse.\n"
            "Cross-game lessons are appended during game_end_cleanup.\n\n"
            f"Agent: {effective_agent_id}\n"
            "Entries:\n"
        )
