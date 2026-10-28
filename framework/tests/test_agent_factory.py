from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from llm_diplomacy.factory import AgentFactory, load_blueprint, validate_agent_folio


def _digest_tree(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted([p for p in root.rglob("*") if p.is_file()], key=lambda p: str(p.relative_to(root))):
        rel = str(path.relative_to(root)).replace("\\", "/")
        hasher.update(rel.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def test_blueprint_loader_reads_yaml() -> None:
    blueprint = load_blueprint(Path("blueprints") / "cassandra.yaml")
    assert blueprint.agent_id == "cassandra"
    assert blueprint.runtime == "baseline_llm"
    assert len(blueprint.identity.priorities) >= 3


def test_factory_creates_complete_valid_folio(tmp_path) -> None:
    blueprint = load_blueprint(Path("blueprints") / "dorian.yaml")
    factory = AgentFactory(schema_version="1.0")
    agent_path, report = factory.create_and_validate(blueprint, agents_root=tmp_path / "agents")
    assert report.ok, report.errors
    assert (agent_path / "manifest.json").exists()
    assert (agent_path / "soul.md").exists()
    assert (agent_path / "persistent_memory.md").exists()
    assert (agent_path / "public_profile.md").exists()
    assert (tmp_path / "strategic_imperatives.md").exists()
    manifest = json.loads((agent_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.0"
    assert manifest["agent_id"] == "dorian"
    assert "negotiate_tick" in manifest["supported_capabilities"]
    assert manifest["folio_paths"]["strategic_imperatives"] == "../../strategic_imperatives.md"


def test_factory_is_deterministic_for_same_blueprint(tmp_path) -> None:
    blueprint_path = Path("blueprints") / "marcellus.yaml"
    factory = AgentFactory(schema_version="1.0")
    path_a = factory.create_agent(blueprint_path, agents_root=tmp_path / "agents")
    digest_a = _digest_tree(path_a)
    path_b = factory.create_agent(blueprint_path, agents_root=tmp_path / "agents")
    digest_b = _digest_tree(path_b)
    assert digest_a == digest_b


def test_create_agent_cli(tmp_path) -> None:
    python_exe = Path(".venv") / "Scripts" / "python.exe"
    proc = subprocess.run(
        [
            str(python_exe),
            "scripts/create_agent.py",
            "blueprints/cassandra.yaml",
            "--agents-root",
            str(tmp_path / "agents"),
        ],
        cwd=str(Path.cwd()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip())
    assert payload["valid"] is True
    agent_dir = tmp_path / "agents" / "cassandra"
    report = validate_agent_folio(agent_dir)
    assert report.ok, report.errors
