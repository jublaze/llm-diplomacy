import copy
import ast
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_diplomacy.compliance.artifacts import ArtifactValidator
from llm_diplomacy.compliance.event_contracts import STREAMS, validate_core_event
from llm_diplomacy.population import PopulationExperimentRunner
from llm_diplomacy.observability import ExperimentLogger
from llm_diplomacy.runner.scripted_experiment_runner import ScriptedExperimentRunner
from test_reliability_provider_cost import population_config


def envelope(event, **fields):
    return {"schema_version": "1.0", "event_type": event, "experiment_id": "e", "timestamp": "t", **fields}


CASES = [
    ("events.jsonl", envelope("game_initialized", event_id="id", game_id="g", phase_id=None, payload={"snapshot_id": "s"}), ["payload", "snapshot_id"]),
    ("events.jsonl", envelope("game_started", event_id="id", game_id="g", phase_id=None, payload={}), ["event_id"]),
    ("events.jsonl", envelope("phase_started", event_id="id", game_id="g", phase_id="p", payload={}), ["phase_id"]),
    ("events.jsonl", envelope("phase_completed", event_id="id", game_id="g", phase_id="p", payload={"snapshot_id": "s", "phase": "movement"}), ["payload", "snapshot_id"]),
    ("events.jsonl", envelope("phase_completed", event_id="id", game_id="g", phase_id="game_end", payload={"result": {"result": "completed", "winners": [], "country_results": {}}}), ["payload", "result", "country_results"]),
    ("events.jsonl", envelope("snapshot_committed", event_id="id", game_id="g", phase_id=None, payload={"snapshot_id": "s"}), ["payload", "snapshot_id"]),
    ("events.jsonl", envelope("negotiation_tick_staged", event_id="id", game_id="g", phase_id="p", payload={"tick_index": 0, "agent_id": "a", "staged_count": 0}), ["payload", "staged_count"]),
    ("events.jsonl", envelope("negotiation_tick_committed", event_id="id", game_id="g", phase_id="p", payload={"tick_index": 0, "released_count": 0}), ["payload", "released_count"]),
    ("events.jsonl", envelope("game_end_cleanup_applied", event_id="id", game_id="g", phase_id=None, payload={"applied_count": 0, "skipped_count": 0}), ["payload", "applied_count"]),
    ("experiment_events.jsonl", envelope("phase_completed", game_id="g", phase_id="p", phase="movement"), ["phase_id"]),
    ("experiment_events.jsonl", envelope("game_completed", game_id="g", outcome="completed"), ["outcome"]),
    ("experiment_events.jsonl", envelope("game_stopped", game_id="g", reason="max_provider_calls", stop_policy="stop_experiment"), ["reason"]),
    ("experiment_events.jsonl", envelope("experiment_completed", completed_games=1), ["completed_games"]),
    ("experiment_events.jsonl", envelope("experiment_error", error="error"), ["error"]),
    ("experiment_events.jsonl", envelope("budget_state", provider_calls=1, input_tokens=None, output_tokens=2, estimated_cost_usd=None, stop_policy="stop_experiment"), ["provider_calls"]),
    ("budget_events.jsonl", envelope("budget_check", payload={"game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "allow_provider_call": True}), ["payload", "allow_provider_call"]),
    ("budget_events.jsonl", envelope("budget_exceeded", payload={"game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "reason": "max_provider_calls", "stop_policy": "stop_experiment"}), ["payload", "reason"]),
    ("budget_events.jsonl", envelope("budget_usage_update", payload={"game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "usage": {}, "totals": {"total_provider_calls": 1, "total_input_tokens": None, "total_output_tokens": None, "total_estimated_cost_usd": None}}), ["payload", "totals", "total_provider_calls"]),
    ("budget_events.jsonl", envelope("provider_error", payload={"game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "error": "unknown billing"}), ["payload", "error"]),
    ("provider_attempts.jsonl", {"schema_version": "1.0", "kind": "request", "game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "attempt_index": 0, "provider": {}, "error_type": None, "resolved_model": "m", "input_tokens": None, "output_tokens": 2, "estimated_cost_usd": None, "post_elimination_cleanup": False}, ["attempt_index"]),
    ("provider_attempts.jsonl", {"schema_version": "1.0", "kind": "blocked", "game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "attempt_index": 0, "provider": {}, "error_type": None, "resolved_model": None, "input_tokens": None, "output_tokens": None, "estimated_cost_usd": None, "post_elimination_cleanup": False}, ["attempt_index"]),
    ("provider_attempts.jsonl", {"schema_version": "1.0", "kind": "lifecycle", "game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "fallback": False, "post_elimination_cleanup": False}, ["fallback"]),
    ("provider_attempts.jsonl", {"schema_version": "1.0", "kind": "eliminated_skip", "game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect", "post_elimination_cleanup": False}, ["kind"]),
]


FIRST_PARTY_EXPERIMENT_EVENTS = {
    "experiment_started", "experiment_completed", "experiment_stopped", "experiment_error",
    "systemic_provider_failure", "game_started", "game_completed", "game_stopped", "game_error",
    "game_summary", "observatory_generated", "phase_started", "phase_completed",
    "orders_validated", "board_resolution_summary", "phase_resolved", "agent_invocation_started",
    "agent_invocation_completed", "agent_invocation_failed", "agent_invocation_skipped_eliminated",
    "eliminated_agent_final_cleanup_completed", "power_eliminated", "prompt_assets_loaded",
    "prompt_check", "reflection_generated", "reflection_validated", "reflection_fallback_applied",
    "reflection_repaired", "orders_generated", "orders_repaired", "orders_fallback_applied",
    "relationship_snapshot", "negotiation_tick_participants", "messages_generated", "message_dropped",
    "message_committed", "budget_state", "budget_stop_triggered", "budget_warning", "budget_milestone",
}


EXPERIMENT_SAMPLES = [
    (envelope("experiment_started", games_requested=1, max_years=1, negotiation_ticks=1, provider={}), ["games_requested"]),
    (envelope("experiment_completed", completed_games=1, games_requested=1), ["completed_games"]),
    (envelope("experiment_stopped", reason="budget", stop_policy="stop_experiment", completed_games=0), ["reason"]),
    (envelope("experiment_error", error="failed", game_id="g", games_completed=0), ["error"]),
    (envelope("systemic_provider_failure", game_id="g", phase_id=None, tick=None, provider_calls=1, provider_errors=1, provider={}), ["provider_calls"]),
    (envelope("game_started", game_id="g", game_index=1), ["game_index"]),
    (envelope("game_started", game_id="g", game_index=1, assignments={"england": "a"}), ["game_index"]),
    (envelope("game_completed", game_id="g", outcome="completed"), ["outcome"]),
    (envelope("game_completed", game_id="g", outcome="solo_victory", winners=["a"]), ["outcome"]),
    (envelope("game_stopped", game_id="g", reason="budget", stop_policy="stop_experiment"), ["reason"]),
    (envelope("game_error", game_id="g", error="failed"), ["error"]),
    (envelope("game_summary", game_id="g", leader_country=None, leader_agent_id=None, leader_centers=None, most_active_diplomat=None, private_messages=0, global_messages=0, largest_private_pair=None, largest_private_pair_count=0, fallbacks=0, parse_failures=0, negotiation_calls=0, negotiation_parse_failures=0, negotiation_schema_failures=0, negotiation_truncated_outputs=0, negotiation_fallbacks=0, negotiation_fallback_rate=0.0, negotiation_outputs_trimmed=0, reflection_first_attempt_valid=0, reflection_repair_success=0, reflection_fallbacks=0, result="draw"), ["result"]),
    (envelope("observatory_generated", game_id="g", observatory_path="games/g/observatory/index.html"), ["observatory_path"]),
    (envelope("phase_started", game_id="g", phase_id="1901_spring_negotiation", phase="negotiation"), ["phase"]),
    (envelope("phase_completed", game_id="g", phase_id="1901_spring_negotiation", phase="negotiation"), ["phase"]),
    (envelope("orders_validated", game_id="g", phase_id="1901_spring_movement", submitted_count=1, validated_count=1, invalid_count=0), ["submitted_count"]),
    (envelope("orders_validated", game_id="g", phase_id="1901_spring_movement", agent_id="a", country=None, invalid_reasons=[], output_artifact_path="x.json"), ["country"]),
    (envelope("board_resolution_summary", game_id="g", phase_id="p", phase="movement", summary={}, resolution_artifact_path="x.json"), ["summary"]),
    (envelope("phase_resolved", game_id="g", phase_id="p", valid_orders=1, invalid_orders=0, output_state_snapshot="s"), ["valid_orders"]),
    (envelope("agent_invocation_started", game_id="g", phase_id="p", agent_id="a", country=None, runtime_kind="scripted", method="reflect", tick=None), ["runtime_kind"]),
    (envelope("agent_invocation_completed", game_id="g", phase_id="p", agent_id="a", country=None, runtime_kind="llm", method="reflect", tick=None, ok=True, fallback=False, parse_error=False, repair_count=0, provider_model=None, provider_error=None, provider={}, usage={}, output_artifact_path="x.json", prompt_checks=None, prompt_assets=None), ["ok"]),
    (envelope("agent_invocation_failed", game_id="g", phase_id="p", agent_id="a", country=None, runtime_kind="llm", method="reflect", error="failed", tick=None), ["error"]),
    (envelope("agent_invocation_skipped_eliminated", game_id="g", phase_id="p", agent_id="a", country=None, method="reflect", elimination_lifecycle_version="v1", legacy_artifact=False, skipped_provider_call=True, elimination_records=[]), ["skipped_provider_call"]),
    (envelope("eliminated_agent_final_cleanup_completed", game_id="g", phase_id="p", agent_id="a", elimination=[], final_cleanup_usage={}), ["final_cleanup_usage"]),
    (envelope("power_eliminated", game_id="g", phase_id="p", elimination={}), ["elimination"]),
    (envelope("prompt_assets_loaded", game_id="g", prompt_profile="default", prompt_assets={}), ["prompt_assets"]),
    (envelope("prompt_check", game_id="g", phase_id="p", agent_id="a", runtime_kind="llm", method="reflect", tick=None, prompt_chars=1, prompt_hash="sha256:x", estimated_input_tokens=None, checks={}, prompt_assets={}, prompt_profile="default", output_artifact_path="x.json"), ["checks"]),
    (envelope("reflection_generated", game_id="g", phase_id="p", agent_id="a", country=None, reflection_summary={}, output_artifact_path="x.json"), ["reflection_summary"]),
    (envelope("reflection_validated", game_id="g", phase_id="p", agent_id="a", country=None, fallback=False, parse_error=False, output_artifact_path="x.json"), ["fallback"]),
    (envelope("reflection_fallback_applied", game_id="g", phase_id="p", agent_id="a", country=None, parse_error_category=None, schema_validation_error_category=None, likely_truncated=False, repair_attempted=False, repair_succeeded=False, output_artifact_path="x.json"), ["repair_succeeded"]),
    (envelope("reflection_repaired", game_id="g", phase_id="p", agent_id="a", country=None, repair_attempts=1, repair_success=True, output_artifact_path="x.json"), ["repair_attempts"]),
    (envelope("orders_generated", game_id="g", phase_id="p", agent_id="a", country=None, submitted_count=1, effective_count=1, order_summaries=[{}], output_artifact_path="x.json"), ["submitted_count"]),
    (envelope("orders_repaired", game_id="g", phase_id="p", agent_id="a", country=None, repair_attempts=1, repair_success=True, output_artifact_path="x.json"), ["repair_success"]),
    (envelope("orders_fallback_applied", game_id="g", phase_id="p", agent_id="a", country=None, invalid_reasons=[], output_artifact_path="x.json"), ["output_artifact_path"]),
    (envelope("relationship_snapshot", game_id="g", phase_id="p", agent_id="a", country=None, tick=0, relationships=[], output_artifact_path="x.json"), ["tick"]),
    (envelope("negotiation_tick_participants", game_id="g", phase_id="p", tick=0, active_agent_ids=["a"], active_participant_count=1), ["active_participant_count"]),
    (envelope("messages_generated", game_id="g", phase_id="p", tick=0, agent_id="a", country=None, generated_count=0, dropped_count=0, message_summaries=[], output_artifact_path="x.json"), ["generated_count"]),
    (envelope("message_dropped", game_id="g", phase_id="p", tick=0, agent_id="a", country=None, reason="invalid", to_agent_id=None, output_artifact_path="x.json"), ["reason"]),
    (envelope("message_committed", game_id="g", phase_id="p", tick=0, released_count=0, released_global=0, released_private=0, global_artifact_path="x.jsonl", private_artifact_root="private"), ["released_count"]),
    (envelope("budget_state", provider_calls=0, stop_policy="stop_experiment", game_id=None, phase_id=None, input_tokens=None, output_tokens=None, estimated_cost_usd=None, stop_reason=None), ["input_tokens"]),
    (envelope("budget_stop_triggered", provider_calls=1, stop_policy="stop_experiment", stop_reason="max_provider_calls", game_id="g", phase_id="p", input_tokens=None, output_tokens=None, estimated_cost_usd=None), ["stop_reason"]),
    (envelope("budget_warning", game_id=None, phase_id=None, metric="provider_calls", usage=8.0, limit=10.0, ratio=0.8), ["ratio"]),
    (envelope("budget_milestone", game_id=None, phase_id=None, metric="provider_calls", milestone="90%", usage=9.0, limit=10.0, ratio=0.9), ["milestone"]),
]


@pytest.mark.parametrize("row,path", EXPERIMENT_SAMPLES)
def test_first_party_experiment_samples_accept_and_reject_mutations(tmp_path, row, path):
    assert validate_core_event(row, "experiment_events.jsonl") == []
    for mutation in ("missing", "wrong_type"):
        changed = copy.deepcopy(row)
        target = changed
        for part in path[:-1]:
            target = target[part]
        if mutation == "missing":
            del target[path[-1]]
        else:
            target[path[-1]] = []
        artifact = tmp_path / "experiment_events.jsonl"
        artifact.write_text(json.dumps(row) + "\n" + json.dumps(changed) + "\n", encoding="utf-8")
        report = ArtifactValidator().validate_experiment_dir(tmp_path)
        errors = [error for error in report.errors if error.category == "invalid_event_payload"]
        assert errors and all(error.line_no == 2 for error in errors)
        assert any("/".join(path) in error.detail for error in errors)


@pytest.mark.parametrize("stream,row,path", CASES)
def test_known_positive_contracts(stream, row, path):
    assert validate_core_event(row, stream) == []


@pytest.mark.parametrize("mutation", ["missing", "wrong_type"])
@pytest.mark.parametrize("stream,row,path", CASES)
def test_bad_core_payload_has_file_line_and_field(tmp_path, stream, row, path, mutation):
    changed = copy.deepcopy(row)
    target = changed
    for part in path[:-1]:
        target = target[part]
    if mutation == "missing":
        del target[path[-1]]
    else:
        target[path[-1]] = []
    artifact = tmp_path / stream
    artifact.write_text(json.dumps(row) + "\n" + json.dumps(changed) + "\n")
    report = ArtifactValidator().validate_experiment_dir(tmp_path)
    errors = [error for error in report.errors if error.category == "invalid_event_payload"]
    assert errors
    assert all(error.line_no == 2 and error.path == artifact for error in errors)
    assert any("/".join(path) in error.detail for error in errors)


@pytest.mark.parametrize("result", [
    {"result": "completed", "partial": True, "winners": [], "country_results": {}},
    {"result": "stopped", "partial": False, "stop_reason": "budget", "winners": [], "country_results": {}},
    {"result": "stopped", "partial": True, "stop_reason": "budget", "winners": ["a"], "country_results": {}},
    {"result": "unknown", "winners": [], "country_results": {}},
])
def test_contradictory_terminal_results_fail(result):
    row = envelope("phase_completed", event_id="id", game_id="g", phase_id="game_end", payload={"result": result})
    assert validate_core_event(row, "events.jsonl")


@pytest.mark.parametrize("value", [-1, True, float("inf"), float("nan"), "3"])
def test_invalid_counters_fail(value):
    row = copy.deepcopy(CASES[-4][1])
    row["input_tokens"] = value
    assert validate_core_event(row, "provider_attempts.jsonl")


def test_literal_producer_inventory_matches_registered_contracts():
    source_root = Path(__file__).parents[1] / "src" / "llm_diplomacy"

    def literal_names(files, methods):
        found = set()
        for relative in files:
            tree = ast.parse((source_root / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                if name not in methods:
                    continue
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
                found.update(
                    kw.value.value for kw in node.keywords
                    if kw.arg == "event_type" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                )
        return found

    experiment_emitters = literal_names(
        ["runner/experiment_runner.py", "runner/scripted_experiment_runner.py"],
        {"event", "_emit_event"},
    )
    game_emitters = literal_names(["gm/game_master.py"], {"_emit_lifecycle_event"})
    budget_emitters = literal_names(["guardrails/budget.py"], {"_append_event"})
    assert experiment_emitters == FIRST_PARTY_EXPERIMENT_EVENTS
    assert game_emitters == {
        "game_initialized", "game_started", "phase_started", "phase_completed", "snapshot_committed",
        "negotiation_tick_staged", "negotiation_tick_committed", "game_end_cleanup_applied",
    }
    assert budget_emitters == {"budget_check", "budget_exceeded", "budget_usage_update", "provider_error",
                               "provider_reservation", "provider_reservation_uncertain"}
    provider_source = "\n".join(
        (source_root / relative).read_text(encoding="utf-8")
        for relative in ("metrics/provider_usage.py", "runner/scripted_experiment_runner.py")
    )
    assert {kind for kind in ("request", "blocked", "lifecycle", "eliminated_skip") if f'"{kind}"' in provider_source} == {
        "request", "blocked", "lifecycle", "eliminated_skip",
    }


def test_actual_invocation_emitter_preserves_prompt_metadata_variants(tmp_path):
    runner = object.__new__(ScriptedExperimentRunner)
    runner.logger = None
    runner.provider_usage = None
    runner._game_runtime_stats = {}
    runner._provider_metadata = lambda: {}
    runner._relpath = lambda _path: "output.json"
    runner._invocation_log_lines = lambda **_kwargs: []
    runner._prompt_checks = lambda _prompt: {"contains_rules": True}
    runner._prompt_assets_in_prompt = lambda **_kwargs: {"prompt_profile": "default"}
    emitted = []
    runner._emit_event = lambda event_type, **fields: emitted.append({"event_type": event_type, **fields})
    payload = {}
    runner._read_phase_output_payload = lambda **_kwargs: (tmp_path / "output.json", payload)
    gm = SimpleNamespace(context=SimpleNamespace(game_id="g"), paths=SimpleNamespace(game_root=tmp_path))

    def capture(runtime_kind):
        start = len(emitted)
        runner._log_agent_invocation_completed(
            gm=gm, phase_id="p", agent_id="a", country=None, runtime_kind=runtime_kind,
            method="reflect", result=SimpleNamespace(ok=True, usage={}),
        )
        return next(row for row in emitted[start:] if row["event_type"] == "agent_invocation_completed")

    def wrap(row):
        return {"schema_version": "1.0", "experiment_id": "e", "timestamp": "t", **row}

    no_metadata = capture("llm")
    assert no_metadata["prompt_checks"] is None and no_metadata["prompt_assets"] is None
    assert validate_core_event(wrap(no_metadata), "experiment_events.jsonl") == []

    payload = {"llm_meta": {"prompt": "rules", "prompt_size_chars": 5, "estimated_input_tokens": 2}}
    normal_metadata = capture("llm")
    assert isinstance(normal_metadata["prompt_checks"], dict)
    assert isinstance(normal_metadata["prompt_assets"], dict)
    assert validate_core_event(wrap(normal_metadata), "experiment_events.jsonl") == []

    payload = {}
    scripted = capture("scripted")
    assert "prompt_checks" not in scripted and "prompt_assets" not in scripted
    assert validate_core_event(wrap(scripted), "experiment_events.jsonl") == []
    malformed = {**normal_metadata, "prompt_checks": []}
    assert validate_core_event(wrap(malformed), "experiment_events.jsonl")


def test_unknown_event_types_are_envelope_only_on_event_streams(tmp_path):
    rows = {
        "events.jsonl": envelope("future_event", event_id="id", game_id="g", phase_id=None, payload={}),
        "experiment_events.jsonl": envelope("future_event", arbitrary_future_payload={"x": 1}),
        "budget_events.jsonl": envelope("future_event", payload={"x": 1}),
        "provider_attempts.jsonl": {
            "kind": "request", "game_id": "g", "phase_id": "p", "agent_id": "a",
            "method": "reflect", "attempt_index": 0, "provider": {}, "error_type": None,
            "resolved_model": None, "input_tokens": None, "output_tokens": None,
            "estimated_cost_usd": None, "post_elimination_cleanup": False, "event_type": "future_event",
        },
    }
    for stream, row in rows.items():
        assert validate_core_event(row, stream) == []
        (tmp_path / stream).write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = ArtifactValidator().validate_experiment_dir(tmp_path)
    assert not [error for error in report.errors if error.category == "invalid_event_payload"]
    invalid = copy.deepcopy(rows["provider_attempts.jsonl"])
    invalid["kind"] = "future_event"
    assert validate_core_event(invalid, "provider_attempts.jsonl")


@pytest.mark.parametrize("event_type", [[], {}, {"unhashable": True}])
def test_malformed_event_discriminators_return_complete_redacted_diagnostics(tmp_path, event_type):
    first = envelope(event_type, experiment_id="SENSITIVE_EVENT_VALUE", payload={"secret": "SENSITIVE_PAYLOAD_VALUE"})
    second = envelope("budget_state")
    path = tmp_path / "experiment_events.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
    report = ArtifactValidator().validate_experiment_dir(tmp_path)
    errors = [error for error in report.errors if error.category == "invalid_event_payload"]
    assert {error.line_no for error in errors} == {1, 2}
    assert "SENSITIVE_EVENT_VALUE" not in " ".join(error.detail for error in errors)
    assert "SENSITIVE_PAYLOAD_VALUE" not in " ".join(error.detail for error in errors)


def test_huge_integer_cost_does_not_abort_artifact_validation(tmp_path):
    row = envelope("budget_state", provider_calls=0, stop_policy="stop_experiment", game_id=None, phase_id=None,
                   input_tokens=None, output_tokens=None, estimated_cost_usd=-(10**400), stop_reason=None)
    path = tmp_path / "experiment_events.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(envelope("budget_state")) + "\n", encoding="utf-8")
    report = ArtifactValidator().validate_experiment_dir(tmp_path)
    errors = [error for error in report.errors if error.category == "invalid_event_payload"]
    assert {error.line_no for error in errors} == {1, 2}
    assert "estimated_cost_usd" in " ".join(error.detail for error in errors)


@pytest.mark.parametrize("stop_reason", [None, "", []])
def test_stopped_terminal_requires_nonempty_reason(stop_reason):
    row = envelope("phase_completed", event_id="id", game_id="g", phase_id="game_end", payload={"result": {
        "result": "stopped", "partial": True, "stop_reason": stop_reason, "winners": [], "country_results": {},
    }})
    assert validate_core_event(row, "events.jsonl")


def test_orders_validated_variants_require_game_phase_context():
    aggregate = envelope("orders_validated", game_id="g", phase_id="p", submitted_count=1, validated_count=1, invalid_count=0)
    per_agent = envelope("orders_validated", game_id="g", phase_id="p", agent_id="a", country=None, invalid_reasons=[], output_artifact_path="x.json")
    for row in (aggregate, per_agent):
        assert validate_core_event(row, "experiment_events.jsonl") == []
        for field in ("game_id", "phase_id"):
            missing = dict(row)
            del missing[field]
            assert any(field + ": missing" in error for error in validate_core_event(missing, "experiment_events.jsonl"))
            wrong = {**row, field: []}
            assert any(field + ": invalid str" in error for error in validate_core_event(wrong, "experiment_events.jsonl"))


@pytest.mark.parametrize("event_type", ["budget_state", "budget_stop_triggered"])
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "estimated_cost_usd"])
def test_budget_experiment_rows_require_nullable_usage_fields(event_type, field):
    row = copy.deepcopy(next(row for row, _path in EXPERIMENT_SAMPLES if row["event_type"] == event_type))
    assert row[field] is None
    assert validate_core_event(row, "experiment_events.jsonl") == []
    missing = dict(row)
    del missing[field]
    assert any(field + ": missing" in error for error in validate_core_event(missing, "experiment_events.jsonl"))
    wrong = {**row, field: []}
    assert any(field + ": invalid" in error for error in validate_core_event(wrong, "experiment_events.jsonl"))


@pytest.mark.parametrize("event_type,field", [
    ("budget_state", "game_id"), ("budget_state", "phase_id"), ("budget_state", "stop_reason"),
    ("budget_stop_triggered", "game_id"), ("budget_stop_triggered", "phase_id"),
])
def test_legacy_budget_context_remains_optional(event_type, field):
    row = envelope(event_type, provider_calls=1, input_tokens=None, output_tokens=2,
                   estimated_cost_usd=None, stop_policy="stop_experiment",
                   **({"stop_reason": "max_provider_calls"} if event_type == "budget_stop_triggered" else {}))
    assert validate_core_event(row, "experiment_events.jsonl") == []
    assert validate_core_event({**row, field: None}, "experiment_events.jsonl") == []
    assert any(field + ": invalid nullable_str" in error for error in
               validate_core_event({**row, field: []}, "experiment_events.jsonl"))


@pytest.mark.parametrize("method", ["negotiate_tick", "submit_orders", "reflect", "game_end_cleanup"])
def test_prompt_check_accepts_producer_methods(method):
    row = envelope("prompt_check", game_id="g", phase_id="p", agent_id="a", runtime_kind="llm",
                   method=method, tick=None, prompt_chars=1, prompt_hash="sha256:x",
                   estimated_input_tokens=None, checks={}, prompt_assets={}, prompt_profile="default",
                   output_artifact_path="x.json")
    assert validate_core_event(row, "experiment_events.jsonl") == []


@pytest.mark.parametrize("field,value", [("method", "not_a_method"), ("runtime_kind", "other")])
def test_prompt_check_rejects_unsupported_enums(field, value):
    row = envelope("prompt_check", game_id="g", phase_id="p", agent_id="a", runtime_kind="llm",
                   method="reflect", tick=None, prompt_chars=1, prompt_hash="sha256:x",
                   estimated_input_tokens=None, checks={}, prompt_assets={}, prompt_profile="default",
                   output_artifact_path="x.json")
    assert any(f"{field}: unsupported value" in error for error in
               validate_core_event({**row, field: value}, "experiment_events.jsonl"))


@pytest.mark.parametrize("field", ["total_input_tokens", "total_output_tokens", "total_estimated_cost_usd"])
def test_budget_usage_update_requires_nullable_totals(field):
    row = copy.deepcopy(next(
        sample for stream, sample, _path in CASES
        if stream == "budget_events.jsonl" and sample["event_type"] == "budget_usage_update"
    ))
    totals = row["payload"]["totals"]
    assert totals[field] is None
    missing = copy.deepcopy(row)
    del missing["payload"]["totals"][field]
    assert any("payload/totals/" + field + ": missing" in error for error in validate_core_event(missing, "budget_events.jsonl"))
    wrong = copy.deepcopy(row)
    wrong["payload"]["totals"][field] = []
    assert any("payload/totals/" + field + ": invalid" in error for error in validate_core_event(wrong, "budget_events.jsonl"))


def test_terminal_map_diagnostics_do_not_echo_untrusted_country_keys(tmp_path):
    row = envelope("phase_completed", event_id="id", game_id="g", phase_id="game_end", payload={"result": {
        "result": "completed", "winners": [], "country_results": {
            "SENSITIVE_TEST_KEY": {"agent_id": "a", "owned_centers": "SENSITIVE_TEST_VALUE"},
        },
    }})
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = ArtifactValidator().validate_experiment_dir(tmp_path)
    details = " ".join(error.detail for error in report.errors)
    assert "/country_results/entry[0]/owned_centers" in details
    assert "SENSITIVE_TEST_KEY" not in details and "SENSITIVE_TEST_VALUE" not in details


def test_unknown_events_are_forward_compatible_but_versions_are_not(tmp_path):
    row = envelope("future_event", payload={"new": "value"})
    assert not validate_core_event(row, "budget_events.jsonl")
    row["event_contract_version"] = "future_v2"
    assert validate_core_event(row, "budget_events.jsonl")
    row["schema_version"] = "2.0"
    (tmp_path / "budget_events.jsonl").write_text(json.dumps(row) + "\n")
    assert "schema_version_mismatch" in {e.category for e in ArtifactValidator().validate_experiment_dir(tmp_path).errors}


@pytest.mark.parametrize("limit", [0, 3, 1000])
def test_generated_game_provider_and_budget_streams_validate(tmp_path, limit):
    config = population_config(tmp_path, budget={"max_provider_calls": limit, "stop_policy": "stop_experiment"})
    result = PopulationExperimentRunner(config=config, base_dir=tmp_path, agents_root=tmp_path / "agents").run()
    streams = tmp_path / "streams"
    names = set()
    for path in result.experiment_root.rglob("*.jsonl"):
        if path.name in STREAMS:
            names.add(path.name)
            destination = streams / path.relative_to(result.experiment_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    assert names == STREAMS
    report = ArtifactValidator().validate_experiment_dir(streams)
    assert report.ok, [str(error) for error in report.errors]
    observed = {
        path.name: {
            json.loads(line).get("event_type") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        }
        for path in streams.rglob("*.jsonl")
    }
    assert {"game_initialized", "phase_started", "phase_completed", "snapshot_committed"} <= observed["events.jsonl"]
    assert {"experiment_started", "game_started", "phase_resolved", "orders_generated", "message_committed"} <= observed["experiment_events.jsonl"]
    assert observed["budget_events.jsonl"]
    assert {"budget_check", "budget_exceeded", "budget_usage_update", "provider_error",
            "provider_reservation", "provider_reservation_uncertain"} >= observed["budget_events.jsonl"]
    attempt_kinds = {
        json.loads(line).get("kind") for path in streams.rglob("provider_attempts.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    }
    assert attempt_kinds and attempt_kinds <= {"request", "blocked", "lifecycle", "eliminated_skip"}
