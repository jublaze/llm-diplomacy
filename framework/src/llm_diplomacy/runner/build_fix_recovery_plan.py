"""Read-only evidence planning for a future, separately reviewed build-rule transition.

This module cannot authorize, append, adopt or resume an experiment. In particular,
the resulting plan is not a source-identity compatibility receipt.
"""
from __future__ import annotations

from typing import Any

from .recovery import ExperimentRecovery, _file_hash, canonical_hash

PLAN_VERSION = "build_rule_transition_evidence_v1"


def _historical_identity_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: row.get(key) for key in ("kind", "sequence", "sha256", "decision_id", "attempt_id", "prompt_hash")}
            for row in rows if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt", "response_validation", "repeat_correction_intent"}]


def inspect_historical_response(recovery: ExperimentRecovery, *, plan: dict[str, Any],
                                decision_id: str, attempt_id: str, prompt_hash: str) -> dict[str, Any]:
    """Check an exact saved response against the planned journal prefix, without validation or writes."""
    if plan.get("version") != PLAN_VERSION or plan.get("status") != "evidence_only_not_approved_for_apply":
        raise ValueError("invalid historical evidence plan")
    rows = recovery._journal_rows()
    count = plan.get("journal_count")
    if not isinstance(count, int) or count <= 0 or len(rows) < count:
        raise ValueError("planned journal prefix is missing")
    prefix = rows[:count]
    if (prefix[-1]["sha256"] != plan.get("journal_head")
            or canonical_hash(_historical_identity_records(prefix)) != plan.get("historical_identity_records_hash")):
        raise ValueError("planned historical journal prefix changed")
    matches = [row for row in prefix if row.get("kind") == "attempt" and row.get("attempt_id") == attempt_id]
    if (len(matches) != 1 or matches[0].get("outcome") != "received"
            or matches[0].get("decision_id") != decision_id or matches[0].get("prompt_hash") != prompt_hash):
        raise ValueError("historical response identity mismatch")
    validations = [row for row in prefix if row.get("kind") == "response_validation"
                   and row.get("attempt_id") == attempt_id]
    if any(row.get("decision_id") != decision_id or row.get("prompt_hash") != prompt_hash for row in validations):
        raise ValueError("historical validation identity mismatch")
    return {"decision_id": decision_id, "attempt_id": attempt_id, "original_prompt_hash": prompt_hash,
            "response_hash": canonical_hash(matches[0].get("response")),
            "historical_validations": [{"valid": row["valid"], "sha256": row["sha256"]} for row in validations]}


def plan_saved_boundary(
    recovery: ExperimentRecovery, *, attempt_id: str, expected_decision: str,
    expected_prompt_hash: str, boundary_kind: str,
) -> dict[str, Any]:
    """Bind a pending received answer or old-rule rejection to untouched history.

    The caller must independently establish writer ownership and validate the
    snapshot, runtime identities, accounting and legal board before any future
    transition. This function does not acquire a lock or write any file.
    """
    if boundary_kind not in {"received_unvalidated", "rejected_empty_build"}:
        raise ValueError("unsupported boundary kind")
    rows = recovery._journal_rows()
    recovery._validate_journal_semantics(rows)
    checkpoint = recovery._read_checkpoint()
    if not checkpoint or not checkpoint.get("halt_required"):
        raise ValueError("a valid halted checkpoint is required")
    if recovery.in_flight():
        raise ValueError("unresolved provider request blocks transition planning")
    if (recovery.path / "commit_manifest.json").exists():
        raise ValueError("unfinished publication blocks transition planning")
    attempts = [r for r in rows if r.get("kind") == "attempt" and r.get("attempt_id") == attempt_id]
    if len(attempts) != 1:
        raise ValueError("saved attempt must be unique")
    attempt = attempts[0]
    if (attempt.get("outcome") != "received" or attempt.get("decision_id") != expected_decision
            or attempt.get("prompt_hash") != expected_prompt_hash
            or not isinstance(attempt.get("response"), dict)):
        raise ValueError("saved response identity or outcome mismatch")
    received_for_decision = [row for row in rows if row.get("kind") == "attempt"
                             and row.get("decision_id") == expected_decision and row.get("outcome") == "received"]
    if not received_for_decision or received_for_decision[-1].get("attempt_id") != attempt_id:
        raise ValueError("a newer received response exists for this decision")
    cursor = checkpoint.get("cursor") or {}
    if not expected_decision.startswith(str(cursor.get("game_id", "")) + "/"):
        raise ValueError("saved answer does not belong to the halted game")
    validations = [r for r in rows if r.get("kind") == "response_validation" and r.get("attempt_id") == attempt_id]
    if any(r.get("decision_id") != expected_decision or r.get("prompt_hash") != expected_prompt_hash for r in validations):
        raise ValueError("saved validation identity mismatch")
    if boundary_kind == "received_unvalidated":
        if validations:
            raise ValueError("received-unvalidated boundary already has a validation")
    else:
        if (not validations or validations[-1].get("valid") is not False
                or validations[-1].get("issue") != "adjustment requires exactly 1 BUILD orders"
                or cursor.get("unit") != "phase_started"
                or expected_decision.split("/")[1] != cursor.get("phase_id")):
            raise ValueError("rejected-build boundary does not match the halted cursor")
        content = attempt["response"].get("content")
        import json
        try:
            payload = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise ValueError("saved response is not a JSON object") from exc
        if not isinstance(payload, dict) or payload.get("orders") != []:
            raise ValueError("saved response does not contain empty orders")
    required_files = {
        "checkpoint": recovery.checkpoint_path,
        "snapshot_inventory": recovery.path / "snapshot_inventory.json",
        "provider_ledger": recovery.root / "metrics" / "provider_attempts.jsonl",
        "budget": recovery.root / "metrics" / "budget" / "budget_state.json",
    }
    hashes = {name: _file_hash(path) for name, path in required_files.items()}
    if any(value is None for value in hashes.values()):
        raise ValueError("required accounting or snapshot evidence is missing")
    history = _historical_identity_records(rows)
    return {
        "version": PLAN_VERSION, "status": "evidence_only_not_approved_for_apply",
        "identity": checkpoint["identity"], "cursor": cursor,
        "boundary_kind": boundary_kind, "decision_id": expected_decision,
        "attempt_id": attempt_id, "original_prompt_hash": expected_prompt_hash,
        "attempt_record_hash": canonical_hash(attempt),
        "response_hash": canonical_hash(attempt["response"]),
        "prior_validation_hashes": [canonical_hash(row) for row in validations],
        "decision_rejection_count": sum(row.get("kind") == "response_validation"
            and row.get("decision_id") == expected_decision and row.get("valid") is False for row in rows),
        "historical_identity_records_hash": canonical_hash(history),
        "journal_count": len(rows), "journal_head": rows[-1]["sha256"] if rows else None,
        "evidence_file_hashes": hashes,
        "required_before_apply": ["fresh no-writer and no-inflight proof", "exclusive recovery lock",
            "complete snapshot and accounting validation", "exact before/after package identity and allowlist",
            "historical no-provider replay", "explicit versioned validation and adoption where applicable",
            "independent review and supervisor release"],
    }


def validate_empty_build_candidate(recovery: ExperimentRecovery, *, plan: dict[str, Any],
                                   context: Any, context_builder: Any, runtime: Any) -> dict[str, Any]:
    """Produce an offline validation candidate, never a durable adoption receipt.

    Context provenance must be independently established from a validated replay;
    until then the returned board hash is only a binding to caller-supplied data.
    """
    import json

    if plan.get("boundary_kind") != "received_unvalidated":
        raise ValueError("only the latest received-unvalidated response may be evaluated")
    current = plan_saved_boundary(recovery, attempt_id=plan["attempt_id"],
        expected_decision=plan["decision_id"], expected_prompt_hash=plan["original_prompt_hash"],
        boundary_kind="received_unvalidated")
    if current != plan:
        raise ValueError("saved boundary changed since planning")
    parts = plan["decision_id"].split("/")
    if (len(parts) != 6 or parts[-1] != "submit_orders" or context.game_id != parts[0]
            or context.phase_id != parts[1] or context.country_assignment != parts[3]
            or context.agent_id != parts[4] or context.visible_state.get("phase") != "adjustment"):
        raise ValueError("candidate context does not match the saved adjustment decision")
    attempt = next(row for row in recovery._journal_rows()
                   if row.get("kind") == "attempt" and row.get("attempt_id") == plan["attempt_id"])
    try:
        payload = json.loads(attempt["response"]["content"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("candidate response is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("orders") != []:
        raise ValueError("candidate is not an empty build response")
    adjustment = context_builder._adjustment_context(state=context.visible_state, country=context.country_assignment)
    if adjustment.get("obligation") != "build":
        raise ValueError("candidate context is not a build phase")
    built = {"legal_order_context": {"adjustment": adjustment}}
    valid, issue = runtime._validate_payload(method="submit_orders", payload=payload, context=context, built_context=built)
    if not valid:
        raise ValueError("candidate fails the amended validator: " + issue)
    return {"kind": "build_rule_adoption_candidate_v1", "status": "offline_candidate_not_adopted",
        "decision_id": plan["decision_id"], "attempt_id": plan["attempt_id"],
        "original_prompt_hash": plan["original_prompt_hash"], "response_hash": plan["response_hash"],
        "boundary_plan_hash": canonical_hash(plan), "board_hash": canonical_hash(context.visible_state),
        "adjustment_context_hash": canonical_hash(adjustment), "validator_version": "build_rule_validator_v1",
        "validated": True, "requires_verified_replay_and_append_only_adoption": True}
