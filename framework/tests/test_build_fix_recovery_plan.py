import hashlib
import json
import shutil

import pytest

from llm_diplomacy.runner.build_fix_recovery_plan import plan_saved_boundary, validate_empty_build_candidate, inspect_historical_response
from llm_diplomacy.runner.recovery import ExperimentRecovery
from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.agents.contracts import AgentContext


def make_copied_halt(tmp_path, *, pending, adjustment_pending=False):
    root = tmp_path / "original"
    recovery = ExperimentRecovery(root, identity={"experiment_id": "synthetic", "code_sha256": "old"}, resume=False)
    recovery.prepare()
    decision = ("game_001/1904_winter_negotiation/tick-3/france/metternich/negotiate_tick" if pending and not adjustment_pending
                else "game_001/1908_winter_adjustment/once/france/metternich/submit_orders")
    phase = "1904_winter_adjustment" if pending and not adjustment_pending else "1908_winter_adjustment"
    recovery.record_dispatch(decision_id=decision, prompt_hash="original-prompt", attempt_id="saved-attempt", provider={})
    recovery.record_response(decision_id=decision, prompt_hash="original-prompt", attempt_id="saved-attempt",
        provider={}, response={"content": json.dumps({"orders": [], "order_rationale": {"goal_supported": "hold", "why_this_advances_strategy": "hold"}}), "model": "fake", "raw": {}},
        usage={"input_tokens": 3, "output_tokens": 2}, error_category=None, outcome="received")
    if not pending:
        recovery.record_validation(decision_id=decision, prompt_hash="original-prompt", attempt_id="saved-attempt",
            valid=False, issue="adjustment requires exactly 1 BUILD orders")
    recovery.checkpoint(cursor={"game_id": "game_001", "phase_id": phase, "unit": "phase_started"}, state={}, applied=[])
    recovery.halt(reason="synthetic_pause")
    (root / "recovery" / "snapshot_inventory.json").write_text('{}', encoding="utf-8")
    (root / "metrics" / "budget").mkdir(parents=True)
    (root / "metrics" / "budget" / "budget_state.json").write_text('{"reserve":5}', encoding="utf-8")
    (root / "metrics" / "provider_attempts.jsonl").write_text('{"attempt":"saved-attempt"}\n', encoding="utf-8")
    copied = tmp_path / "copied"
    shutil.copytree(root, copied)
    return ExperimentRecovery(copied, identity=recovery.identity, resume=True), decision


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize("pending", [True, False])
def test_copied_boundary_plan_preserves_every_file_and_original_identity(tmp_path, pending):
    recovery, decision = make_copied_halt(tmp_path, pending=pending)
    before = hashes(recovery.root)
    plan = plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
        expected_prompt_hash="original-prompt", boundary_kind="received_unvalidated" if pending else "rejected_empty_build")
    assert plan["attempt_id"] == "saved-attempt" and plan["original_prompt_hash"] == "original-prompt"
    assert len(plan["prior_validation_hashes"]) == (0 if pending else 1)
    assert plan["status"] == "evidence_only_not_approved_for_apply"
    assert hashes(recovery.root) == before


def test_pending_answer_is_not_reclassified_or_replaced(tmp_path):
    recovery, decision = make_copied_halt(tmp_path, pending=True)
    with pytest.raises(ValueError, match="identity"):
        plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
            expected_prompt_hash="different", boundary_kind="received_unvalidated")
    with pytest.raises(ValueError, match="boundary"):
        plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
            expected_prompt_hash="original-prompt", boundary_kind="rejected_empty_build")
    recovery.record_dispatch(decision_id=decision, prompt_hash="other", attempt_id="unknown", provider={})
    with pytest.raises(ValueError, match="unresolved"):
        plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
            expected_prompt_hash="original-prompt", boundary_kind="received_unvalidated")


def test_an_older_rejected_answer_cannot_supersede_a_newer_received_answer(tmp_path):
    recovery, decision = make_copied_halt(tmp_path, pending=False)
    recovery.record_dispatch(decision_id=decision, prompt_hash="next-prompt", attempt_id="next", provider={})
    recovery.record_response(decision_id=decision, prompt_hash="next-prompt", attempt_id="next", provider={},
        response={"content": '{"orders":[]}', "model": "fake", "raw": {}},
        usage={"input_tokens": 1}, error_category=None, outcome="received")
    with pytest.raises(ValueError, match="newer received"):
        plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
            expected_prompt_hash="original-prompt", boundary_kind="rejected_empty_build")
    plan = plan_saved_boundary(recovery, attempt_id="next", expected_decision=decision,
        expected_prompt_hash="next-prompt", boundary_kind="received_unvalidated")
    assert plan["decision_rejection_count"] == 1 and plan["prior_validation_hashes"] == []


def test_offline_empty_build_candidate_does_not_append_or_change_the_original(tmp_path):
    recovery, decision = make_copied_halt(tmp_path, pending=True, adjustment_pending=True)
    before = hashes(recovery.root)
    plan = plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
        expected_prompt_hash="original-prompt", boundary_kind="received_unvalidated")
    state = dict(year=1908, season="winter", phase="adjustment", units=[
        dict(unit_id=p, owner="france", type="A", province=p, status="active") for p in ("bre", "par", "mar")
    ], supply_ownership={p: "france" for p in ("bre", "par", "mar", "bel")})
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "metternich", "france", state)
    result = validate_empty_build_candidate(recovery, plan=plan, context=ctx,
        context_builder=PromptContextBuilder(folio_root=tmp_path), runtime=object.__new__(BaselineLLMAgentRuntime))
    assert result["validated"] is True and result["status"] == "offline_candidate_not_adopted"
    assert result["original_prompt_hash"] == "original-prompt"
    assert hashes(recovery.root) == before
    ctx.visible_state["supply_ownership"] = {"bre": "france"}
    with pytest.raises(ValueError, match="not a build"):
        validate_empty_build_candidate(recovery, plan=plan, context=ctx,
            context_builder=PromptContextBuilder(folio_root=tmp_path), runtime=object.__new__(BaselineLLMAgentRuntime))


@pytest.mark.parametrize("pending", [True, False])
def test_historical_inspection_preserves_exact_prompt_and_old_validation(tmp_path, pending):
    recovery, decision = make_copied_halt(tmp_path, pending=pending)
    plan = plan_saved_boundary(recovery, attempt_id="saved-attempt", expected_decision=decision,
        expected_prompt_hash="original-prompt", boundary_kind="received_unvalidated" if pending else "rejected_empty_build")
    before = hashes(recovery.root)
    result = inspect_historical_response(recovery, plan=plan, decision_id=decision,
        attempt_id="saved-attempt", prompt_hash="original-prompt")
    assert [row["valid"] for row in result["historical_validations"]] == ([] if pending else [False])
    with pytest.raises(ValueError, match="identity"):
        inspect_historical_response(recovery, plan=plan, decision_id=decision,
            attempt_id="saved-attempt", prompt_hash="changed-prompt")
    assert hashes(recovery.root) == before
