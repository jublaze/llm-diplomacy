from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner


def _population(tmp_path: Path, *, dry_run: bool = False) -> Path:
    path = tmp_path / "players.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "experiment_id": "per_player",
                "agents": [
                    {"agent_id": "cassandra_a", "blueprint": str(Path("blueprints/cassandra.yaml").resolve()), "provider_config": {"kind": "fake", "model": "model-a"}},
                    {"agent_id": "cassandra_b", "blueprint": str(Path("blueprints/cassandra.yaml").resolve()), "provider_config": {"kind": "fake", "model": "model-b"}},
                    str(Path("blueprints/dorian.yaml").resolve()),
                ],
                "runtime": {"kind": "baseline_llm", "provider": "fake", "llm_agent_ids": ["cassandra_a", "cassandra_b"]},
                "games": {"count": 1, "max_year": 1901, "country_assignment": "round_robin", "seed": 1},
                "outputs": {"observatory": False, "summary": True},
                "dry_run": dry_run,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_same_blueprint_instances_keep_soul_and_provider_isolation(tmp_path: Path) -> None:
    config = load_population_experiment_config(_population(tmp_path))
    result = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    first = tmp_path / "agents" / "cassandra_a"
    second = tmp_path / "agents" / "cassandra_b"
    assert (first / "soul.md").read_bytes() == (second / "soul.md").read_bytes()
    assert json.loads((first / "manifest.json").read_text(encoding="utf-8"))["source_agent_id"] == "cassandra"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["provider_mode"] == "mixed"
    assert manifest["providers_by_agent"]["cassandra_a"]["model"] == "model-a"
    assert manifest["providers_by_agent"]["cassandra_b"]["model"] == "model-b"


def test_strict_direct_provider_preflight_precedes_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_PLAYER_KEY", raising=False)
    config = ExperimentConfig(
        schema_version="1.0", experiment_id="strict", games=1,
        agents=[{"agent_id": "a", "runtime_kind": "llm", "provider_config": {
            "kind": "responses", "model": "x", "api_key_env_var": "MISSING_PLAYER_KEY"
        }}], rule_engine={"name": "diplomacy"}, negotiation={"max_ticks": 1}, seed=1, raw={},
    )
    with pytest.raises(RuntimeError, match="Provider preflight failed for a"):
        ScriptedExperimentRunner(config=config, base_dir=tmp_path).run()
    assert not (tmp_path / "experiments" / "strict").exists()


def test_population_rejects_unsafe_or_inline_provider_credentials(tmp_path: Path) -> None:
    path = _population(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["agents"][0]["agent_id"] = "../unsafe"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        load_population_experiment_config(path)
    raw["agents"][0]["agent_id"] = "safe"
    raw["agents"][0]["provider_config"] = {"kind": "fake", "api_key": "forbidden"}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="environment-variable"):
        load_population_experiment_config(path)
