from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_diplomacy.tools import advanced_model_phase_substitution as tool


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_experiment(tmp_path: Path) -> Path:
    experiment = tmp_path / "experiments" / "exp_substitution"
    game = experiment / "games" / "game_001"

    _write_jsonl(
        game / "logs" / "events.jsonl",
        [
            {
                "schema_version": "1.0",
                "event_id": "evt_end",
                "event_type": "phase_completed",
                "phase_id": "game_end",
                "payload": {
                    "result": {
                        "schema_version": "1.0",
                        "result": "completed",
                        "country_results": {
                            "austria": {"agent_id": "cassandra", "owned_centers": 4},
                            "england": {"agent_id": "dorian", "owned_centers": 3},
                        },
                        "winners": ["cassandra"],
                    }
                },
            }
        ],
    )
    _write_jsonl(
        game / "messages" / "global" / "global.jsonl",
        [
            {
                "schema_version": "1.0",
                "message_id": "g1",
                "phase_id": "1901_spring_negotiation",
                "tick": 1,
                "from_agent_id": "cassandra",
                "to": {"type": "global"},
                "body": "I propose a DMZ in Tyrolia and support coordination.",
            }
        ],
    )
    _write_jsonl(
        game / "messages" / "private" / "cassandra" / "inbox.jsonl",
        [
            {
                "schema_version": "1.0",
                "message_id": "p1",
                "phase_id": "1901_spring_negotiation",
                "tick": 1,
                "from_agent_id": "dorian",
                "to": {"type": "private", "to_agent_id": "cassandra"},
                "body": "Can you support me to BEL if I support your move to SER?",
            }
        ],
    )

    _write_json(
        game / "state" / "snapshots" / "initial.json",
        {
            "schema_version": "1.0",
            "snapshot_id": "initial",
            "experiment_id": "exp_substitution",
            "game_id": "game_001",
            "phase_id": None,
            "created_at": "2026-07-03T00:00:00Z",
            "payload": {
                "units": [
                    {"unit_id": "u_aus_a_vie", "owner": "austria", "status": "active"},
                ],
                "supply_ownership": {"vie": "austria"},
            },
        },
    )

    _write_json(
        game / "phases" / "1901_spring_movement" / "orders" / "submitted_orders.json",
        {
            "schema_version": "1.0",
            "orders": [
                {
                    "schema_version": "1.0",
                    "order_id": "o1",
                    "power": "austria",
                    "unit_ref": "u_aus_a_vie",
                    "type": "MOVE",
                    "payload": {"to": "gal"},
                }
            ],
        },
    )
    _write_json(
        game / "phases" / "1901_spring_movement" / "resolution" / "resolution.json",
        {
            "schema_version": "1.0",
            "phase": "movement",
            "input_state": {
                "supply_ownership": {"vie": "austria"},
            },
            "output_state": {
                "units": [
                    {
                        "unit_id": "u_aus_a_vie",
                        "owner": "austria",
                        "status": "active",
                        "must_retreat": False,
                    }
                ],
                "supply_ownership": {"vie": "austria", "ser": "austria"},
            },
            "movement_outcomes": [
                {"unit_id": "u_aus_a_vie", "from": "vie", "to": "gal", "result": "moved"}
            ],
            "retreat_outcomes": [],
            "adjustment_outcomes": [],
        },
    )
    _write_json(
        game / "phases" / "1901_spring_movement" / "reflection" / "cassandra.json",
        {
            "schema_version": "1.0",
            "result": {
                "reflection": {
                    "memory_update": "England asked for concrete support; useful lead.",
                    "belief_updates": [
                        {
                            "subject": "dorian",
                            "belief": "willing to coordinate",
                            "confidence": "medium",
                            "basis": "message",
                        }
                    ],
                }
            },
        },
    )
    _write_json(
        game / "phases" / "1901_spring_negotiation" / "orders" / "submitted_orders.json",
        {"schema_version": "1.0", "orders": []},
    )
    _write_json(
        game / "phases" / "1901_spring_negotiation" / "resolution" / "resolution.json",
        {"schema_version": "1.0", "phase": "negotiation", "input_state": {}, "output_state": {}},
    )

    request_context = {
        "board_summary": {"year": 1901, "season": "spring", "phase": "movement", "own_units": 1},
        "visible_messages": {
            "global": [{"body": "global message"}],
            "private": [{"body": "private message"}],
        },
        "legal_order_context": {
            "phase": "movement",
            "your_units": [{"unit_ref": "u_aus_a_vie", "legal_orders": [{"type": "MOVE", "payload": {"to": "gal"}}]}],
        },
        "previous_commitments": {"strategic_intent": {"short_term": "gain ser"}},
    }

    for stem, method in [
        ("1901_spring_negotiation_negotiate_tick", "negotiate_tick"),
        ("1901_spring_movement_submit_orders", "submit_orders"),
        ("1901_spring_movement_reflect", "reflect"),
    ]:
        _write_json(
            game / "agents" / "cassandra" / "runtime" / "phase_outputs" / f"{stem}.json",
            {
                "schema_version": "1.0",
                "method": method,
                "llm_meta": {"request_context": request_context, "prompt": "test prompt"},
                "result": {"schema_version": "1.0", "ok": True},
            },
        )
    return experiment


def test_builds_reflection_prompt_from_fixture(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--game",
            "game_001",
            "--phase",
            "1901_spring_movement",
            "--agent",
            "cassandra",
            "--mode",
            "reflection",
            "--dry-run",
        ]
    )
    assert rc == 0
    sample = _read_json(
        experiment
        / "advanced_model_comparison"
        / "samples"
        / "game_001_1901_spring_movement_cassandra_reflection.json"
    )
    assert sample["advanced_model_status"] == "dry_run"
    assert "reflection/belief update" in sample["prompt"]["text"]


def test_builds_negotiation_prompt_from_fixture(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--game",
            "game_001",
            "--phase",
            "1901_spring_movement",
            "--agent",
            "cassandra",
            "--mode",
            "negotiation",
            "--dry-run",
        ]
    )
    assert rc == 0
    sample = _read_json(
        experiment
        / "advanced_model_comparison"
        / "samples"
        / "game_001_1901_spring_negotiation_cassandra_negotiation.json"
    )
    assert "Mode: negotiation" in sample["prompt"]["text"]


def test_builds_orders_prompt_from_fixture(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--game",
            "game_001",
            "--phase",
            "1901_spring_movement",
            "--agent",
            "cassandra",
            "--mode",
            "orders",
            "--dry-run",
        ]
    )
    assert rc == 0
    sample = _read_json(
        experiment
        / "advanced_model_comparison"
        / "samples"
        / "game_001_1901_spring_movement_cassandra_orders.json"
    )
    assert "Mode: orders" in sample["prompt"]["text"]


def test_dry_run_writes_prompt_without_api_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _fixture_experiment(tmp_path)

    def _unexpected(**_: object) -> dict[str, object]:
        raise AssertionError("API should not be called in dry-run")

    monkeypatch.setattr(tool, "call_responses_api", _unexpected)
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--sample-preset",
            "interesting",
            "--mode",
            "reflection",
            "--max-samples",
            "2",
            "--dry-run",
        ]
    )
    assert rc == 0
    summary = _read_json(experiment / "advanced_model_comparison" / "comparison_summary.json")
    assert summary["samples_attempted"] >= 1


def test_responses_api_error_body_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _fixture_experiment(tmp_path)

    def _error_call(**_: object) -> dict[str, object]:
        return {
            "ok": False,
            "status_code": 400,
            "error": "HTTPError: 400",
            "error_body": '{"error":{"message":"bad model"}}',
            "response_json": {"error": {"message": "bad model"}},
            "elapsed_seconds": 0.1,
        }

    monkeypatch.setattr(tool, "call_responses_api", _error_call)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--game",
            "game_001",
            "--phase",
            "1901_spring_movement",
            "--agent",
            "cassandra",
            "--mode",
            "orders",
            "--yes",
        ]
    )
    assert rc == 0
    sample = _read_json(
        experiment
        / "advanced_model_comparison"
        / "samples"
        / "game_001_1901_spring_movement_cassandra_orders.json"
    )
    assert sample["advanced_model_status"] == "api_error"
    assert "bad model" in sample["api_error_body"]


def test_comparison_summary_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _fixture_experiment(tmp_path)

    def _ok_call(**_: object) -> dict[str, object]:
        return {
            "ok": True,
            "status_code": 200,
            "error": None,
            "error_body": "",
            "response_json": {"id": "resp_1"},
            "elapsed_seconds": 0.2,
            "response_id": "resp_1",
            "status": "completed",
            "requested_model": "gpt-5.5",
            "resolved_model": "gpt-5.5-2026-04-23",
            "usage": {"input_tokens": 10, "output_tokens": 12},
            "output_text": '{"strategic_position":"improved","belief_updates":[],"missed_or_ignored_events":[],"relationship_updates":[],"memory_candidates":[]}',
        }

    monkeypatch.setattr(tool, "call_responses_api", _ok_call)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--sample-preset",
            "interesting",
            "--mode",
            "reflection",
            "--max-samples",
            "1",
            "--yes",
        ]
    )
    assert rc == 0
    summary_json = _read_json(experiment / "advanced_model_comparison" / "comparison_summary.json")
    summary_md = (experiment / "advanced_model_comparison" / "comparison_summary.md").read_text(encoding="utf-8")
    assert summary_json["samples_attempted"] == 1
    assert "Advanced Model Phase Substitution Summary" in summary_md


def test_max_samples_limits_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _fixture_experiment(tmp_path)
    calls = {"count": 0}

    def _ok_call(**_: object) -> dict[str, object]:
        calls["count"] += 1
        return {
            "ok": True,
            "status_code": 200,
            "error": None,
            "error_body": "",
            "response_json": {"id": f"resp_{calls['count']}"},
            "elapsed_seconds": 0.2,
            "response_id": f"resp_{calls['count']}",
            "status": "completed",
            "requested_model": "gpt-5.5",
            "resolved_model": "gpt-5.5-2026-04-23",
            "usage": {"input_tokens": 10, "output_tokens": 12},
            "output_text": '{"orders":["A VIE - GAL"],"rationale":"test","strategic_objective":"grow","relationship_dependencies":[],"alternative_considered":"hold","risk_assessment":"medium"}',
        }

    monkeypatch.setattr(tool, "call_responses_api", _ok_call)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    rc = tool.main(
        [
            "--experiment",
            str(experiment),
            "--sample-preset",
            "interesting",
            "--mode",
            "reflection",
            "--mode",
            "negotiation",
            "--mode",
            "orders",
            "--max-samples",
            "2",
            "--yes",
        ]
    )
    assert rc == 0
    assert calls["count"] == 2


def test_unsupported_claim_detection_basic() -> None:
    comparison = tool._comparison_heuristics(
        mode="reflection",
        context={},
        original_output={"memory_update": "no clue"},
        advanced_text="I used a hidden channel and omniscient motive signals.",
        advanced_json={"x": "y"},
    )
    assert comparison["unsupported_claims_detected"] is True


def test_reflection_prompt_includes_compact_limits(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--mode", "reflection",
        "--dry-run",
    ])
    assert rc == 0
    sample = _read_json(
        experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_reflection.json"
    )
    prompt = sample["prompt"]["text"]
    assert "belief_updates: at most 3" in prompt
    assert "missed_or_ignored_events: at most 2" in prompt
    assert "relationship_updates: at most 6" in prompt
    assert "memory_candidates: at most 3" in prompt
    assert "strategic_position: at most 150 words" in prompt


def test_reflection_prompt_requires_valid_json_only(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--mode", "reflection",
        "--dry-run",
    ])
    assert rc == 0
    sample = _read_json(
        experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_reflection.json"
    )
    prompt = sample["prompt"]["text"]
    assert "Return only valid JSON" in prompt
    assert "Do not include markdown" in prompt


def test_output_status_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _fixture_experiment(tmp_path)

    def _ok_call(**_: object) -> dict[str, object]:
        return {
            "ok": True,
            "status_code": 200,
            "error": None,
            "error_body": "",
            "response_json": {"id": "resp_ok"},
            "elapsed_seconds": 0.2,
            "response_id": "resp_ok",
            "status": "completed",
            "requested_model": "gpt-5.5",
            "resolved_model": "gpt-5.5-2026-04-23",
            "usage": {"input_tokens": 10, "output_tokens": 11},
            "output_text": '{"belief_updates":[],"strategic_position":"x","missed_or_ignored_events":[],"relationship_updates":[],"memory_candidates":[]}',
        }

    monkeypatch.setattr(tool, "call_responses_api", _ok_call)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    rc = tool.main([
        "--experiment", str(experiment),
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--mode", "reflection",
        "--yes",
    ])
    assert rc == 0
    sample = _read_json(experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_reflection.json")
    assert sample["output_status"] == "valid_json"
    assert sample["valid_json"] is True


def test_output_status_empty_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _fixture_experiment(tmp_path)

    def _ok_empty(**_: object) -> dict[str, object]:
        return {
            "ok": True,
            "status_code": 200,
            "error": None,
            "error_body": "",
            "response_json": {"id": "resp_empty"},
            "elapsed_seconds": 0.2,
            "response_id": "resp_empty",
            "status": "completed",
            "requested_model": "gpt-5.5",
            "resolved_model": "gpt-5.5-2026-04-23",
            "usage": {"input_tokens": 10, "output_tokens": 11},
            "output_text": "   ",
        }

    monkeypatch.setattr(tool, "call_responses_api", _ok_empty)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    rc = tool.main([
        "--experiment", str(experiment),
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--mode", "reflection",
        "--yes",
    ])
    assert rc == 0
    sample = _read_json(experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_reflection.json")
    assert sample["output_status"] == "empty_output"
    assert sample["empty_output"] is True


def test_output_status_truncated_output_from_unterminated_string() -> None:
    status = tool._classify_output_status(
        response={"ok": True, "status": "completed"},
        output_text='{"x":"unterminated',
        parsed_obj=None,
    )
    assert status["output_status"] == "truncated_output"


def test_output_status_invalid_json() -> None:
    status = tool._classify_output_status(
        response={"ok": True, "status": "completed"},
        output_text='not json but text',
        parsed_obj=None,
    )
    assert status["output_status"] == "invalid_json"


def test_summary_counts_output_quality() -> None:
    payload = tool._summary_payload(
        experiment_id="x",
        requested_model="gpt-5.5",
        reasoning_effort="medium",
        sample_results=[
            {"advanced_model_status": "completed", "api_completed": True, "output_status": "valid_json", "comparison": {}},
            {"advanced_model_status": "completed", "api_completed": True, "output_status": "truncated_output", "comparison": {}},
            {"advanced_model_status": "api_error", "api_completed": False, "output_status": "api_failed", "comparison": {}},
        ],
    )
    assert payload["api_completed"] == 2
    assert payload["valid_json"] == 1
    assert payload["truncated_output"] == 1
    assert payload["api_failed"] == 1


def test_reflection_probe_heuristic_detects_next_probe_or_action() -> None:
    comp = tool._comparison_heuristics(
        mode="reflection",
        context={},
        original_output={},
        advanced_text='{}',
        advanced_json={"belief_updates": [{"next_probe_or_action": "Ask Russia to explain Galicia."}]},
    )
    assert comp["contains_specific_diplomatic_probe"] is True


def test_reflection_probe_heuristic_detects_relationship_next_use() -> None:
    comp = tool._comparison_heuristics(
        mode="reflection",
        context={},
        original_output={},
        advanced_text='{}',
        advanced_json={"relationship_updates": [{"next_use": "Probe France about Marseille and verify claims."}]},
    )
    assert comp["contains_specific_diplomatic_probe"] is True


def test_samples_completed_does_not_imply_valid_json() -> None:
    payload = tool._summary_payload(
        experiment_id="x",
        requested_model="gpt-5.5",
        reasoning_effort="medium",
        sample_results=[
            {"advanced_model_status": "completed", "api_completed": True, "output_status": "truncated_output", "comparison": {}},
        ],
    )
    assert payload["samples_completed"] == 1
    assert payload["valid_json"] == 0
    assert payload["truncated_output"] == 1



def test_negotiation_auto_sampling_prefers_negotiate_tick_phase_outputs(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    # Add an extra negotiate_tick artifact to ensure runtime-output sampling path is used.
    _write_json(
        experiment
        / "games"
        / "game_001"
        / "agents"
        / "dorian"
        / "runtime"
        / "phase_outputs"
        / "1901_fall_negotiation_negotiate_tick.json",
        {
            "schema_version": "1.0",
            "method": "negotiate_tick",
            "llm_meta": {"request_context": {}},
            "result": {"schema_version": "1.0", "ok": True},
        },
    )
    rc = tool.main([
        "--experiment", str(experiment),
        "--mode", "negotiation",
        "--sample-preset", "interesting",
        "--max-samples", "5",
        "--dry-run",
    ])
    assert rc == 0
    samples_dir = experiment / "advanced_model_comparison" / "samples"
    sample_files = sorted([row.name for row in samples_dir.glob("*.json")])
    assert any("_negotiation_" in row and row.endswith("_negotiation.json") for row in sample_files)


def test_no_samples_raises_nonzero_style_error(tmp_path: Path) -> None:
    experiment = tmp_path / "experiments" / "empty_exp"
    (experiment / "games").mkdir(parents=True, exist_ok=True)
    with pytest.raises(RuntimeError, match="No samples found"):
        tool.main([
            "--experiment", str(experiment),
            "--mode", "negotiation",
            "--sample-preset", "interesting",
            "--max-samples", "5",
            "--dry-run",
        ])


def test_stale_samples_are_cleared_before_new_run(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    samples_dir = experiment / "advanced_model_comparison" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    stale = samples_dir / "stale.json"
    stale.write_text("{}", encoding="utf-8")

    rc = tool.main([
        "--experiment", str(experiment),
        "--mode", "reflection",
        "--sample-preset", "interesting",
        "--max-samples", "1",
        "--dry-run",
    ])
    assert rc == 0
    assert not stale.exists()


def test_orders_prompt_includes_compact_limits(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--mode", "orders",
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--max-samples", "1",
        "--dry-run",
    ])
    assert rc == 0
    sample = json.loads((experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_orders.json").read_text(encoding="utf-8"))
    prompt = sample["prompt"]["text"]
    assert "Hard limits:" in prompt
    assert "strategic_objective: max 75 words" in prompt
    assert "rationale: max 150 words" in prompt
    assert "relationship_dependencies: max 6" in prompt
    assert "alternative_considered: max 75 words" in prompt
    assert "risk_assessment: max 125 words" in prompt


def test_orders_prompt_requires_json_only(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--mode", "orders",
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--max-samples", "1",
        "--dry-run",
    ])
    assert rc == 0
    sample = json.loads((experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_orders.json").read_text(encoding="utf-8"))
    prompt = sample["prompt"]["text"]
    assert "Return only valid JSON." in prompt
    assert "Do not include markdown." in prompt
    assert "Do not include commentary outside the JSON." in prompt


def test_orders_prompt_prioritizes_order_list(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--mode", "orders",
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--max-samples", "1",
        "--dry-run",
    ])
    assert rc == 0
    sample = json.loads((experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_orders.json").read_text(encoding="utf-8"))
    prompt = sample["prompt"]["text"]
    assert "The order list is the primary output." in prompt
    assert "1. Identify the agent's current units and legal order context" in prompt
    assert "2. Produce the order list." in prompt


def test_orders_summary_does_not_count_empty_output_as_valid() -> None:
    payload = tool._summary_payload(
        experiment_id="x",
        requested_model="gpt-5.5",
        reasoning_effort="medium",
        sample_results=[
            {"advanced_model_status": "completed", "api_completed": True, "output_status": "empty_output", "comparison": {}},
            {"advanced_model_status": "completed", "api_completed": True, "output_status": "valid_json", "comparison": {}},
        ],
    )
    assert payload["api_completed"] == 2
    assert payload["empty_output"] == 1
    assert payload["valid_json"] == 1


def test_orders_heuristic_detects_orders_and_rationale() -> None:
    comp = tool._comparison_heuristics(
        mode="orders",
        context={},
        original_output={},
        advanced_text='{"orders":["A VIE - GAL"],"rationale":"pressure"}',
        advanced_json={"orders": ["A VIE - GAL"], "rationale": "pressure"},
    )
    assert comp["contains_orders"] is True
    assert comp["contains_rationale"] is True
    assert comp["contains_specific_order_or_province"] is True


def test_orders_heuristic_detects_relationship_dependency_and_risk() -> None:
    comp = tool._comparison_heuristics(
        mode="orders",
        context={},
        original_output={},
        advanced_text='{"risk_assessment":"risk of bounce"}',
        advanced_json={
            "orders": ["A VIE - GAL"],
            "relationship_dependencies": [{"agent_id": "russia", "dependency": "hold", "risk": "stab"}],
            "risk_assessment": "risk of bounce",
        },
    )
    assert comp["mentions_relationship_dependency"] is True
    assert comp["mentions_risk"] is True



def test_orders_mode_defaults_reasoning_effort_low(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--mode", "orders",
        "--dry-run",
    ])
    assert rc == 0
    sample = json.loads((experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_orders.json").read_text(encoding="utf-8"))
    summary = json.loads((experiment / "advanced_model_comparison" / "comparison_summary.json").read_text(encoding="utf-8"))
    assert sample["reasoning_effort"] == "low"
    assert summary["reasoning_effort"] == "low"


def test_reasoning_effort_cli_override_recorded(tmp_path: Path) -> None:
    experiment = _fixture_experiment(tmp_path)
    rc = tool.main([
        "--experiment", str(experiment),
        "--game", "game_001",
        "--phase", "1901_spring_movement",
        "--agent", "cassandra",
        "--mode", "orders",
        "--reasoning-effort", "high",
        "--dry-run",
    ])
    assert rc == 0
    sample = json.loads((experiment / "advanced_model_comparison" / "samples" / "game_001_1901_spring_movement_cassandra_orders.json").read_text(encoding="utf-8"))
    summary = json.loads((experiment / "advanced_model_comparison" / "comparison_summary.json").read_text(encoding="utf-8"))
    assert sample["reasoning_effort"] == "high"
    assert summary["reasoning_effort"] == "high"
