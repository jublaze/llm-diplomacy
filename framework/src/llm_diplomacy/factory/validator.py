from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentFolioValidationReport:
    agent_dir: Path
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_agent_folio(agent_dir: str | Path) -> AgentFolioValidationReport:
    root = Path(agent_dir)
    errors: list[str] = []
    required_paths = [
        root / "manifest.json",
        root / "soul.md",
        root / "persistent_memory.md",
        root / "public_profile.md",
        root / "memory",
        root / "games",
    ]
    for path in required_paths:
        if not path.exists():
            errors.append(f"missing:{path}")

    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid_manifest_json:{manifest_path}:{exc}")
        else:
            if not isinstance(payload, dict):
                errors.append(f"manifest_not_object:{manifest_path}")
            else:
                for field in (
                    "schema_version",
                    "agent_id",
                    "display_name",
                    "runtime",
                    "supported_capabilities",
                    "folio_paths",
                ):
                    if field not in payload:
                        errors.append(f"manifest_missing:{field}")
    return AgentFolioValidationReport(agent_dir=root, errors=errors)
