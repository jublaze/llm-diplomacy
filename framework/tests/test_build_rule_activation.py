import json

import pytest

from test_build_replay_receipt import fixture, authorize, DECISION
from test_build_rule_adoption import evidence
from test_build_rule_accounting import setup
from llm_diplomacy.runner.recovery import canonical_hash


def activated(tmp_path):
    recovery, old, new = fixture(tmp_path)
    base = setup()["baseline_budget"]
    (recovery.root / "metrics/budget/budget_state.json").write_text(json.dumps(base), encoding="utf8")
    authorize(recovery, old, new)
    data = evidence(recovery)
    recovery.authorize_build_rule_adoption(evidence=data)
    recovery.authorize_build_rule_activation()
    return recovery, data


def cross_boundary(recovery):
    recovery.prepare()
    selector = recovery.build_rule_replay_selector
    row = selector.cached_response(recovery, decision_id=DECISION, prompt_hash="exact")
    selection = selector.select_attempt(decision_id=DECISION, prompt_hash="exact", cached=row)
    context = json.loads((recovery.path / "build-rule-evidence/context.json").read_bytes())
    selector.cross_boundary(selection=selection, decision_id=DECISION, cached=row,
        context_sha256=canonical_hash(context), response_hash=canonical_hash(row["response"]), valid=True)
    recovery.record_build_rule_boundary_validation()


def test_activation_is_idempotent_and_requires_exact_boundary_crossing(tmp_path):
    recovery, data = activated(tmp_path)
    journal = recovery.journal_path.read_bytes()
    recovery.authorize_build_rule_activation()
    assert recovery.journal_path.read_bytes() == journal
    recovery.prepare()
    selector = recovery.build_rule_replay_selector
    with pytest.raises(ValueError, match="activation"):
        selector.select("future")
    row = selector.cached_response(recovery, decision_id=DECISION, prompt_hash="exact")
    selection = selector.select_attempt(decision_id=DECISION, prompt_hash="exact", cached=row)
    context = json.loads((recovery.path / "build-rule-evidence/context.json").read_bytes())
    with pytest.raises(RuntimeError, match="adoption and activation"):
        selector.cross_boundary(selection=selection, decision_id=DECISION, cached=row,
            context_sha256="wrong", response_hash=canonical_hash(row["response"]), valid=True)
    selector.cross_boundary(selection=selection, decision_id=DECISION, cached=row,
        context_sha256=canonical_hash(context), response_hash=canonical_hash(row["response"]), valid=True)
    recovery.record_build_rule_boundary_validation()
    assert not selector.select("future").legacy_prompt
    with pytest.raises(ValueError, match="cannot be regenerated"):
        selector.select(DECISION)
    assert selector.cached_new_response(recovery, decision_id=DECISION, prompt_hash="exact") is None


def test_postactivation_accounting_matches_new_paid_record_and_rejects_tamper(tmp_path):
    recovery, data = activated(tmp_path)
    cross_boundary(recovery)
    new = setup()
    prepared, dispatch, attempt = new["rows"]
    recovery.record_prepared(decision_id=prepared["decision_id"], prompt_hash="p", attempt_id="new", provider={})
    recovery.record_dispatch(decision_id=dispatch["decision_id"], prompt_hash="p", attempt_id="new", provider={})
    recovery.record_response(decision_id=attempt["decision_id"], prompt_hash="p", attempt_id="new", provider={},
        response={"content": "{}"}, usage=attempt["usage"], error_category=None, outcome="received")
    ledger = recovery.root / "metrics/provider_attempts.jsonl"
    ledger.write_bytes(ledger.read_bytes() + new["current_ledger"][len(new["baseline_ledger"]):])
    budget = recovery.root / "metrics/budget/budget_state.json"
    budget.write_text(json.dumps(new["current_budget"]), encoding="utf8")
    recovery.prepare()
    bad = json.loads(budget.read_bytes()); bad["pending_reservations"] = {}
    budget.write_text(json.dumps(bad), encoding="utf8")
    with pytest.raises(ValueError, match="reserves"):
        recovery.prepare()


def test_future_dispatch_without_boundary_validation_is_rejected(tmp_path):
    recovery, data = activated(tmp_path)
    request = setup()["rows"][1]
    recovery.record_dispatch(decision_id=request["decision_id"], prompt_hash="p", attempt_id="new", provider={})
    with pytest.raises(ValueError, match="precedes durable boundary validation"):
        recovery.prepare()


def test_terminal_checkpoint_requires_crossed_boundary_and_durable_commit(tmp_path):
    recovery, data = activated(tmp_path)
    recovery.prepare_working_tree()
    selector = recovery.build_rule_replay_selector
    row = selector.cached_response(recovery, decision_id=DECISION, prompt_hash="exact")
    selection = selector.select_attempt(decision_id=DECISION, prompt_hash="exact", cached=row)
    context = json.loads((recovery.path / "build-rule-evidence/context.json").read_bytes())
    selector.cross_boundary(selection=selection, decision_id=DECISION, cached=row,
        context_sha256=canonical_hash(context), response_hash=canonical_hash(row["response"]), valid=True)
    recovery.record_build_rule_boundary_validation()
    stage = recovery.working_root / "terminal"
    stage.mkdir()
    (stage / "result.txt").write_text("complete", encoding="utf8")
    cursor = {"game_index": 1, "unit": "game_completed"}
    recovery.commit_tree(staged_root=stage, canonical_root=recovery.root, cursor=cursor,
                         state={"games_completed": 1}, applied=["game_001"])
    assert recovery.prepare()["cursor"] == cursor
    report_stage = recovery.working_root / "report"
    report_stage.mkdir()
    (report_stage / "report.txt").write_text("report", encoding="utf8")
    report_cursor = {"game_index": 1, "unit": "population_reports_completed"}
    recovery.commit_tree(staged_root=report_stage, canonical_root=recovery.root, cursor=report_cursor,
                         state={"games_completed": 1, "reports_completed": True}, applied=["game_001", "population_reports"])
    assert recovery.prepare()["cursor"] == report_cursor
    checkpoint = recovery._read_checkpoint()
    checkpoint["cursor"]["unit"] = "game_completed"
    from llm_diplomacy.runner.recovery import atomic_json
    atomic_json(recovery.checkpoint_path, {**checkpoint, "sha256": canonical_hash(checkpoint)})
    with pytest.raises(ValueError, match="differs from its durable receipt"):
        recovery.prepare()


@pytest.mark.parametrize("stage", ["adoption", "activation", "boundary"])
def test_authorization_append_head_interruption_is_idempotent(tmp_path, monkeypatch, stage):
    import llm_diplomacy.runner.recovery as module
    recovery, old, new = fixture(tmp_path)
    (recovery.root / "metrics/budget/budget_state.json").write_text(json.dumps(setup()["baseline_budget"]), encoding="utf8")
    authorize(recovery, old, new)
    data = evidence(recovery)
    if stage != "adoption": recovery.authorize_build_rule_adoption(evidence=data)
    if stage == "boundary":
        recovery.authorize_build_rule_activation()
        recovery.prepare()
        selector = recovery.build_rule_replay_selector
        row = selector.cached_response(recovery, decision_id=DECISION, prompt_hash="exact")
        selection = selector.select_attempt(decision_id=DECISION, prompt_hash="exact", cached=row)
        context = json.loads((recovery.path / "build-rule-evidence/context.json").read_bytes())
        selector.cross_boundary(selection=selection, decision_id=DECISION, cached=row,
            context_sha256=canonical_hash(context), response_hash=canonical_hash(row["response"]), valid=True)
    action = {"adoption": lambda: recovery.authorize_build_rule_adoption(evidence=data),
              "activation": recovery.authorize_build_rule_activation,
              "boundary": recovery.record_build_rule_boundary_validation}[stage]
    original = module.atomic_json
    def fail_head(path, value):
        if path == recovery.journal_head_path: raise OSError("injected head interruption")
        return original(path, value)
    monkeypatch.setattr(module, "atomic_json", fail_head)
    with pytest.raises(OSError, match="interruption"): action()
    monkeypatch.setattr(module, "atomic_json", original)
    action()
    recovery.prepare()
    kind = {"adoption": "build_rule_boundary_adoption_v1", "activation": "build_rule_activation_v1",
            "boundary": "build_rule_boundary_validation_v1"}[stage]
    assert sum(row.get("kind") == kind for row in recovery._journal_rows()) == 1
