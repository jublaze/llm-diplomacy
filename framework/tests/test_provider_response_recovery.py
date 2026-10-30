import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
from llm_diplomacy.runner.recovery import ExperimentRecovery

spec = importlib.util.spec_from_file_location("recover_provider_responses", Path(__file__).parents[1] / "scripts/recover_provider_responses.py")
recover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recover)


def test_repeat_original_recovery_binds_actual_correction_ancestry(tmp_path):
    recovery=ExperimentRecovery(tmp_path,identity={'test':'repeat-original'},resume=False,repeat_received_invalid=True)
    recovery.prepare();decision='g/p/o/france/a/game_end_cleanup'
    recovery.record_response(decision_id=decision,prompt_hash='base',attempt_id='parent',provider={},
        response={'content':'bad'},usage=None,error_category=None,outcome='received')
    recovery.record_validation(decision_id=decision,prompt_hash='base',attempt_id='parent',valid=False,issue='bad schema')
    recovery.prepare_repeat_correction(decision_id=decision,parent_prompt_hash='base',
        feedback={'rejected_answer':'bad','validator_issue':'bad schema'},correction_prompt_hash='corrective')
    rows=recovery._journal_rows();originals=[r for r in rows if r.get('kind')=='attempt']
    observed=[{'decision_id':decision,'prompt_hash':'corrective','attempt_id':'child'}]
    recover.validate_repeat_originals(recovery,rows,observed,originals)
    with pytest.raises(ValueError,match='prompt identity'):
        recover.validate_repeat_originals(recovery,rows,[{**observed[0],'prompt_hash':'wrong'}],originals)
    recovery.record_validation(decision_id=decision,prompt_hash='base',attempt_id='parent',valid=True)
    with pytest.raises(ValueError,match='accepted or unrelated'):
        recover.validate_repeat_originals(recovery,recovery._journal_rows(),observed,originals)


def test_terminal_accounting_verification_is_read_only_and_does_not_authorize_repair(tmp_path):
    config={'max_provider_calls':5,'max_input_tokens':10000,'max_output_tokens':10000,'max_estimated_cost_usd':1.0}
    runner=SimpleNamespace(config=SimpleNamespace(schema_version='1.0',experiment_id='terminal',budget=config))
    recovery=ExperimentRecovery(tmp_path,identity={'test':'terminal-audit'},resume=False)
    recovery.prepare();recovery.checkpoint(cursor={'unit':'population_reports_completed'},state={},applied=['population_reports'])
    BudgetController(schema_version='1.0',experiment_id='terminal',artifact_root=tmp_path/'metrics/budget',config=BudgetConfig(**config))
    (tmp_path/'metrics/provider_attempts.jsonl').write_text('')
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert recover.reconcile_accounting(runner,recovery,dry_run=True)['dry_run'] is True
    assert {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before
    with pytest.raises(ValueError,match='halted identity'):recover.reconcile_accounting(runner,recovery)


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "experiments/case"
    r = ExperimentRecovery(root, identity={"code": "same"}, resume=False)
    r.prepare()
    runtime = r.working_root / "experiments/case/games/game_001/agents/actor/runtime/phase_inputs"
    runtime.mkdir(parents=True)
    context = {"schema_version": "1.0", "experiment_id": "case", "game_id": "game_001", "phase_id": "1901_winter_negotiation",
               "agent_id": "actor", "country_assignment": "austria", "visible_state": {"year": 1901, "season": "winter", "phase": "adjustment"}}
    recover.atomic_json(runtime / "1901_winter_negotiation_negotiate_tick.json", {"context": context, "method": "negotiate_tick"})
    r.save_initial_snapshot()
    budget_cfg = {"max_provider_calls": 5, "max_input_tokens": 10000, "max_output_tokens": 10000, "max_estimated_cost_usd": 1.0}
    cfg = SimpleNamespace(schema_version="1.0", experiment_id="case", budget=budget_cfg,
                          players=[SimpleNamespace(agent_id="actor")])
    runner = SimpleNamespace(config=cfg, _player_provider=lambda p: {"model_profile": "openai_gpt_6_luna"})
    budget = BudgetController(schema_version="1.0", experiment_id="case", artifact_root=root / "metrics/budget", config=BudgetConfig(**budget_cfg))
    (root / "metrics/provider_attempts.jsonl").write_text("".join(json.dumps({"kind":"request","error_type":"TimeoutError","game_id":"game_001","phase_id":"1901_winter_negotiation","agent_id":"actor","method":"negotiate_tick"})+"\n" for _ in range(3)), encoding="utf-8")
    prompt = "same exact prompt"
    prompt_hash = recover.hashlib.sha256(prompt.encode()).hexdigest()
    decision = "game_001/1901_winter_negotiation/tick-3/austria/actor/negotiate_tick"
    payload = {"private_messages": [], "global_messages": [], "relationship_updates": [], "withheld_information": []}
    entries = []
    # Space recorded prepared times synthetically to distinguish reservation ownership.
    for i in range(3):
        attempt = f"attempt-{i}"
        r.record_prepared(decision_id=decision, prompt_hash=prompt_hash, attempt_id=attempt, provider={"requested_model": "gpt-6-luna"})
        budget.reserve_before_call(reservation_id=f"reserve-{i}", game_id="game_001", phase_id="1901_winter_negotiation",
                                  agent_id="actor", method="negotiate_tick", input_tokens=100, output_tokens=100, cost_usd=.1)
        r.record_dispatch(decision_id=decision, prompt_hash=prompt_hash, attempt_id=attempt, provider={})
        r.record_response(decision_id=decision, prompt_hash=prompt_hash, attempt_id=attempt, provider={}, response=None,
                          usage=None, error_category="provider_timeout", outcome="unknown")
        budget.settle_reservation(reservation_id=f"reserve-{i}", usage=None, uncertain=True)
        now = int(datetime.now(timezone.utc).timestamp())
        response = {"id": f"resp_{i}", "status": "completed", "model": "gpt-6-luna", "created_at": now, "completed_at": now + 1600,
                    "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "output_tokens_details": {"reasoning_tokens": 5}},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]}]}
        response_path, input_path = tmp_path / f"response-{i}.json", tmp_path / f"input-{i}.json"
        recover.atomic_json(response_path, response)
        recover.atomic_json(input_path, {"data": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}], "has_more": False})
        entries.append({"attempt_id": attempt, "response_id": response["id"], "response_path": str(response_path), "input_path": str(input_path)})
    # Real requests are sequential; advance each original request interval by10seconds.
    rows = r._journal_rows()
    events_path = root / "metrics/budget/budget_events.jsonl"
    events = recover.json_lines(events_path)
    from datetime import timedelta
    for i in range(3):
        for row in rows:
            if row.get("attempt_id") == f"attempt-{i}" and row.get("started_at"):
                row["started_at"] = (datetime.fromisoformat(row["started_at"]) + timedelta(seconds=i * 10)).isoformat()
        for event in events:
            if event.get("payload", {}).get("reservation_id") == f"reserve-{i}":
                event["timestamp"] = (datetime.fromisoformat(event["timestamp"]) + timedelta(seconds=i * 10)).isoformat()
        response = recover.read(entries[i]["response_path"])
        response["created_at"] += i * 10
        response["completed_at"] += i * 10
        recover.atomic_json(Path(entries[i]["response_path"]), response)
    # Rebuild the synthetic chain through the production append primitive.
    r.journal_path.unlink()
    r.journal_head_path.unlink()
    for row in rows:
        r._append({k: v for k, v in row.items() if k not in {"sha256", "previous_hash", "sequence"}})
    events_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    r.halt(reason="provider_timeout", decision_id=decision)
    return runner, r, {"responses": entries}


def test_import_reconciles_all_completed_attempts_and_reuses_earliest(case):
    runner, r, manifest = case
    plan = recover.build_plan(runner, r, manifest)
    ledger = (r.root / "metrics/provider_attempts.jsonl").read_bytes()
    receipt = recover.apply_plan(runner, r, plan)
    state = recover.read(r.root / "metrics/budget/budget_state.json")
    assert state["total_provider_calls"] == 3
    assert state["known_input_tokens"] == 30 and state["known_output_tokens"] == 60
    assert state["pending_reservations"] == {}
    assert state["total_input_tokens"] == 30 and state["total_output_tokens"] == 60
    assert state["total_estimated_cost_usd"] == state["known_estimated_cost_usd"]
    assert receipt["actual_requests_added"] == 0
    assert [o["provider_elapsed_seconds"] for o in receipt["observations"]] == [1600] * 3
    assert r.cached_response(plan["observations"][0]["decision_id"], plan["observations"][0]["prompt_hash"])["attempt_id"] == "attempt-0"
    assert r.in_flight() == []
    assert r.attempt_count(plan["observations"][0]["decision_id"]) == 3
    assert (r.root / "metrics/provider_attempts.jsonl").read_bytes() == ledger
    before = r.journal_path.read_bytes(), (r.root / "metrics/budget/budget_state.json").read_bytes()
    assert recover.apply_plan(runner, r, recover.build_plan(runner, r, manifest)) == receipt
    assert before == (r.journal_path.read_bytes(), (r.root / "metrics/budget/budget_state.json").read_bytes())


@pytest.fixture
def interrupted_import(case):
    runner, recovery, manifest = case
    rows = [r for r in recovery._journal_rows() if r.get("attempt_id") not in {"attempt-1", "attempt-2"}]
    for row in rows:
        if row.get("kind") == "attempt":
            row["error_category"] = "process_interrupted"
    recovery.journal_path.unlink()
    recovery.journal_head_path.unlink()
    for row in rows:
        recovery._append({k: v for k, v in row.items() if k not in {"sha256", "previous_hash", "sequence"}})
    recovery.checkpoint(cursor={"unit": "phase_started", "game_id": "game_001", "phase_id": "1901_winter_negotiation"},
                        state={}, applied=[])
    recovery.halt(reason="process_interrupted")
    budget_path = recovery.root / "metrics/budget/budget_state.json"
    state = recover.read(budget_path)
    state.update(total_provider_calls=1, pending_cost_usd=.1, pending_input_tokens=100,
                 pending_output_tokens=100, pending_reservations={"reserve-0": state["pending_reservations"]["reserve-0"]},
                 budget_admission_estimated_cost_usd=.1, budget_admission_estimated_input_tokens=100,
                 budget_admission_estimated_output_tokens=100)
    recover.atomic_json(budget_path, state)
    manifest = {"responses": manifest["responses"][:1]}
    ledger = recovery.root / "metrics/provider_attempts.jsonl"
    ledger.write_bytes(b"")
    plan = recover.build_plan(runner, recovery, manifest)
    with pytest.raises(ValueError, match="matched uniquely"):
        recover.apply_plan(runner, recovery, plan)
    return runner, recovery, manifest, plan


def test_interrupted_import_dry_run_and_idempotent_completion(interrupted_import):
    runner, recovery, manifest, plan = interrupted_import
    before = {p: p.read_bytes() for p in recovery.root.rglob("*") if p.is_file()}
    report = recover.complete_interrupted_import(runner, recovery, plan)
    assert report["new_provider_calls"] == 0 and not report["applied"]
    assert before == {p: p.read_bytes() for p in recovery.root.rglob("*") if p.is_file()}
    with patch.object(recover.ResponsesAPIProvider, "generate", side_effect=AssertionError("no generation")):
        receipt = recover.complete_interrupted_import(runner, recovery, recover.build_plan(runner, recovery, manifest), apply=True)
    requests = recover.json_lines(recovery.root / "metrics/provider_attempts.jsonl")
    assert len(requests) == 1 and requests[0]["attempt_id"] == "attempt-0"
    assert requests[0]["recovered_response_id"] == "resp_0"
    assert requests[0]["provider_elapsed_seconds"] == 1600
    state = recover.read(recovery.root / "metrics/budget/budget_state.json")
    assert state["total_provider_calls"] == 1 and state["pending_reservations"] == {}
    assert state["known_input_tokens"] == 10 and state["known_output_tokens"] == 20
    after = {p: p.read_bytes() for p in recovery.root.rglob("*") if p.is_file()}
    assert recover.complete_interrupted_import(runner, recovery, plan, apply=True) == receipt
    assert after == {p: p.read_bytes() for p in recovery.root.rglob("*") if p.is_file()}


def test_interrupted_import_restart_after_append(interrupted_import):
    runner, recovery, _, plan = interrupted_import
    reconcile = recover.reconcile_accounting
    def crash(runner, recovery, **kwargs):
        if not kwargs.get("dry_run"):
            raise RuntimeError("simulated restart after append")
        return reconcile(runner, recovery, **kwargs)
    with patch.object(recover, "reconcile_accounting", side_effect=crash):
        with pytest.raises(RuntimeError, match="simulated restart"):
            recover.complete_interrupted_import(runner, recovery, plan, apply=True)
    receipt = recover.complete_interrupted_import(runner, recovery, plan, apply=True)
    assert receipt["ledger_already_completed"]
    assert len(recover.json_lines(recovery.root / "metrics/provider_attempts.jsonl")) == 1
    assert recover.read(recovery.root / "metrics/budget/budget_state.json")["total_provider_calls"] == 1


def test_interrupted_import_preserves_prior_ledger_and_reviewed_unknown_reserves(case):
    runner, recovery, manifest = case
    rows = recovery._journal_rows()
    for row in rows:
        if row.get("attempt_id") == "attempt-2":
            row["decision_id"] = row["decision_id"].replace("tick-3", "tick-2")
            if row.get("kind") == "attempt":
                row["error_category"] = "process_interrupted"
    recovery.journal_path.unlink()
    recovery.journal_head_path.unlink()
    for row in rows:
        recovery._append({k: v for k, v in row.items() if k not in {"sha256", "previous_hash", "sequence"}})
    recovery.checkpoint(cursor={"unit": "phase_started"}, state={}, applied=[])
    for attempt in ("attempt-0", "attempt-1"):
        original = next(row for row in rows if row.get("attempt_id") == attempt)
        recovery._append({"kind": "unknown_resolved", "decision_id": original["decision_id"],
                          "attempt_id": attempt, "prompt_hash": original["prompt_hash"], "choice": "retry_after_review"})
    recovery.halt(reason="process_interrupted")
    ledger = recovery.root / "metrics/provider_attempts.jsonl"
    prefix = b"".join(ledger.read_bytes().splitlines(keepends=True)[:2])
    ledger.write_bytes(prefix)
    plan = recover.build_plan(runner, recovery, {"responses": manifest["responses"][2:]})
    with pytest.raises(ValueError, match="matched uniquely"):
        recover.apply_plan(runner, recovery, plan)
    budget_path = recovery.root / "metrics/budget/budget_state.json"
    prior = recover.read(budget_path)
    receipt = recover.complete_interrupted_import(runner, recovery, plan, apply=True)
    after = recover.read(budget_path)
    assert ledger.read_bytes().startswith(prefix) and len(recover.json_lines(ledger)) == 3
    assert after == prior
    assert set(after["pending_reservations"]) == {"reserve-0", "reserve-1"}
    assert receipt["reconciled_totals"]["retained_uncertain_requests"] == 2


@pytest.mark.parametrize("history", ["accepted_retry", "no_resume", "rejected_retry"])
def test_interrupted_import_preserves_only_handled_http_exposure(case, history):
    runner, recovery, manifest = case
    rows = recovery._journal_rows()
    for row in rows:
        if row.get("attempt_id") == "attempt-0" and row.get("kind") == "attempt":
            row.update(outcome="known_failed", error_category="provider_transient_http")
        if row.get("attempt_id") == "attempt-2":
            row["decision_id"] = row["decision_id"].replace("tick-3", "tick-2")
            if row.get("kind") == "attempt":
                row["error_category"] = "process_interrupted"
    recovery.journal_path.unlink()
    recovery.journal_head_path.unlink()
    for row in rows:
        recovery._append({k: v for k, v in row.items() if k not in {"sha256", "previous_hash", "sequence"}})
    recovery.checkpoint(cursor={"unit": "phase_started"}, state={}, applied=[])
    recovery.halt(reason="process_interrupted")
    if history != "no_resume":
        recovery._append({"kind": "resume_session", "session_id": "reviewed-retry"})
    ledger = recovery.root / "metrics/provider_attempts.jsonl"
    requests = recover.json_lines(ledger)[:2]
    requests[0]["error_type"] = "ProviderHTTPError"
    prefix = "".join(json.dumps(r) + "\n" for r in requests).encode()
    ledger.write_bytes(prefix)
    earlier = recover.build_plan(runner, recovery, {"responses": manifest["responses"][1:2]})
    with pytest.raises(ValueError, match="matched uniquely"):
        recover.apply_plan(runner, recovery, earlier)
    recovery.record_validation(decision_id=earlier["observations"][0]["decision_id"],
                               prompt_hash=earlier["observations"][0]["prompt_hash"], attempt_id="attempt-1",
                               valid=history != "rejected_retry")
    plan = recover.build_plan(runner, recovery, {"responses": manifest["responses"][2:]})
    with pytest.raises(ValueError, match="matched uniquely"):
        recover.apply_plan(runner, recovery, plan)
    budget_path = recovery.root / "metrics/budget/budget_state.json"
    before = budget_path.read_bytes()
    if history == "accepted_retry":
        receipt = recover.complete_interrupted_import(runner, recovery, plan, apply=True)
        assert receipt["reconciled_totals"]["retained_uncertain_requests"] == 1
        assert ledger.read_bytes().startswith(prefix)
        assert budget_path.read_bytes() == before
    else:
        with pytest.raises(ValueError, match="unknown or ambiguous"):
            recover.complete_interrupted_import(runner, recovery, plan, apply=True)
        assert ledger.read_bytes() == prefix and budget_path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["ledger", "call_count", "usage", "reserve", "stop", "input", "response", "intent"])
def test_interrupted_import_refuses_mismatch_without_mutation(interrupted_import, mutation):
    runner, recovery, manifest, plan = interrupted_import
    budget_path = recovery.root / "metrics/budget/budget_state.json"
    state = recover.read(budget_path)
    if mutation == "ledger":
        (recovery.root / "metrics/provider_attempts.jsonl").write_text('{"kind":"other"}\n')
    elif mutation == "call_count":
        state["total_provider_calls"] += 1
    elif mutation == "usage":
        state["known_input_tokens"] += 1
    elif mutation == "reserve":
        state["pending_reservations"]["unexpected"] = {"status": "uncertain", "cost_usd": .1}
    elif mutation == "stop":
        state.update(stop_experiment=True, stop_reason="max_provider_calls")
    elif mutation in {"input", "response"}:
        key = mutation + "_path"
        value = recover.read(manifest["responses"][0][key])
        if mutation == "input":
            value["data"][0]["content"][0]["text"] += "changed"
        else:
            value["usage"]["input_tokens"] += 1
        recover.atomic_json(Path(manifest["responses"][0][key]), value)
    elif mutation == "intent":
        path = recovery.path / "provider_imports" / recover.canonical_hash(plan) / "interrupted_ledger_intent.json"
        recover.atomic_json(path, {"different": True})
    recover.atomic_json(budget_path, state)
    before = {p: p.read_bytes() for p in recovery.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        verified = recover.build_plan(runner, recovery, manifest)
        recover.complete_interrupted_import(runner, recovery, verified, apply=True)
    assert before == {p: p.read_bytes() for p in recovery.root.rglob("*") if p.is_file()}


def test_corrective_import_uses_actual_prompt_hash_and_is_idempotent(case):
    runner, r, manifest = case
    original_rows = r._journal_rows()
    for row in original_rows:
        if isinstance(row.get("decision_id"), str):
            row["decision_id"] = row["decision_id"].replace("negotiate_tick", "submit_orders")
    budget_events = r.root / "metrics/budget/budget_events.jsonl"
    reservation_rows = recover.json_lines(budget_events)
    for row in reservation_rows:
        if row.get("event_type") == "provider_reservation":
            row["payload"]["method"] = "submit_orders"
    budget_events.write_text("".join(json.dumps(row) + "\n" for row in reservation_rows), encoding="utf-8")
    provider_attempts = r.root / "metrics/provider_attempts.jsonl"
    request_rows = recover.json_lines(provider_attempts)
    for row in request_rows:
        if row.get("method") == "negotiate_tick":
            row["method"] = "submit_orders"
    provider_attempts.write_text("".join(json.dumps(row) + "\n" for row in request_rows), encoding="utf-8")
    runtime_root = r.working_root / "experiments/case/games/game_001/agents/actor/runtime/phase_inputs"
    saved_context = recover.read(runtime_root / "1901_winter_negotiation_negotiate_tick.json")
    saved_context["method"] = "submit_orders"
    recover.atomic_json(runtime_root / "1901_winter_negotiation_submit_orders.json", saved_context)
    for entry in manifest["responses"]:
        response = recover.read(entry["response_path"])
        response["output"][0]["content"][0]["text"] = json.dumps({
            "orders": [], "order_rationale": {
                "goal_supported": "", "why_this_advances_strategy": ""
            }
        })
        recover.atomic_json(Path(entry["response_path"]), response)
    first_request = next(row for row in original_rows if row.get("kind") == "dispatch_prepared")
    decision = first_request["decision_id"]
    correction_hash = first_request["prompt_hash"]
    base_hash = recover.hashlib.sha256(b"rejected parent prompt").hexdigest()
    parent_response = {"content": '{"private_messages":[],"global_messages":[],"relationship_updates":[],"withheld_information":[]}',
                       "model": "gpt-6-luna", "raw": {}}
    r.journal_path.unlink()
    r.journal_head_path.unlink()
    r.record_response(decision_id=decision, prompt_hash=base_hash, attempt_id="parent-attempt",
                      provider={}, response=parent_response, usage=None,
                      error_category=None, outcome="received")
    r.record_validation(decision_id=decision, prompt_hash=base_hash, attempt_id="parent-attempt",
                        valid=False, issue="rejected parent response")
    r.prepare_order_correction(decision_id=decision, base_prompt_hash=base_hash,
                               feedback={"rejected_answer": parent_response["content"],
                                         "engine_errors": [{"reason": "invalid_output"}]},
                               correction_prompt_hash=correction_hash)
    for row in original_rows:
        if row.get("kind") == "halt":
            continue
        r._append({key: value for key, value in row.items()
                   if key not in {"sequence", "previous_hash", "sha256"}})
    r.checkpoint(cursor={"unit": "phase_started", "game_id": "game_001", "phase_id": "1901_winter_negotiation"},
                 state={}, applied=[])
    r.halt(reason="provider_timeout", decision_id=decision)

    plan = recover.build_plan(runner, r, manifest)
    assert all(item["prompt_hash"] == correction_hash for item in plan["observations"])
    ledger_before = (r.root / "metrics/provider_attempts.jsonl").read_bytes()
    receipt = recover.apply_plan(runner, r, plan)
    assert receipt["actual_requests_added"] == 0
    assert (r.root / "metrics/provider_attempts.jsonl").read_bytes() == ledger_before
    assert recover.build_plan(runner, r, manifest) == plan
    assert recover.apply_plan(runner, r, plan) == receipt

    bad_manifest = {"responses": [dict(entry) for entry in manifest["responses"]]}
    first_input = Path(bad_manifest["responses"][0]["input_path"])
    saved = recover.read(first_input)
    changed = json.loads(json.dumps(saved))
    changed["data"][0]["content"][0]["text"] += " altered"
    recover.atomic_json(first_input, changed)
    with pytest.raises(ValueError, match="prompt hash mismatch"):
        recover.build_plan(runner, r, bad_manifest)
    recover.atomic_json(first_input, saved)


@pytest.mark.parametrize("method", ["submit_orders", "reflect", "negotiate_tick"])
def test_corrective_import_preserves_reviewed_unknown_reserve(case, method):
    runner, r, manifest = case
    rows = r._journal_rows()
    for row in rows:
        if isinstance(row.get("decision_id"), str):
            row["decision_id"] = row["decision_id"].replace("negotiate_tick", method)
    decision = next(row["decision_id"] for row in rows if row.get("kind") == "dispatch")
    base_hash = next(row["prompt_hash"] for row in rows if row.get("kind") == "dispatch")
    correction_hash = recover.hashlib.sha256(b"one corrective prompt").hexdigest()
    runtime_root = r.working_root / "experiments/case/games/game_001/agents/actor/runtime/phase_inputs"
    saved = recover.read(runtime_root / "1901_winter_negotiation_negotiate_tick.json")
    saved["method"] = method
    recover.atomic_json(runtime_root / f"1901_winter_negotiation_{method}.json", saved)
    reflection = {
        "schema_version": "reflection_compact_v3_campaign_memory",
        "phase_summary": "The public order record shows the army held.",
        "strategic_position": "The northern position remains stable.",
        "objective_update": {"current_objective": "Verify support before moving.", "why_it_matters": "Avoids unsupported risk."},
        "relationship_updates": [], "action_lessons": [], "memory_candidates": [],
        "belief_summary": "The public board shows no change.",
        "outcome_review": "The published order record and adjudication are the evidence for the next action.",
        "campaign_plan": "Maintain a conditional route and revise it against public evidence.",
        "commitment_memory": "No decision-relevant commitments yet.",
        "winter_assessment": "No winter build or disband choice is pending.",
        "memory_update": "The board shows the army held; verify support before moving.",
    }
    for entry in manifest["responses"]:
        value = recover.read(entry["response_path"])
        if method == "reflect":
            value["output"][0]["content"][0]["text"] = json.dumps(reflection)
        elif method == "submit_orders":
            value["output"][0]["content"][0]["text"] = json.dumps({"orders": [], "order_rationale": {
                "goal_supported": "adjustments", "why_this_advances_strategy": "No adjustment available."}})
        recover.atomic_json(Path(entry["response_path"]), value)
    provider_ledger = r.root / "metrics/provider_attempts.jsonl"
    ledger_rows = recover.json_lines(provider_ledger)
    for value in ledger_rows:
        if value.get("method") == "negotiate_tick":
            value["method"] = method
    ledger_rows[1].update(error_type=None, input_tokens=10, output_tokens=20, estimated_cost_usd=.000011)
    provider_ledger.write_text("".join(json.dumps(value) + "\n" for value in ledger_rows), encoding="utf-8")
    for path in (r.root / "metrics/budget/budget_events.jsonl",):
        values = recover.json_lines(path)
        for value in values:
            if value.get("method") == "negotiate_tick":
                value["method"] = method
            payload = value.get("payload")
            if isinstance(payload, dict) and payload.get("method") == "negotiate_tick":
                payload["method"] = method
        path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
    state_path = r.root / "metrics/budget/budget_state.json"
    state = recover.read(state_path)
    for reservation in state["pending_reservations"].values():
        reservation["method"] = method
    recover.atomic_json(state_path, state)

    parent_content = recover.ResponsesAPIProvider._extract_text(
        recover.read(manifest["responses"][1]["response_path"]))
    if method == "reflect":
        parent_content = json.dumps({**reflection, "schema_version": "reflection_campaign_v1"})
    parent_response = {"content": parent_content,
                       "model": "gpt-6-luna", "raw": {}}
    r.journal_path.unlink()
    r.journal_head_path.unlink()
    r._append({key: value for key, value in rows[0].items() if key not in {"sequence", "previous_hash", "sha256"}})
    for attempt_id in ("attempt-0", "attempt-1"):
        for row in rows:
            if row.get("attempt_id") != attempt_id:
                continue
            item = {key: value for key, value in row.items() if key not in {"sequence", "previous_hash", "sha256"}}
            if attempt_id == "attempt-1" and item.get("kind") == "attempt":
                item.update(outcome="received", response=parent_response, error_category=None,
                            usage={"input_tokens": 10, "output_tokens": 20, "prompt_tokens": 10,
                                   "completion_tokens": 20, "total_tokens": 30, "estimated_cost_usd": .000011})
            r._append(item)
        if attempt_id == "attempt-0":
            r.resolve_unknown(decision_id=decision, attempt_id=attempt_id, prompt_hash=base_hash, choice="retry_after_review")
    r.record_validation(decision_id=decision, prompt_hash=base_hash, attempt_id="attempt-1",
                        valid=False, issue="parent rejected")
    feedback = ({"method": "reflect", "validator_issue": "wrong reflection schema",
                 "rejected_answer": parent_content, "required_schema": "reflection_compact_v3_campaign_memory",
                 "required_schema_example": reflection} if method == "reflect" else
                ({"method": "negotiate_tick", "validator_issue": "private_messages.recipient must be non-empty string",
                  "rejected_answer": parent_content, "required_fields": ["private_messages", "global_messages",
                    "relationship_updates", "withheld_information"]} if method == "negotiate_tick" else
                 {"engine_errors": [{"reason": "invalid_order"}]}))
    r.prepare_order_correction(decision_id=decision, base_prompt_hash=base_hash,
                               feedback=feedback, correction_prompt_hash=correction_hash, method=method)
    for row in rows:
        if row.get("attempt_id") == "attempt-2":
            r._append({**{key: value for key, value in row.items()
                          if key not in {"sequence", "previous_hash", "sha256"}}, "prompt_hash": correction_hash})
    r.checkpoint(cursor={"unit": "phase_started"}, state={}, applied=[])
    r.halt(reason="provider_timeout", decision_id=decision)

    events_path = r.root / "metrics/budget/budget_events.jsonl"
    events = recover.json_lines(events_path)
    budget = BudgetController(schema_version="1.0", experiment_id="case",
        artifact_root=r.root / "metrics/budget", config=BudgetConfig(**runner.config.budget))
    budget.settle_reservation(reservation_id="reserve-1", usage={"input_tokens": 10, "output_tokens": 20,
        "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "estimated_cost_usd": .000011})

    correction_entry = dict(manifest["responses"][2])
    correction_input = recover.read(correction_entry["input_path"])
    correction_input["data"][0]["content"][0]["text"] = "one corrective prompt"
    recover.atomic_json(Path(correction_entry["input_path"]), correction_input)
    correction_response = recover.read(correction_entry["response_path"])
    dispatch_time = next(row["started_at"] for row in rows
                         if row.get("kind") == "dispatch" and row.get("attempt_id") == "attempt-2")
    now = int(recover.stamp(dispatch_time))
    correction_response.update(created_at=now, completed_at=now + 1)
    recover.atomic_json(Path(correction_entry["response_path"]), correction_response)
    correction_entry["attempt_id"] = "attempt-2"
    correction_entry["response_id"] = correction_response["id"]
    correction_manifest = {"responses": [correction_entry]}
    prepared2 = next(row for row in r._journal_rows() if row.get("kind") == "dispatch_prepared"
                     and row.get("attempt_id") == "attempt-2")
    matching_reservations = [event["payload"]["reservation_id"] for event in recover.json_lines(events_path)
        if event.get("event_type") == "provider_reservation"
        and 0 <= recover.stamp(event["timestamp"]) - recover.stamp(prepared2["started_at"]) <= 5]
    assert matching_reservations == ["reserve-2"]
    matching_event = next(event for event in recover.json_lines(events_path)
                          if event.get("payload", {}).get("reservation_id") == "reserve-2")
    assert {key: matching_event["payload"].get(key) for key in
            ("agent_id", "game_id", "phase_id", "method")} == {
                "agent_id": "actor", "game_id": "game_001", "phase_id": "1901_winter_negotiation",
                "method": method}
    plan = recover.build_plan(runner, r, correction_manifest)
    saved_correction_input = recover.read(correction_input_path := Path(correction_entry["input_path"]))
    tampered_input = json.loads(json.dumps(saved_correction_input))
    tampered_input["data"][0]["content"][0]["text"] += " changed"
    recover.atomic_json(correction_input_path, tampered_input)
    with pytest.raises(ValueError, match="prompt hash mismatch"):
        recover.build_plan(runner, r, correction_manifest)
    recover.atomic_json(correction_input_path, saved_correction_input)
    ledger_before = (r.root / "metrics/provider_attempts.jsonl").read_bytes()
    receipt = recover.apply_plan(runner, r, plan)
    state_after = recover.read(state_path)
    assert receipt["actual_requests_added"] == 0
    assert state_after["total_provider_calls"] == 3
    assert state_after["total_estimated_cost_usd"] is None
    assert state_after["pending_reservations"]["reserve-0"]["status"] == "uncertain"
    assert "reserve-2" not in state_after["pending_reservations"]
    assert state_after["known_input_tokens"] == 20 and state_after["known_output_tokens"] == 40
    assert (r.root / "metrics/provider_attempts.jsonl").read_bytes() == ledger_before
    assert receipt["reconciled_totals"]["retained_uncertain_requests"] == 1
    if method == "reflect":
        assert any(row["attempt_id"] == "attempt-1" for row in r.status()["rejected_responses"])
        imported = plan["observations"][0]
        r.record_validation(decision_id=decision, prompt_hash=correction_hash,
                            attempt_id=imported["attempt_id"], valid=True)
        assert all(row["attempt_id"] != "attempt-1" for row in r.status()["rejected_responses"])
        validated_journal = r.journal_path.read_bytes()
        r.record_validation(decision_id=decision, prompt_hash=correction_hash,
                            attempt_id=imported["attempt_id"], valid=True)
        assert r.journal_path.read_bytes() == validated_journal
        r.record_validation(decision_id=decision, prompt_hash=base_hash, attempt_id="attempt-1",
                            valid=False, issue="later independent rejection")
        assert any(row["attempt_id"] == "attempt-1"
                   and row["issue"] == "later independent rejection"
                   for row in r.status()["rejected_responses"])
    before = r.journal_path.read_bytes(), state_path.read_bytes()
    assert recover.apply_plan(runner, r, recover.build_plan(runner, r, correction_manifest)) == receipt
    assert before == (r.journal_path.read_bytes(), state_path.read_bytes())
    stopped = recover.read(state_path)
    stopped.update(stop_experiment=True, stop_reason="operator_review")
    recover.atomic_json(state_path, stopped)
    with pytest.raises(ValueError, match="unrelated budget stop"):
        recover.reconcile_accounting(runner, r)
    assert recover.read(state_path) == stopped
    stopped.update(stop_experiment=False, stop_reason=None)
    recover.atomic_json(state_path, stopped)
    with patch.object(BudgetController, "_exceeded_reason", return_value="max_provider_calls"):
        with pytest.raises(ValueError, match="real budget limit"):
            recover.reconcile_accounting(runner, r)
    assert recover.read(state_path) == stopped


@pytest.mark.parametrize("field,value,error", [("status", "in_progress", "not completed"), ("model", "other", "model mismatch"),
    ("created_at", 1, "creation time"), ("completed_at", 1, "completion precedes"), ("error", {"code": "failed"}, "error or incomplete")])
def test_rejects_unusable_provider_records_without_mutation(case, field, value, error):
    runner, r, manifest = case
    p = manifest["responses"][0]["response_path"]
    record = recover.read(p); record[field] = value; recover.atomic_json(Path(p), record)
    before = r.journal_path.read_bytes()
    with pytest.raises(ValueError, match=error):
        recover.build_plan(runner, r, manifest)
    assert r.journal_path.read_bytes() == before


def test_rejects_input_tamper_and_missing_duplicate(case):
    runner, r, manifest = case
    with pytest.raises(ValueError, match="every unknown"):
        recover.build_plan(runner, r, {"responses": manifest["responses"][:1]})
    entry = manifest["responses"][0];value = recover.read(entry["input_path"])
    value["data"][0]["content"][0]["text"] += "tampered"
    recover.atomic_json(Path(entry["input_path"]), value)
    with pytest.raises(ValueError, match="prompt hash"):
        recover.build_plan(runner, r, manifest)


def test_crash_after_settlement_resumes_without_double_accounting(case):
    runner, r, manifest = case
    plan = recover.build_plan(runner, r, manifest)
    original = BudgetController.settle_reservation
    def interrupted(self, **kwargs):
        original(self, **kwargs)
        raise RuntimeError("simulated interruption after durable settlement")
    with patch.object(BudgetController, "settle_reservation", interrupted), pytest.raises(RuntimeError):
        recover.apply_plan(runner, r, plan)
    recover.apply_plan(runner, r, plan)
    state = recover.read(r.root / "metrics/budget/budget_state.json")
    assert state["known_input_tokens"] == 30 and state["total_provider_calls"] == 3


def test_crash_during_journal_import_finishes_without_duplicate_rows(case):
    runner, r, manifest = case
    plan = recover.build_plan(runner, r, manifest)
    original = r.record_response
    def interrupted(**kwargs):
        original(**kwargs)
        raise RuntimeError("simulated interruption after journal append")
    with patch.object(r, "record_response", interrupted), pytest.raises(RuntimeError):
        recover.apply_plan(runner, r, plan)
    recover.apply_plan(runner, r, recover.build_plan(runner, r, manifest))
    assert len([x for x in r._journal_rows() if x.get("kind") == "attempt" and x.get("outcome") == "received"]) == 3


def test_rejects_invalid_selected_output(case):
    runner, r, manifest = case
    entry = manifest["responses"][0];value = recover.read(entry["response_path"])
    value["output"][0]["content"][0]["text"] = '{"wrong":true}'
    recover.atomic_json(Path(entry["response_path"]), value)
    with pytest.raises(ValueError, match="invalid"):
        recover.build_plan(runner, r, manifest)


def test_rejects_negotiation_response_with_dropped_unknown_recipient(case):
    runner, r, manifest = case
    decision = "game_001/1901_winter_negotiation/tick-3/austria/actor/negotiate_tick"
    content = json.dumps({
        "private_messages": [{"recipient": "unknown-power", "body": "hello", "purpose": "offer"}],
        "global_messages": [], "relationship_updates": [], "withheld_information": [],
    })
    with pytest.raises(ValueError, match="normalization would drop"):
        recover.validate_selected(r.root, r, decision, content)


def test_rejects_legacy_negotiation_relationship_value_coercion(case):
    runner, r, manifest = case
    decision = "game_001/1901_winter_negotiation/tick-3/austria/actor/negotiate_tick"
    content = json.dumps({
        "messages": [],
        "relationship_analysis": [
            {"agent_id": "england", "current_relationship": "conditional",
             "next_strategic_use_of_relationship": "verify support"},
            {"agent_id": "france", "current_relationship": {"meaning": "LOST_FACT"},
             "next_strategic_use_of_relationship": "verify the border"},
        ],
    })
    with pytest.raises(ValueError, match="current_relationship must be a string"):
        recover.validate_selected(r.root, r, decision, content)


def test_rejects_changed_request_ledger_during_retry(case):
    runner, r, manifest = case
    plan = recover.build_plan(runner, r, manifest)
    with patch.object(BudgetController, "settle_reservation", side_effect=RuntimeError("interrupted")), pytest.raises(RuntimeError):
        recover.apply_plan(runner, r, plan)
    with (r.root / "metrics/provider_attempts.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"kind":"request"}\n')
    with pytest.raises(ValueError, match="ledger changed"):
        recover.apply_plan(runner, r, plan)


def test_fetch_only_issues_get_and_preserves_processing_observation(case, monkeypatch):
    runner, r, manifest = case
    cfg = recover.parse_llm_provider_config(runner._player_provider(runner.config.players[0]))
    monkeypatch.setenv(cfg.api_key_env_var, "offline-placeholder")
    class Handle:
        def __init__(self, value): self.value = value
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(self.value).encode()
    seen = []
    def get(request, **kwargs):
        assert request.get_method() == "GET" and request.data is None
        seen.append(request.full_url)
        return Handle({"data": [], "has_more": False} if request.full_url.endswith("input_items") else
                      {"id": "resp_0", "status": "in_progress"})
    before = r.journal_path.read_bytes(), (r.root / "metrics/budget/budget_state.json").read_bytes()
    with patch.object(recover.urllib.request, "urlopen", get):
        result = recover.fetch_records(runner, r, {"responses": manifest["responses"][:1]})
    assert len(seen) == 2 and recover.read(result["responses"][0]["response_path"])["status"] == "in_progress"
    assert before == (r.journal_path.read_bytes(), (r.root / "metrics/budget/budget_state.json").read_bytes())


def test_reflection_response_import_applies_fresh_schema_and_semantic_guards(case):
    runner, r, _ = case
    decision = "game_001/1901_winter_negotiation/once/austria/actor/reflect"
    phase_inputs = r.working_root / "experiments/case/games/game_001/agents/actor/runtime/phase_inputs"
    saved = recover.read(phase_inputs / "1901_winter_negotiation_negotiate_tick.json")
    saved["method"] = "reflect"
    recover.atomic_json(phase_inputs / "1901_winter_negotiation_reflect.json", saved)
    valid = {
        "schema_version": "reflection_compact_v3_campaign_memory",
        "phase_summary": "The public order record shows the army held.",
        "strategic_position": "The northern position remains stable.",
        "objective_update": {"current_objective": "Verify support before moving.", "why_it_matters": "Avoids unsupported risk."},
        "relationship_updates": [], "action_lessons": [], "memory_candidates": [],
        "belief_summary": "The public board shows no change.",
        "outcome_review": "The published order record and adjudication are the evidence for the next action.",
        "campaign_plan": "Maintain a conditional route and revise it against public evidence.",
        "commitment_memory": "No decision-relevant commitments yet.",
        "winter_assessment": "No winter build or disband choice is pending.",
        "memory_update": "The board shows the army held; verify support before moving.",
    }
    content = json.dumps(valid)
    assert recover.validate_selected(r.root, r, decision, content) is None
    invalid_schema = {**valid, "schema_version": "reflection_campaign_v1"}
    with pytest.raises(ValueError, match="must use schema_version"):
        recover.validate_selected(r.root, r, decision, json.dumps(invalid_schema))
    invalid_semantics = {**valid, "relationship_updates": [{"agent_id": "italy", "status": "trust",
        "evidence": "short", "value_to_my_objectives": "low", "risk_or_limit": "", "next_use": ""}]}
    with pytest.raises(ValueError, match="lacks strategic consequence detail"):
        recover.validate_selected(r.root, r, decision, json.dumps(invalid_semantics))


@pytest.mark.parametrize("problem", ["missing_request", "extra_request", "missing_received", "bad_usage", "ordering"])
def test_complete_accounting_proof_fails_closed(case, problem):
    runner, r, manifest = case
    plan = recover.build_plan(runner, r, manifest)
    recover.apply_plan(runner, r, plan)
    ledger = r.root / "metrics/provider_attempts.jsonl"
    rows = recover.json_lines(ledger)
    if problem == "missing_request": rows.pop()
    elif problem == "extra_request": rows.append(rows[-1])
    elif problem == "ordering": rows[0]["agent_id"] = "different"
    elif problem == "bad_usage":
        rows[0]["error_type"] = None
        rows[0]["input_tokens"] = True
    elif problem == "missing_received":
        rows[0]["error_type"] = "TimeoutError"
        # A different dispatch has no matching recovered response.
        r.record_prepared(decision_id="game_001/1901_winter_negotiation/tick-4/austria/actor/negotiate_tick",
                          prompt_hash="different", attempt_id="unknown-new", provider={})
        r.record_dispatch(decision_id="game_001/1901_winter_negotiation/tick-4/austria/actor/negotiate_tick",
                          prompt_hash="different", attempt_id="unknown-new", provider={})
        rows.append(rows[0])
    ledger.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    budget_path = r.root / "metrics/budget/budget_state.json"
    before = budget_path.read_bytes()
    with pytest.raises(ValueError): recover.reconcile_accounting(runner, r)
    assert budget_path.read_bytes() == before


def test_false_unknown_cost_block_is_skipped_without_dispatch_or_limit_change(case):
    runner, r, manifest = case
    plan = recover.build_plan(runner, r, manifest)
    recover.apply_plan(runner, r, plan)
    path = r.root / "metrics/budget/budget_state.json"
    state = recover.read(path)
    state.update(total_estimated_cost_usd=None, total_input_tokens=None, total_output_tokens=None,
                 stop_experiment=True, force_fallback=True, stop_reason="max_estimated_cost_usd")
    recover.atomic_json(path, state)
    decision = "game_001/1901_winter_negotiation/tick-3/russia/other/negotiate_tick"
    r.record_prepared(decision_id=decision, prompt_hash="next-hash", attempt_id="blocked", provider={})
    r.record_response(decision_id=decision, prompt_hash="next-hash", attempt_id="blocked", provider={},
                      response={"content":"{}", "raw":{"budget_blocked":True,"budget_reason":"max_estimated_cost_usd"}},
                      usage={}, error_category=None, outcome="blocked")
    r.record_validation(decision_id=decision, prompt_hash="next-hash", attempt_id="blocked", valid=False)
    ledger = (r.root / "metrics/provider_attempts.jsonl").read_bytes()
    result = recover.reconcile_accounting(runner, r)
    assert result["false_budget_denial_cleared"] and r.cached_response(decision, "next-hash") is None
    assert r.attempt_count(decision) == 0
    after = recover.read(path)
    assert not after["stop_experiment"] and not after["force_fallback"]
    assert after["max_estimated_cost_usd"] == state["max_estimated_cost_usd"]
    assert (r.root / "metrics/provider_attempts.jsonl").read_bytes() == ledger
    before = r.journal_path.read_bytes(), path.read_bytes(), (path.parent / "budget_events.jsonl").read_bytes()
    recover.reconcile_accounting(runner, r)
    assert before == (r.journal_path.read_bytes(), path.read_bytes(), (path.parent / "budget_events.jsonl").read_bytes())
    assert any(x.get("kind") == "response_validation" and x.get("attempt_id") == "blocked" and x["valid"] is False
               for x in r._journal_rows())


def test_reconciled_budget_admits_next_request_with_same_limits(case):
    runner, r, manifest = case
    recover.apply_plan(runner, r, recover.build_plan(runner, r, manifest))
    reopened = BudgetController(schema_version="1.0", experiment_id="case", artifact_root=r.root / "metrics/budget",
                               config=BudgetConfig(**runner.config.budget))
    assert reopened.reserve_before_call(reservation_id="next", game_id="g2", phase_id="next", agent_id="next",
                                        method="reflect", input_tokens=1, output_tokens=1, cost_usd=.001).allow_provider_call
    assert reopened.total_provider_calls == 4 and reopened.config.max_provider_calls == 5

def test_free_text_reflection_response_import_accepts_unstructured_content(case):
    runner, recovery, _ = case
    decision = "game_001/1901_winter_negotiation/once/austria/actor/reflect"
    phase_inputs = recovery.working_root / "experiments/case/games/game_001/agents/actor/runtime/phase_inputs"
    saved = recover.read(phase_inputs / "1901_winter_negotiation_negotiate_tick.json")
    saved["method"] = "reflect"
    saved["reflection_protocol"] = "free_text_reflection_v1"
    recover.atomic_json(phase_inputs / "1901_winter_negotiation_reflect.json", saved)
    recovery.record_prepared(
        decision_id=decision, prompt_hash="offline-prose-prompt", attempt_id="offline-prose-attempt",
        provider={"reflection_protocol": "free_text_reflection_v1"},
    )

    assert recover.validate_selected(recovery.root, recovery, decision, "private prose\r\nnot JSON") is None
    assert recover.validate_selected(recovery.root, recovery, decision, "") is None
    saved["reflection_protocol"] = "legacy_json_v1"
    recover.atomic_json(phase_inputs / "1901_winter_negotiation_reflect.json", saved)
    with pytest.raises(ValueError, match="hash-chained prepared request"):
        recover.validate_selected(recovery.root, recovery, decision, "not JSON")


@pytest.mark.parametrize("method,content", [
    ("submit_orders", json.dumps({"orders": [], "order_rationale": {
        "goal_supported": "adjustments", "why_this_advances_strategy": "No adjustment is available."}})),
    ("negotiate_tick", json.dumps({"private_messages": [], "global_messages": [],
                                    "relationship_updates": [], "withheld_information": []})),
])
def test_runtime_written_nonreflection_phase_input_is_importable(case, method, content):
    from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
    from llm_diplomacy.agents.contracts import AgentContext

    _, recovery, _ = case
    context = AgentContext(
        schema_version="1.0", experiment_id="case", game_id="game_001",
        phase_id="1901_winter_negotiation", agent_id="actor", country_assignment="austria",
        visible_state={"year": 1901, "season": "winter", "phase": "adjustment", "game_id": "game_001"},
    )
    runtime_root = recovery.working_root / "experiments/case/games/game_001/agents"
    runtime = BaselineLLMAgentRuntime(schema_version="1.0", runtime_root=runtime_root, provider=None)
    runtime._write_phase_input(context, method)
    decision = f"game_001/{context.phase_id}/tick-1/austria/actor/{method}"

    assert recover.validate_selected(recovery.root, recovery, decision, content) is None
