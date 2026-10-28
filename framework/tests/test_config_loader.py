from __future__ import annotations

import json

from llm_diplomacy.config.loader import load_experiment_config


def test_load_json_config(tmp_path) -> None:
    config_data = {
        "schema_version": "1.0",
        "experiment_id": "exp_001",
        "games": 2,
        "agents": [{"agent_id": "agent_alpha"}],
        "rule_engine": {"name": "stub_rule_engine"},
        "negotiation": {"max_ticks": 4},
        "seed": 7,
    }
    config_path = tmp_path / "exp.json"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")

    loaded = load_experiment_config(config_path)

    assert loaded.schema_version == "1.0"
    assert loaded.experiment_id == "exp_001"
    assert loaded.games == 2
    assert loaded.seed == 7
