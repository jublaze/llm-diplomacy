import pytest

from llm_diplomacy.runner.recovery import ExperimentRecovery, canonical_hash


def recovery():
    store = object.__new__(ExperimentRecovery)
    rows = [
        {"kind": "dispatch_prepared", "attempt_id": "original", "decision_id": "decision", "prompt_hash": "hash"},
        {"kind": "dispatch", "attempt_id": "original", "decision_id": "decision", "prompt_hash": "hash"},
        {"kind": "attempt", "attempt_id": "original", "decision_id": "decision", "prompt_hash": "hash", "outcome": "unknown"},
    ]
    store._journal_rows = lambda: rows
    store._append = rows.append
    return store, rows


@pytest.mark.parametrize("decision,prompt", [("decision", "hash"), ("decision", "drift"), ("other", "hash")])
def test_unresolved_attempt_blocks_all_fresh_dispatches(decision, prompt):
    store, _ = recovery()
    with pytest.raises(RuntimeError, match="unresolved"):
        store.assert_dispatch_allowed(decision_id=decision, prompt_hash=prompt)


@pytest.mark.parametrize("decision,attempt,prompt", [("other", "original", "hash"),
    ("decision", "wrong", "hash"), ("decision", "original", "wrong"), ("decision", "original", None)])
def test_authorization_must_match_full_identity(decision, attempt, prompt):
    store, rows = recovery()
    with pytest.raises(ValueError):
        store.resolve_unknown(decision_id=decision, attempt_id=attempt, prompt_hash=prompt, choice="retry_after_review")
    assert len(rows) == 3


def test_authorization_binds_next_dispatch_and_is_not_reusable():
    store, rows = recovery()
    store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")
    with pytest.raises(ValueError):
        store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")
    for decision, prompt in [("other", "hash"), ("decision", "drift")]:
        with pytest.raises(RuntimeError, match="authorized replacement"):
            store.assert_dispatch_allowed(decision_id=decision, prompt_hash=prompt)
    store.assert_dispatch_allowed(decision_id="decision", prompt_hash="hash")
    rows.append({"kind": "dispatch_prepared", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash"})
    rows.append({"kind": "attempt", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash", "outcome": "unknown"})
    with pytest.raises(RuntimeError, match="unresolved"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="hash")


def test_cannot_resolve_a_prepared_without_dispatch():
    store, rows = recovery()
    rows.pop(1)
    with pytest.raises(ValueError):
        store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")


@pytest.mark.parametrize("choice", ["retry_after_review", "abandon_decision"])
def test_explicit_decision_can_resolve_dispatched_attempt_without_outcome(choice):
    store, rows = recovery()
    rows.pop()
    store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice=choice)
    assert rows[-1]["kind"] == "unknown_resolved" and rows[-1]["choice"] == choice
    assert not store.in_flight()


def test_failed_replacement_does_not_reuse_authorization():
    store, rows = recovery()
    store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")
    rows.extend([{"kind": "dispatch_prepared", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash"},
                 {"kind": "attempt", "attempt_id": "replacement", "outcome": "known_failed"}])
    with pytest.raises(RuntimeError, match="already consumed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="hash")
    with pytest.raises(RuntimeError, match="already consumed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="drift")


def test_second_failed_replacement_does_not_reuse_first_skip():
    store, rows = recovery()
    store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")
    rows.extend([
        {"kind": "dispatch_prepared", "attempt_id": "b", "decision_id": "decision", "prompt_hash": "hash"},
        {"kind": "attempt", "attempt_id": "b", "outcome": "received"},
        {"kind": "response_skipped", "attempt_id": "b", "prompt_hash": "hash", "choice": "retry_after_review"},
        {"kind": "dispatch_prepared", "attempt_id": "c", "decision_id": "decision", "prompt_hash": "hash"},
        {"kind": "attempt", "attempt_id": "c", "outcome": "known_failed"},
    ])
    with pytest.raises(RuntimeError, match="already consumed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="hash")


def test_late_original_after_replacement_requires_review():
    store, rows = recovery()
    store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")
    rows.extend([
        {"kind": "dispatch_prepared", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash"},
        {"kind": "attempt", "attempt_id": "replacement", "outcome": "known_failed"},
        {"kind": "attempt", "attempt_id": "original", "outcome": "received"},
    ])
    with pytest.raises(RuntimeError, match="original and replacement"):
        store.assert_dispatch_allowed(decision_id="other", prompt_hash="hash")


def correction_case():
    store, rows = recovery()
    store.resolve_unknown(decision_id="decision", attempt_id="original", prompt_hash="hash", choice="retry_after_review")
    response = {"content": "bad"}
    rows.extend([
        {"kind": "dispatch_prepared", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash"},
        {"kind": "attempt", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash", "outcome": "received", "response": response},
        {"kind": "response_validation", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash", "valid": False, "issue": "bad schema"},
    ])
    for i, item in enumerate(rows): item["sequence"] = i
    feedback = {"rejected_answer": "bad", "validator_issue": "bad schema"}
    intent = {"kind": "repeat_correction_intent", "decision_id": "decision", "parent_attempt_id": "replacement",
              "parent_prompt_hash": "hash", "correction_prompt_hash": "corrected", "parent_response_hash": canonical_hash(response),
              "parent_validation_hash": canonical_hash(rows[-1]), "feedback": feedback, "feedback_hash": canonical_hash(feedback), "sequence": len(rows)}
    rows.append(intent)
    return store, rows


def test_exact_received_invalid_repeat_intent_allows_corrective_prompt():
    store, _ = correction_case()
    store.assert_dispatch_allowed(decision_id="decision", prompt_hash="corrected")


def test_exact_received_invalid_method_intent_allows_corrective_prompt():
    store, rows = correction_case()
    rows[-1]["kind"] = "order_correction_intent"
    rows[-1]["base_prompt_hash"] = rows[-1].pop("parent_prompt_hash")
    store.assert_dispatch_allowed(decision_id="decision", prompt_hash="corrected")


def test_method_intent_rejects_changed_parent_response():
    store, rows = correction_case()
    rows[-1]["kind"] = "order_correction_intent"
    rows[-1]["base_prompt_hash"] = rows[-1].pop("parent_prompt_hash")
    rows[-1]["parent_response_hash"] = "wrong"
    with pytest.raises(RuntimeError, match="already consumed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="corrected")


@pytest.mark.parametrize("field", ["rejected_answer", "validator_issue"])
def test_method_intent_rejects_substituted_feedback_even_with_new_hash(field):
    store, rows = correction_case()
    rows[-1]["kind"] = "order_correction_intent"
    rows[-1]["base_prompt_hash"] = rows[-1].pop("parent_prompt_hash")
    rows[-1]["feedback"][field] = "substituted"
    rows[-1]["feedback_hash"] = canonical_hash(rows[-1]["feedback"])
    with pytest.raises(RuntimeError, match="already consumed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="corrected")


def test_correction_rejects_later_parent_validation():
    store, rows = correction_case()
    rows.append({"kind": "response_validation", "attempt_id": "replacement", "valid": True, "sequence": len(rows)})
    with pytest.raises(RuntimeError, match="validation changed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="corrected")


@pytest.mark.parametrize("mutation", ["conflicting", "wrong_decision", "wrong_prompt"])
def test_skip_requires_latest_rejected_validation_with_matching_identity(mutation):
    store, rows = correction_case()
    rows.pop()
    if mutation == "conflicting":
        rows.append({"kind": "response_validation", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash", "valid": True})
    if mutation == "wrong_decision": rows[-1]["decision_id"] = "wrong"
    if mutation == "wrong_prompt": rows[-1]["prompt_hash"] = "wrong"
    rows.append({"kind": "response_skipped", "attempt_id": "replacement", "decision_id": "decision", "prompt_hash": "hash", "choice": "retry_after_review"})
    with pytest.raises(RuntimeError, match="already consumed"):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="hash")


@pytest.mark.parametrize("mutation", ["parent", "feedback", "hash", "stale"])
def test_invalid_repeat_intent_does_not_allow_corrective_prompt(mutation):
    store, rows = correction_case()
    intent = rows[-1]
    if mutation == "parent": intent["parent_attempt_id"] = "original"
    if mutation == "feedback": intent["feedback"]["validator_issue"] = "different"
    if mutation == "hash": intent["correction_prompt_hash"] = "wrong"
    if mutation == "stale":
        rows.insert(-1, {"kind": "attempt", "attempt_id": "newer", "decision_id": "decision", "prompt_hash": "hash", "outcome": "received", "sequence": intent["sequence"] - 1})
    with pytest.raises((ValueError, RuntimeError)):
        store.assert_dispatch_allowed(decision_id="decision", prompt_hash="corrected")
