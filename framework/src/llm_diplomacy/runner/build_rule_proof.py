"""Verify externally reviewed, hash-pinned offline replay evidence.

This routine does not append recovery events or authorize activation. Expected
file hashes must come from an independently reviewed sealed evidence manifest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .recovery import canonical_hash


def _pinned_json(path: Path, expected: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("replay proof input must be a regular file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("replay proof input differs from reviewed hash")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("replay proof input must be an object")
    return result


def verify_reviewed_boundary(*, receipt: dict[str, Any], journal_rows: list[dict[str, Any]],
                             result_path: Path, context_path: Path, tree_proof_path: Path,
                             expected_hashes: dict[str, str]) -> dict[str, Any]:
    if set(expected_hashes) != {"result", "context", "tree_proof"}:
        raise ValueError("all replay proof inputs require independent hash pins")
    result = _pinned_json(result_path, expected_hashes["result"])
    context = _pinned_json(context_path, expected_hashes["context"])
    tree = _pinned_json(tree_proof_path, expected_hashes["tree_proof"])
    boundary = receipt["boundary"]
    if (result.get("experiment") != receipt["from_identity"].get("experiment_id")
            or not result.get("expected_boundary_gate")
            or result.get("attempted_provider_calls") != {"generation": 0, "dispatch": 0}
            or not result.get("source_journal_prefix_unchanged") or not result.get("ledger_unchanged")
            or not result.get("budget_unchanged") or not result.get("candidate_package_matches_receipt")
            or result.get("candidate_package_before") != receipt["to_identity"]["code_sha256"]
            or result.get("candidate_package_after") != receipt["to_identity"]["code_sha256"]):
        raise ValueError("replay result is not the reviewed no-provider boundary run")
    if (tree.get("scan") != "windows_extended_length_paths" or tree.get("difference_count") != 0
            or tree.get("before_files") != tree.get("after_files")
            or tree.get("original_replay_result_sha256") != expected_hashes["result"]):
        raise ValueError("extended-path source inventory proof does not match replay result")
    trace = result.get("boundary_validation_trace")
    if not isinstance(trace, list) or not trace or trace[-1].get("context_sha256") != canonical_hash(context):
        raise ValueError("reconstructed context differs from boundary validation")
    if not trace[-1].get("valid"):
        raise ValueError("saved boundary response failed validation")
    source_rows = journal_rows[:receipt["source_journal_count"]]
    attempts = [row for row in source_rows if row.get("kind") == "attempt" and row.get("outcome") == "received"
                and row.get("decision_id") == boundary["decision_id"]]
    if (not attempts or attempts[-1].get("attempt_id") != boundary["attempt_id"]
            or attempts[-1].get("prompt_hash") != boundary["prompt_hash"]
            or canonical_hash(attempts[-1].get("response")) != boundary["response_hash"]):
        raise ValueError("proof boundary is not the exact latest saved response")
    content = attempts[-1]["response"]["content"]
    payload = json.loads(content)
    from llm_diplomacy.agents.contracts import AgentContext
    from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
    parts = boundary["decision_id"].split("/")
    if len(parts) != 6 or context.get("game_id") != parts[0]:
        raise ValueError("reconstructed board does not match the boundary game")
    agent_context = AgentContext("1.0", result["experiment"], parts[0], parts[1], parts[4], parts[3], context)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    runtime.schema_version = "1.0"
    if boundary["amend"]:
        if payload.get("orders") != [] or trace[-1].get("payload_sha256") != canonical_hash(payload):
            raise ValueError("saved empty response differs from the evaluated response")
        from llm_diplomacy.agents.llm_context import PromptContextBuilder
        builder = PromptContextBuilder(folio_root=Path("."))
        adjustment = builder._adjustment_context(state=context, country=parts[3])
        if (adjustment.get("obligation") != "build" or
                runtime._validate_submit_orders_legality(context=agent_context,
                    built_context={"legal_order_context": {"adjustment": adjustment}}, orders=[]) is not None):
            raise ValueError("reconstructed board does not authorize the empty build response")
        old_validations = [row for row in source_rows if row.get("kind") == "response_validation"
                           and row.get("decision_id") == boundary["decision_id"] and row.get("valid") is False]
        if len(old_validations) != len(attempts) - 1:
            raise ValueError("earlier boundary attempts do not have preserved rejection records")
        if len(trace) != len(attempts) or any(item.get("valid") for item in trace[:-1]):
            raise ValueError("boundary replay did not reproduce every old rejection")
    else:
        normalized, _ = runtime._normalize_negotiate_candidate(payload)
        if trace[-1].get("payload_sha256") != canonical_hash(normalized):
            raise ValueError("saved negotiation response differs from the evaluated response")
        if runtime._strict_negotiate_candidate_issue(raw_payload=payload, context=agent_context,
                                                     built_context={}) is not None:
            raise ValueError("saved negotiation response fails strict validation on reconstructed context")
    return {"version": "reviewed_build_boundary_proof_v1", "transition_sha256": receipt["sha256"],
            "decision_id": boundary["decision_id"], "attempt_id": boundary["attempt_id"],
            "prompt_hash": boundary["prompt_hash"], "response_hash": boundary["response_hash"],
            "context_sha256": canonical_hash(context), "proof_hashes": dict(expected_hashes),
            "valid": True, "adoption_authorized": False}
