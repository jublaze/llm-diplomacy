"""Recover stored Responses API results without generating another answer."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_provider import ResponsesAPIProvider, parse_llm_provider_config
from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, atomic_json, canonical_hash


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def json_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


_PLAN_VALIDATION_TOKEN = object()


class ValidatedImportPlan(dict):
    """A plan produced by this process's complete original-response validation."""

    def __init__(self, token, **kwargs):
        if token is not _PLAN_VALIDATION_TOKEN:
            raise ValueError("provider import plan must be created by original-response validation")
        super().__init__(**kwargs)
        self.validated_sha256 = canonical_hash(self)


def fetch_records(runner, recovery, manifest):
    """GET only. Preserve each observed remote state, even when still processing."""
    entries = []
    prepared = {r["attempt_id"]: r for r in recovery._journal_rows() if r.get("kind") == "dispatch_prepared"}
    for entry in manifest["responses"]:
        old = prepared[entry["attempt_id"]]
        agent = old["decision_id"].split("/")[-2]
        config = parse_llm_provider_config(runner._player_provider(next(p for p in runner.config.players if p.agent_id == agent)))
        if (config.base_url or "https://api.openai.com/v1").rstrip("/") != "https://api.openai.com/v1":
            raise ValueError("automatic retrieval is restricted to the original OpenAI endpoint")
        response_id = entry["response_id"]
        if not re.fullmatch(r"resp_[A-Za-z0-9]+", response_id):
            raise ValueError("invalid response ID")
        key = os.environ.get(config.api_key_env_var, "").strip()
        if not key:
            raise ValueError("project credential is unavailable; no retrieval")
        saved = {"attempt_id": entry["attempt_id"], "response_id": response_id}
        for suffix, label in (("", "response"), ("/input_items", "input")):
            request = urllib.request.Request("https://api.openai.com/v1/responses/" + response_id + suffix,
                                             headers={"Authorization": "Bearer " + key}, method="GET")
            with urllib.request.urlopen(request, timeout=60) as handle:
                value = json.loads(handle.read())
            path = recovery.path / "provider_observations" / response_id / (canonical_hash(value) + "-" + label + ".json")
            if not path.exists():
                atomic_json(path, value)
            saved[label + "_path"] = str(path.resolve())
        entries.append(saved)
    return {"responses": entries}


def validate_selected(root, recovery, decision, content):
    game, phase, _, country, agent, method = decision.split("/")
    runtime_root = recovery.working_root / "experiments" / root.name / "games" / game / "agents"
    saved = read(runtime_root / agent / "runtime/phase_inputs" / f"{phase}_{method}.json")
    context = AgentContext(**saved["context"])
    if context.country_assignment != country or context.agent_id != agent or saved["method"] != method:
        raise ValueError("saved validation context does not match the recovered decision")
    runtime = BaselineLLMAgentRuntime(schema_version=context.schema_version, runtime_root=runtime_root,
                                     provider=None, halt_on_issue=True,
                                     reflection_protocol=(
                                         (saved.get("reflection_protocol") or "legacy_json_v1")
                                         if method == "reflect" else "free_text_reflection_v1"
                                     ))
    if not isinstance(content, str):
        raise ValueError("recovered output content must be text")
    if method == "reflect":
        prepared_rows = [row for row in recovery._journal_rows()
                         if row.get("kind") == "dispatch_prepared" and row.get("decision_id") == decision]
        durable_protocol = (
            (prepared_rows[-1].get("provider") or {}).get("reflection_protocol", "legacy_json_v1")
            if prepared_rows else "legacy_json_v1"
        )
        if saved.get("reflection_protocol", "legacy_json_v1") != durable_protocol:
            raise ValueError("saved reflection protocol differs from the hash-chained prepared request")
        runtime.reflection_protocol = durable_protocol
    if method == "reflect" and runtime.reflection_protocol == "free_text_reflection_v1":
        return
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("recovered output must be a JSON object")
    built_context = runtime.context_builder.build(context)
    if method == "reflect":
        issue = runtime._fresh_reflection_issue(payload, context=context) or runtime._reflection_normalization_issue(payload)
        if issue:
            raise ValueError(issue)
        payload = runtime._normalize_reflection_candidate(payload)
    elif method == "negotiate_tick":
        issue = runtime._strict_negotiate_candidate_issue(
            raw_payload=payload, context=context, built_context=built_context)
        if issue:
            raise ValueError(issue)
        payload, normalization = runtime._normalize_negotiate_candidate(payload)
    valid, issue = runtime._validate_payload(method=method, payload=payload, context=context,
                                           built_context=built_context)
    if not valid:
        raise ValueError(f"recovered output is invalid: {issue}")
    if method == "reflect":
        issue = runtime._reflection_semantic_issue(payload=payload, context=context)
        if issue:
            raise ValueError(issue)


def build_plan(runner, recovery, manifest):
    """Validate immutable provider records and bind them to actual old requests."""
    root = recovery.root
    cp = recovery._read_checkpoint()
    recovery._validate_journal_semantics(recovery._journal_rows())
    recovery._validate_snapshot()
    if cp is None or cp["identity"] != recovery.identity:
        raise ValueError("recovery identity mismatch")
    pending = recovery.in_flight()
    if (recovery.path / "commit_manifest.json").exists():
        raise ValueError("import requires a halted run without pending publication")
    if not cp.get("halt_required"):
        if len(pending) != 1 or pending[0].get("kind") != "dispatch":
            raise ValueError("import requires a halted run or one interrupted durable dispatch")
        attempts = [row for row in recovery._journal_rows() if row.get("kind") == "attempt"
                    and row.get("attempt_id") == pending[0].get("attempt_id")]
        if attempts and not (len(attempts) == 1 and attempts[0].get("outcome") == "unknown"
                             and attempts[0].get("error_category") == "process_interrupted"):
            raise ValueError("unhalted import requires an interrupted dispatch")
    rows = recovery._journal_rows()
    entries = manifest.get("responses", [])
    if not entries or len({e["attempt_id"] for e in entries}) != len(entries):
        raise ValueError("provide distinct original attempts")
    observations = []
    reservations = [r for r in json_lines(root / "metrics/budget/budget_events.jsonl")
                    if r.get("event_type") == "provider_reservation"]
    for entry in entries:
        attempt = entry["attempt_id"]
        prepared = next((r for r in rows if r.get("kind") == "dispatch_prepared" and r.get("attempt_id") == attempt), None)
        dispatched = next((r for r in rows if r.get("kind") == "dispatch" and r.get("attempt_id") == attempt), None)
        attempts = [r for r in rows if r.get("kind") == "attempt" and r.get("attempt_id") == attempt]
        missing_outcome = (not attempts and len(pending) == 1 and pending[0].get("kind") == "dispatch"
                           and pending[0].get("attempt_id") == attempt)
        if (prepared is None or dispatched is None or
                (prepared.get("decision_id"), prepared.get("prompt_hash")) != (dispatched.get("decision_id"), dispatched.get("prompt_hash")) or
                not (missing_outcome or any(r.get("outcome") == "unknown" for r in attempts))):
            raise ValueError("record does not belong to an original unknown request")
        response, inputs = read(entry["response_path"]), read(entry["input_path"])
        if response.get("id") != entry["response_id"] or not str(response["id"]).startswith("resp_"):
            raise ValueError("provider response ID mismatch")
        if response.get("status") != "completed":
            raise ValueError("provider response is not completed; wait and retrieve it later")
        if response.get("error") or response.get("incomplete_details"):
            raise ValueError("provider returned an error or incomplete response")
        data = inputs.get("data", [])
        if inputs.get("has_more") or len(data) != 1 or data[0].get("role") != "user":
            raise ValueError("complete original user input is required")
        blocks = data[0].get("content", [])
        if not blocks or any(c.get("type") not in {"input_text", "text"} for c in blocks):
            raise ValueError("unexpected provider input content")
        prompt = "".join(c["text"] for c in blocks)
        if hashlib.sha256(prompt.encode()).hexdigest() != prepared["prompt_hash"]:
            raise ValueError("provider input prompt hash mismatch")
        game, phase, _, country, agent, method = prepared["decision_id"].split("/")
        config = parse_llm_provider_config(runner._player_provider(next(p for p in runner.config.players if p.agent_id == agent)))
        if config.api_style != "responses" or response.get("model") != config.model:
            raise ValueError("recovered provider/model mismatch")
        for name in ("created_at", "completed_at"):
            if isinstance(response.get(name), bool) or not isinstance(response.get(name), (int, float)) or not math.isfinite(response[name]):
                raise ValueError("valid provider timestamps are required")
        if not stamp(prepared["started_at"]) - 1 <= response["created_at"] <= stamp(dispatched["started_at"]) + 1:
            raise ValueError("provider creation time is outside the original request interval")
        if response["completed_at"] < response["created_at"]:
            raise ValueError("provider completion precedes creation")
        matches = [r for r in reservations if 0 <= stamp(r["timestamp"]) - stamp(prepared["started_at"]) <= 5
                   and all(r["payload"].get(k) == v for k, v in
                           {"agent_id": agent, "game_id": game, "phase_id": phase, "method": method}.items())]
        if len(matches) != 1:
            raise ValueError("original budget reservation is missing or ambiguous")
        usage = ResponsesAPIProvider._normalize_usage(response.get("usage"))
        if any(not isinstance(usage[k], int) or isinstance(usage[k], bool) or usage[k] < 0
               for k in ("input_tokens", "output_tokens", "total_tokens")):
            raise ValueError("provider token usage is missing or invalid")
        if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise ValueError("provider token usage is inconsistent")
        if config.input_cost_per_1k_tokens is None or config.output_cost_per_1k_tokens is None:
            raise ValueError("configured accounting prices are required")
        usage["estimated_cost_usd"] = round((usage["input_tokens"] * config.input_cost_per_1k_tokens
                                             + usage["output_tokens"] * config.output_cost_per_1k_tokens) / 1000, 10)
        content = ResponsesAPIProvider._extract_text(response)
        observations.append({"attempt_id": attempt, "decision_id": prepared["decision_id"],
            "prompt_hash": prepared["prompt_hash"], "sequence": prepared["sequence"],
            "reservation_id": matches[0]["payload"]["reservation_id"], "provider": prepared["provider"],
            "response_id": response["id"], "response_sha256": canonical_hash(response),
            "input_sha256": canonical_hash(inputs), "created_at": response["created_at"],
            "completed_at": response["completed_at"],
            "source_response": response, "source_input": inputs,
            "provider_pricing": {"input_cost_per_1k_tokens": config.input_cost_per_1k_tokens,
                                 "output_cost_per_1k_tokens": config.output_cost_per_1k_tokens},
            "provider_elapsed_seconds": response["completed_at"] - response["created_at"], "usage": usage,
            "response": {"content": content, "model": response["model"],
                         "raw": {"response": {"id": response["id"]}, "usage": usage,
                                 "recovered_from_provider": True,
                                 "provider_elapsed_seconds": response["completed_at"] - response["created_at"]}}})
    observations.sort(key=lambda r: r["sequence"])
    decisions = {r["decision_id"] for r in observations}
    if len(decisions) != 1 or len({r["response_id"] for r in observations}) != len(observations):
        raise ValueError("one decision and distinct provider responses are required")
    decision = observations[0]["decision_id"]
    unknowns = {r["attempt_id"] for r in rows if r.get("kind") == "attempt" and
                r.get("outcome") == "unknown" and r.get("decision_id") == decision}
    unknowns.update(item["attempt_id"] for item in pending if item.get("kind") == "dispatch"
                    and item.get("decision_id") == decision and not any(
                        row.get("kind") == "attempt" and row.get("attempt_id") == item["attempt_id"] for row in rows))
    correction = recovery.order_correction_intent(decision)
    if correction is not None:
        resolved_for_retry = {
            row["attempt_id"] for row in rows
            if row.get("kind") == "unknown_resolved" and row.get("decision_id") == decision
            and row.get("choice") == "retry_after_review"
            and any(attempt.get("kind") == "attempt" and attempt.get("outcome") == "unknown"
                    and attempt.get("decision_id") == decision
                    and attempt.get("attempt_id") == row.get("attempt_id")
                    and attempt.get("prompt_hash") == row.get("prompt_hash")
                    and attempt.get("sequence", 0) < row.get("sequence", 0) for attempt in rows)
        }
        if recovery.is_abandoned(decision):
            raise ValueError("an abandoned decision cannot be recovered")
        unknowns -= resolved_for_retry
    if unknowns != {r["attempt_id"] for r in observations}:
        raise ValueError("retrieve every unknown attempt for this decision before import")
    originals = [r for r in rows if r.get("kind") == "attempt" and r.get("outcome") == "received"
                 and r.get("decision_id") == decision]
    if recovery.repeat_received_invalid:
        validate_repeat_originals(recovery, rows, observations, originals)
    elif correction is None:
        if any(not (r.get("response", {}).get("raw") or {}).get("recovered_from_provider") for r in originals):
            raise ValueError("a completed local response already exists; never replace it")
    else:
        parent = next((r for r in originals if r.get("attempt_id") == correction["parent_attempt_id"]), None)
        imported = {r["attempt_id"]: r for r in originals
                    if (r.get("response", {}).get("raw") or {}).get("recovered_from_provider")}
        imported_observations = {r["attempt_id"]: r for r in observations}
        if (not parent or parent.get("prompt_hash") != correction["base_prompt_hash"]
                or canonical_hash(parent.get("response")) != correction["parent_response_hash"]
                or any(r.get("prompt_hash") != correction["correction_prompt_hash"] for r in observations)
                or any(r.get("attempt_id") != parent.get("attempt_id")
                       and (r.get("attempt_id") not in imported
                            or imported[r["attempt_id"]].get("prompt_hash") != r.get("prompt_hash")
                            or imported[r["attempt_id"]].get("response") != r.get("response")
                            or imported_observations.get(r["attempt_id"], {}).get("response")
                            != r.get("response"))
                       for r in originals)):
            raise ValueError("retrieval does not match the exact durable corrective request")
    validate_selected(root, recovery, decision, observations[0]["response"]["content"])
    game, phase, _, _, agent, method = decision.split("/")
    validation_path = recovery.working_root / "experiments" / root.name / "games" / game / "agents" / agent / "runtime/phase_inputs" / f"{phase}_{method}.json"
    validation_context = read(validation_path)
    routes = [runner._player_provider(player) for player in runner.config.players]
    def has_secret(value):
        if isinstance(value, dict):
            return any(key.lower() in {"api_key", "token", "secret", "password"} or has_secret(item)
                       for key, item in value.items())
        return isinstance(value, list) and any(has_secret(item) for item in value)
    if has_secret(routes):
        raise ValueError("provider route contains a secret; no import proof was written")
    if recovery.identity.get("routes_sha256") is not None and canonical_hash(routes) != recovery.identity["routes_sha256"]:
        raise ValueError("provider routes differ from the recovery identity")
    def scrub(value):
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key.lower() not in {"api_key", "token", "secret", "password"}}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value
    configuration = scrub(getattr(runner.config, "raw", None))
    if recovery.identity.get("configuration_sha256") is not None and canonical_hash(configuration) != recovery.identity["configuration_sha256"]:
        raise ValueError("provider configuration differs from the recovery identity")
    return ValidatedImportPlan(_PLAN_VALIDATION_TOKEN, identity=recovery.identity, selected_attempt_id=observations[0]["attempt_id"],
                               observations=observations, validation_context=validation_context,
                               validation_context_sha256=canonical_hash(validation_context), provider_routes=routes,
                               provider_agents=[player.agent_id for player in runner.config.players], provider_configuration=configuration)


def validate_repeat_originals(recovery, rows, observations, originals):
    decision = observations[0]["decision_id"]
    hashes = {r["prompt_hash"] for r in observations}
    if len(hashes) != 1:
        raise ValueError("repeat recovery requires the exact pending actual prompt")
    current = next(iter(hashes))
    recovery.assert_prompt_identity(decision, current)
    intents = {r["correction_prompt_hash"]: r for r in rows
               if r.get("kind") == "repeat_correction_intent" and r.get("decision_id") == decision}
    ancestors = set()
    seen = set()
    while current in intents:
        if current in seen:
            raise ValueError("repeat recovery chain cycles")
        seen.add(current)
        intent = intents[current]
        recovery._validate_repeat_correction(intent, rows)
        ancestors.add(intent["parent_attempt_id"])
        current = intent["parent_prompt_hash"]
    imported = {r["attempt_id"]: r for r in observations}
    for original in originals:
        attempt_id = original["attempt_id"]
        if (attempt_id in imported and original["prompt_hash"] == imported[attempt_id]["prompt_hash"]
                and (original.get("response", {}).get("raw") or {}).get("recovered_from_provider")):
            continue
        validations = [r for r in rows if r.get("kind") == "response_validation" and r.get("attempt_id") == attempt_id]
        if attempt_id not in ancestors or not validations or validations[-1].get("valid") is not False:
            raise ValueError("repeat recovery cannot replace an accepted or unrelated original")


class ReadOnlyBudget(BudgetController):
    def _flush_state(self):
        pass


def import_transaction_path(recovery, plan):
    path = recovery.path / "provider_imports" / canonical_hash(plan)
    legacy = {k: v for k, v in plan.items() if k not in {"validation_context", "validation_context_sha256", "provider_routes", "provider_agents", "provider_configuration"}}
    legacy["observations"] = [{k: v for k, v in row.items()
              if k not in {"source_response", "source_input", "provider_pricing"}} for row in plan["observations"]]
    legacy_path = recovery.path / "provider_imports" / canonical_hash(legacy)
    if legacy_path != path and legacy_path.exists() and not path.exists():
        raise ValueError("legacy provider import transaction requires separate compatibility review")
    return path


def reconcile_accounting(runner, recovery, *, request_rows=None, dry_run=False, preserve_handled_http=False):
    """Restore known totals only when every actual dispatch has proven usage."""
    rows = recovery._journal_rows()
    recovery._validate_journal_semantics(rows)
    cp = recovery._read_checkpoint()
    terminal_read_only = dry_run and cp is not None and cp.get("cursor", {}).get("unit") == "population_reports_completed"
    upgrades = [row for row in rows if row.get("kind") == "terminal_currency_accounting_upgrade_v1"]
    terminal_compatible = (terminal_read_only and len(upgrades) == 1
                           and upgrades[0].get("from_identity") == cp.get("identity")
                           and upgrades[0].get("to_identity") == recovery.identity)
    if cp is None or (cp["identity"] != recovery.identity and not terminal_compatible) or not (cp.get("halt_required") or terminal_read_only):
        raise ValueError("accounting reconciliation requires the same halted identity")
    if request_rows is not None and not dry_run:
        raise ValueError("proposed request rows require a dry run")
    requests = [r for r in (request_rows if request_rows is not None else
                           json_lines(recovery.root / "metrics/provider_attempts.jsonl")) if r.get("kind") == "request"]
    dispatches = [r for r in rows if r.get("kind") == "dispatch"]
    if len(requests) != len(dispatches):
        raise ValueError("actual requests cannot be matched uniquely to dispatches")
    totals = {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
    unresolved_reviewed = []
    for request, dispatch in zip(requests, dispatches):
        game, phase, _, _, agent, method = dispatch["decision_id"].split("/")
        if any(request.get(k) != v for k, v in {"game_id": game, "phase_id": phase, "agent_id": agent, "method": method}.items()):
            raise ValueError("actual request/dispatch ordering is ambiguous")
        usage = request
        if request.get("error_type"):
            recovered = [r for r in rows if r.get("kind") == "attempt" and r.get("attempt_id") == dispatch["attempt_id"]
                         and r.get("outcome") == "received" and
                         (r.get("response", {}).get("raw") or {}).get("recovered_from_provider")]
            if not recovered:
                unknown = next((r for r in rows if r.get("kind") == "attempt"
                                and r.get("attempt_id") == dispatch["attempt_id"]
                                and r.get("decision_id") == dispatch["decision_id"]
                                and r.get("prompt_hash") == dispatch["prompt_hash"]
                                and r.get("outcome") == "unknown"), None)
                resolutions = [r for r in rows if r.get("kind") == "unknown_resolved"
                               and r.get("attempt_id") == dispatch["attempt_id"]
                               and r.get("decision_id") == dispatch["decision_id"]
                               and r.get("prompt_hash") == dispatch["prompt_hash"]
                               and r.get("choice") == "retry_after_review"
                               and unknown is not None and r.get("sequence", 0) > unknown.get("sequence", 0)]
                if unknown is not None and len(resolutions) == 1:
                    unresolved_reviewed.append(dispatch)
                    continue
                failed = [r for r in rows if r.get("kind") == "attempt"
                          and r.get("attempt_id") == dispatch["attempt_id"]
                          and r.get("decision_id") == dispatch["decision_id"]
                          and r.get("prompt_hash") == dispatch["prompt_hash"]
                          and r.get("outcome") == "known_failed" and r.get("error_category") == "provider_transient_http"]
                if preserve_handled_http and request["error_type"] == "ProviderHTTPError" and len(failed) == 1:
                    later = recovery.cached_response(dispatch["decision_id"], dispatch["prompt_hash"])
                    validations = [r for r in rows if r.get("kind") == "response_validation"
                                   and later is not None and r.get("attempt_id") == later["attempt_id"]]
                    resumed = later is not None and any(r.get("kind") == "resume_session"
                              and failed[0]["sequence"] < r["sequence"] < later["sequence"] for r in rows)
                    if resumed and validations and validations[-1].get("valid") is True:
                        unresolved_reviewed.append(dispatch)
                        continue
                raise ValueError("an actual request still has unknown or ambiguous usage")
            if len(recovered) != 1:
                raise ValueError("an actual request still has unknown or ambiguous usage")
            usage = recovered[0]["usage"]
        for key in totals:
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("an actual request has incomplete usage")
            if key != "estimated_cost_usd" and not isinstance(value, int):
                raise ValueError("token accounting must use integer counts")
            totals[key] += value
    budget_class = ReadOnlyBudget if dry_run else BudgetController
    budget = budget_class(schema_version=runner.config.schema_version, experiment_id=runner.config.experiment_id,
                              artifact_root=recovery.root / "metrics/budget", config=BudgetConfig(**runner.config.budget))
    if unresolved_reviewed:
        events = [r for r in json_lines(recovery.root / "metrics/budget/budget_events.jsonl")
                  if r.get("event_type") == "provider_reservation"]
        expected_pending = set()
        for dispatch in unresolved_reviewed:
            game, phase, _, _, agent, method = dispatch["decision_id"].split("/")
            prepared = next(r for r in rows if r.get("kind") == "dispatch_prepared"
                            and r.get("attempt_id") == dispatch["attempt_id"])
            matches = [r for r in events if 0 <= stamp(r["timestamp"]) - stamp(prepared["started_at"]) <= 5
                       and all(r["payload"].get(k) == v for k, v in
                               {"agent_id": agent, "game_id": game, "phase_id": phase, "method": method}.items())]
            if len(matches) != 1:
                raise ValueError("reviewed unknown reservation is missing or ambiguous")
            reservation_id = matches[0]["payload"]["reservation_id"]
            reservation = budget.pending_reservations.get(reservation_id)
            if reservation is None or reservation.get("status") != "uncertain":
                raise ValueError("reviewed unknown exposure is not retained as uncertain")
            expected_pending.add(reservation_id)
        if set(budget.pending_reservations) != expected_pending or budget.total_provider_calls != len(requests):
            raise ValueError("partial accounting has unrelated pending provider exposure")
        for key, value in totals.items():
            if abs(getattr(budget, "known_" + key) - value) > 1e-8:
                raise ValueError("known usage differs from proven provider accounting")
        if any(getattr(budget, name) is not None for name in
               ("total_input_tokens", "total_output_tokens", "total_estimated_cost_usd")):
            raise ValueError("unknown provider totals were incorrectly cleared")
        if budget._exceeded_reason() is not None:
            raise ValueError("a real budget limit is reached; no override permitted")
        if budget.stop_experiment or budget.force_fallback or budget.stop_game_after_phase:
            raise ValueError("an unrelated budget stop cannot be cleared during partial reconciliation")
        return {"all_actual_requests_accounted": False, "known_totals": totals,
                "retained_uncertain_requests": len(unresolved_reviewed),
                "accounting": "Known usage reconciled; reviewed unknown requests remain reserved."}
    if budget.pending_reservations or budget.total_provider_calls != len(requests):
        raise ValueError("pending or unmatched provider accounting remains")
    for key, value in totals.items():
        if abs(getattr(budget, "known_" + key) - value) > 1e-8:
            raise ValueError("reconstructed usage differs from known budget accounting")
    blocked = [r for r in rows if r.get("kind") == "attempt" and r.get("outcome") == "blocked"
               and (r.get("response", {}).get("raw") or {}).get("budget_reason") == "max_estimated_cost_usd"
               and r["attempt_id"] not in {d["attempt_id"] for d in dispatches}]
    basis = {"dispatches": canonical_hash(dispatches), "totals": totals,
             "blocked_attempts": [r["attempt_id"] for r in blocked]}
    receipt_path = recovery.path / "budget_reconciliations" / (canonical_hash(basis) + ".json")
    before = read(receipt_path) if receipt_path.exists() else {"basis": basis, "before": budget.status()}
    if before["basis"] != basis:
        raise ValueError("accounting reconciliation receipt differs")
    had_unknown_cost = before["before"]["total_estimated_cost_usd"] is None
    false_denial = (had_unknown_cost and before["before"]["stop_reason"] == "max_estimated_cost_usd"
                    and bool(blocked))
    changed = False
    for key in totals:
        name = "total_" + key
        if getattr(budget, name) != getattr(budget, "known_" + key):
            setattr(budget, name, getattr(budget, "known_" + key))
            changed = True
    if budget._exceeded_reason() is not None:
        raise ValueError("a real budget limit is reached; no override permitted")
    if budget.stop_experiment or budget.force_fallback or budget.stop_game_after_phase:
        if not false_denial:
            raise ValueError("an unrelated budget stop cannot be cleared")
        budget.stop_experiment = budget.force_fallback = budget.stop_game_after_phase = False
        budget.stop_reason = budget.stop_game_id = None
        changed = True
    if any(getattr(budget, name) != 0 for name in ("pending_cost_usd", "pending_input_tokens", "pending_output_tokens")):
        budget.pending_cost_usd = 0.0
        budget.pending_input_tokens = budget.pending_output_tokens = 0
        changed = True
    if dry_run:
        return {"all_actual_requests_accounted": len(requests), "totals": totals,
                "false_budget_denial_cleared": false_denial, "dry_run": True}
    if not receipt_path.exists():
        atomic_json(receipt_path, before)
    if changed:
        budget._append_event("provider_usage_reconciled", {"receipt": str(receipt_path), "totals": totals,
                             "actual_requests": len(requests), "false_budget_denial_cleared": false_denial})
        budget._flush_state()
    if false_denial:
        skipped = {r["attempt_id"] for r in rows if r.get("kind") == "response_skipped"}
        for r in blocked:
            if r["attempt_id"] not in skipped:
                recovery._append({"kind": "response_skipped", "decision_id": r["decision_id"],
                    "prompt_hash": r["prompt_hash"], "attempt_id": r["attempt_id"],
                    "choice": "proven_budget_reconciliation_no_provider_dispatch"})
    return {"all_actual_requests_accounted": len(requests), "totals": totals,
            "false_budget_denial_cleared": false_denial, "receipt": str(receipt_path)}


def apply_plan(runner, recovery, plan):
    """Caller holds writer.lock. Preserve history; settle once; import earliest last."""
    if not isinstance(plan, ValidatedImportPlan) or canonical_hash(plan) != plan.validated_sha256:
        raise ValueError("provider import requires a freshly validated plan")
    path = import_transaction_path(recovery, plan)
    checkpoint = recovery._read_checkpoint()
    for observation in plan["observations"]:
        attempt = observation["attempt_id"]
        attempts = [row for row in recovery._journal_rows() if row.get("kind") == "attempt" and row.get("attempt_id") == attempt]
        if not attempts:
            pending = recovery.in_flight()
            if (len(pending) != 1 or pending[0].get("kind") != "dispatch"
                    or (pending[0].get("attempt_id"), pending[0].get("decision_id"), pending[0].get("prompt_hash"))
                    != (attempt, observation["decision_id"], observation["prompt_hash"])):
                raise ValueError("interrupted import does not match the sole durable dispatch")
            recovery.record_response(decision_id=observation["decision_id"], prompt_hash=observation["prompt_hash"],
                attempt_id=attempt, provider=observation["provider"], response=None, usage=None,
                error_category="process_interrupted", outcome="unknown")
    if checkpoint is not None and not checkpoint.get("halt_required"):
        recovery.halt(reason="process_interrupted", decision_id=plan["observations"][0]["decision_id"])
    path.mkdir(parents=True, exist_ok=True)
    transaction = path / "transaction.json"
    budget_path = recovery.root / "metrics/budget/budget_state.json"
    events_path = budget_path.parent / "budget_events.jsonl"
    ledger = recovery.root / "metrics/provider_attempts.jsonl"
    if transaction.exists():
        before = read(transaction)
        if before["plan"] != plan:
            raise ValueError("import transaction differs from validated provider records")
    else:
        before = {"plan": plan, "budget_before": read(budget_path),
                  "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
                  "journal_before": recovery.journal_path.read_text(encoding="utf-8"),
                  "checkpoint_before": read(recovery.checkpoint_path),
                  "budget_events_before": events_path.read_text(encoding="utf-8") if events_path.exists() else ""}
        atomic_json(transaction, before)
    rows = recovery._journal_rows()
    if any(row.get("kind") == "build_rule_activation_v1" for row in rows):
        plan_hash = canonical_hash(plan)
        intents = [row for row in rows if row.get("kind") == "provider_import_intent_v1"
                   and row.get("plan_sha256") == plan_hash]
        expected = {"plan_sha256": plan_hash, "transaction_sha256": canonical_hash(before)}
        if len(intents) > 1 or (intents and any(intents[0].get(k) != v for k, v in expected.items())):
            raise ValueError("provider import intent changed or duplicated")
        if not intents:
            if recovery.journal_path.read_text(encoding="utf-8") != before["journal_before"]:
                raise ValueError("provider import history changed before intent")
            recovery._append({"kind": "provider_import_intent_v1", **expected})
    if hashlib.sha256(ledger.read_bytes()).hexdigest() != before["ledger_sha256"]:
        raise ValueError("actual request ledger changed during import")
    state = read(budget_path)
    baseline = before["budget_before"]
    if state["total_provider_calls"] != baseline["total_provider_calls"]:
        raise ValueError("provider call count changed during import")
    settled = [r for r in plan["observations"] if r["reservation_id"] not in state["pending_reservations"]]
    for field, usage_field in (("known_input_tokens", "input_tokens"), ("known_output_tokens", "output_tokens"),
                               ("known_estimated_cost_usd", "estimated_cost_usd")):
        expected = baseline[field] + sum(r["usage"][usage_field] for r in settled)
        if abs(state[field] - expected) > 1e-8:
            raise ValueError("budget settlement does not match this import transaction")
    for r in plan["observations"]:
        if r["reservation_id"] not in baseline["pending_reservations"]:
            raise ValueError("original reservation was not pending at import start")
    budget = BudgetController(schema_version=runner.config.schema_version, experiment_id=runner.config.experiment_id,
                              artifact_root=budget_path.parent, config=BudgetConfig(**runner.config.budget))
    activated = any(row.get("kind") == "build_rule_activation_v1" for row in rows)
    expected_budget = copy.deepcopy(baseline)
    for r in plan["observations"]:
        if not activated:
            budget.settle_reservation(reservation_id=r["reservation_id"], usage=r["usage"])
            continue
        from llm_diplomacy.runner.provider_import_intents import settled_status
        expected_budget = settled_status(expected_budget, r["reservation_id"], r["usage"])
        events_text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
        prefix = before.get("budget_events_before")
        if not isinstance(prefix, str) or not events_text.startswith(prefix):
            raise ValueError("provider import budget event prefix changed")
        game, phase, _, _, agent, method = r["decision_id"].split("/")
        prefix_count = len(prefix.splitlines())
        events = [json.loads(line) for line in events_text.splitlines()]
        matching = [(index, event) for index, event in enumerate(events[prefix_count:], prefix_count)
                    if event.get("event_type") == "budget_usage_update"
                    and event.get("experiment_id") == runner.config.experiment_id
                    and all(event.get("payload", {}).get(k) == v for k, v in
                            {"game_id": game, "phase_id": phase, "agent_id": agent, "method": method}.items())
                    and event.get("payload", {}).get("usage") == r["usage"]
                    and event.get("payload", {}).get("totals") == expected_budget]
        if len(matching) > 1:
            raise ValueError("provider import settlement event is duplicated")
        if r["reservation_id"] in budget.pending_reservations and matching:
            candidate = copy.deepcopy(budget)
            candidate._append_event = lambda *args, **kwargs: None
            candidate._flush_state = lambda: None
            candidate.settle_reservation(reservation_id=r["reservation_id"], usage=r["usage"])
            if candidate.status() != expected_budget:
                raise ValueError("provider import settlement state differs from event")
            append_event = budget._append_event
            budget._append_event = lambda *args, **kwargs: None
            try:
                budget.settle_reservation(reservation_id=r["reservation_id"], usage=r["usage"])
            finally:
                budget._append_event = append_event
        elif r["reservation_id"] in budget.pending_reservations:
            budget.settle_reservation(reservation_id=r["reservation_id"], usage=r["usage"])
            events = json_lines(events_path)
            matching = [(len(events) - 1, events[-1])]
            if events[-1].get("payload", {}).get("totals") != expected_budget:
                raise ValueError("provider import settlement did not produce expected totals")
        if len(matching) != 1:
            raise ValueError("provider import settlement event is missing")
        index, event = matching[0]
        marker = {"plan_sha256": plan_hash, "attempt_id": r["attempt_id"],
                  "event_index": index, "event_sha256": canonical_hash(event)}
        existing = [row for row in recovery._journal_rows() if row.get("kind") == "provider_import_settlement_v1"
                    and row.get("plan_sha256") == plan_hash and row.get("attempt_id") == r["attempt_id"]]
        if len(existing) > 1 or (existing and any(existing[0].get(k) != v for k, v in marker.items())):
            raise ValueError("provider import settlement marker changed")
        if not existing:
            recovery._append({"kind": "provider_import_settlement_v1", **marker})
    # Reverse order makes the earliest original attempt the selected cached answer.
    for r in reversed(plan["observations"]):
        previous = [x for x in recovery._journal_rows() if x.get("kind") == "attempt" and
                    x.get("attempt_id") == r["attempt_id"] and x.get("outcome") == "received"]
        if previous:
            if len(previous) != 1 or previous[0]["response"] != r["response"]:
                raise ValueError("an imported response changed")
            continue
        recovery.record_response(decision_id=r["decision_id"], prompt_hash=r["prompt_hash"],
            attempt_id=r["attempt_id"], provider=r["provider"], response=r["response"], usage=r["usage"],
            error_category=None, outcome="received")
    selected = plan["observations"][0]
    if recovery.cached_response(selected["decision_id"], selected["prompt_hash"])["attempt_id"] != selected["attempt_id"]:
        raise ValueError("cached selection differs from earliest original response")
    accounting = reconcile_accounting(runner, recovery)
    receipt = {"completed_at": datetime.now(timezone.utc).isoformat(), "selected_attempt_id": selected["attempt_id"],
               "observations": [{k: v for k, v in r.items() if k not in {"response", "source_response", "source_input"}} for r in plan["observations"]],
               "actual_requests_added": 0, "reconciled_totals": accounting,
               "accounting": "Configured-price estimates; all remote completions counted, including unused duplicates."}
    if not (path / "receipt.json").exists():
        atomic_json(path / "receipt.json", receipt)
    return read(path / "receipt.json")


def complete_interrupted_import(runner, recovery, plan, *, apply=False):
    """Caller holds writer.lock; restore one lost final ledger row from a verified import."""
    if len(plan["observations"]) != 1:
        raise ValueError("interrupted import requires exactly one original response")
    path = import_transaction_path(recovery, plan)
    transaction = read(path / "transaction.json")
    if transaction["plan"] != plan:
        raise ValueError("interrupted import transaction changed")
    observation = plan["observations"][0]
    rows = recovery._journal_rows()
    dispatches = [r for r in rows if r.get("kind") == "dispatch"]
    if not dispatches or dispatches[-1]["attempt_id"] != observation["attempt_id"]:
        raise ValueError("only the final interrupted dispatch can be completed")
    if not any(r.get("kind") == "attempt" and r.get("attempt_id") == observation["attempt_id"]
               and r.get("outcome") == "unknown" and r.get("error_category") == "process_interrupted"
               for r in rows):
        raise ValueError("original process interruption evidence is missing")
    cached = recovery.cached_response(observation["decision_id"], observation["prompt_hash"])
    if cached is None or cached.get("attempt_id") != observation["attempt_id"] or cached.get("response") != observation["response"]:
        raise ValueError("verified original response is not the selected cached answer")
    state = read(recovery.root / "metrics/budget/budget_state.json")
    baseline = transaction["budget_before"]
    reservation = observation["reservation_id"]
    if reservation not in baseline["pending_reservations"] or reservation in state["pending_reservations"]:
        raise ValueError("original import reservation has not been settled exactly once")
    expected_pending = {k: v for k, v in baseline["pending_reservations"].items() if k != reservation}
    if state["pending_reservations"] != expected_pending or state["total_provider_calls"] != baseline["total_provider_calls"]:
        raise ValueError("unrelated provider accounting changed")
    for field, usage_field in (("known_input_tokens", "input_tokens"), ("known_output_tokens", "output_tokens"),
                               ("known_estimated_cost_usd", "estimated_cost_usd")):
        if abs(state[field] - baseline[field] - observation["usage"][usage_field]) > 1e-8:
            raise ValueError("original import usage differs from settlement")
    game, phase, _, _, agent, method = observation["decision_id"].split("/")
    if method not in {"negotiate_tick", "submit_orders", "reflect"}:
        raise ValueError("interrupted cleanup imports are not supported")
    request = {"kind": "request", "schema_version": runner.config.schema_version,
               "game_id": game, "phase_id": phase, "agent_id": agent, "method": method,
               "provider": observation["provider"], "resolved_model": observation["response"]["model"],
               "post_elimination_cleanup": False, "error_type": None, "cached_input_tokens": None,
               **{k: observation["usage"][k] for k in
                  ("input_tokens", "output_tokens", "reasoning_tokens", "estimated_cost_usd")},
               "attempt_id": observation["attempt_id"], "prompt_hash": observation["prompt_hash"],
               "recovered_response_id": observation["response_id"],
               "provider_created_at": observation["created_at"], "provider_completed_at": observation["completed_at"],
               "provider_elapsed_seconds": observation["provider_elapsed_seconds"],
               "recovery_import_sha256": canonical_hash(plan)}
    line = (json.dumps(request, sort_keys=True) + "\n").encode("utf-8")
    ledger = recovery.root / "metrics/provider_attempts.jsonl"
    raw = ledger.read_bytes()
    already_appended = raw.endswith(line) and hashlib.sha256(raw[:-len(line)]).hexdigest() == transaction["ledger_sha256"]
    if not already_appended and hashlib.sha256(raw).hexdigest() != transaction["ledger_sha256"]:
        raise ValueError("original request ledger prefix changed")
    if raw and not raw.endswith(b"\n"):
        raise ValueError("request ledger has an incomplete final line")
    requests = [json.loads(r) for r in raw.decode("utf-8").splitlines()]
    proposed = requests if already_appended else requests + [request]
    reconcile_accounting(runner, recovery, request_rows=proposed, dry_run=True, preserve_handled_http=True)
    report = {"selected_attempt_id": observation["attempt_id"], "reconstructed_request_rows": 1,
              "new_provider_calls": 0, "ledger_already_completed": already_appended, "applied": apply}
    if not apply:
        return report
    intent = {"plan_sha256": canonical_hash(plan), "ledger_before_sha256": transaction["ledger_sha256"],
              "request": request}
    intent_path = path / "interrupted_ledger_intent.json"
    if intent_path.exists() and read(intent_path) != intent:
        raise ValueError("interrupted ledger completion intent changed")
    if not intent_path.exists():
        atomic_json(intent_path, intent)
    if not already_appended:
        with ledger.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    accounting = reconcile_accounting(runner, recovery, preserve_handled_http=True)
    receipt = {"completed_at": datetime.now(timezone.utc).isoformat(), **report,
               "reconciled_totals": accounting, "ledger_after_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest()}
    receipt_path = path / "interrupted_ledger_receipt.json"
    if not receipt_path.exists():
        atomic_json(receipt_path, receipt)
    return read(receipt_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--agents-root", required=True)
    parser.add_argument("--manifest", required=True, help="JSON responses list: attempt_id,response_id,response_path,input_path")
    parser.add_argument("--fetch", action="store_true", help="GET saved provider results/input only; no generation. Paths optional in manifest.")
    parser.add_argument("--apply", action="store_true", help="Import verified saved results; never launches play or generates.")
    parser.add_argument("--reconcile-accounting", action="store_true", help="Finish a prior import's complete-usage reconciliation; no generation.")
    parser.add_argument("--complete-interrupted-import", action="store_true",
                        help="Validate one missing final request ledger entry from a prior verified import; use --apply to append it.")
    args = parser.parse_args()
    config = load_population_experiment_config(args.config)
    runner = PopulationExperimentRunner(config=config, base_dir=args.output_root, agents_root=args.agents_root)
    root = Path(args.output_root) / "experiments" / config.experiment_id
    if config.game_count != 1 or not config.raw.get("provider_failure_policy", {}).get("strict_recovery"):
        raise ValueError("one strict-recovery game is required")
    recovery = runner._recovery_store()
    with RecoveryLock(recovery.path / "writer.lock"):
        if args.reconcile_accounting:
            if args.apply or args.fetch or args.complete_interrupted_import:
                raise ValueError("accounting-only reconciliation cannot fetch or import")
            print(json.dumps(reconcile_accounting(runner, recovery), indent=2))
            return
        manifest = read(args.manifest)
        if args.fetch:
            manifest = fetch_records(runner, recovery, manifest)
            pending = [read(e["response_path"]).get("status") for e in manifest["responses"]]
            if any(status in {"queued", "in_progress"} for status in pending):
                print(json.dumps({"waiting_for_existing_responses": True, "statuses": pending, "applied": False}))
                return
        plan = build_plan(runner, recovery, manifest)
        if args.complete_interrupted_import:
            print(json.dumps(complete_interrupted_import(runner, recovery, plan, apply=args.apply), indent=2))
            return
        report = apply_plan(runner, recovery, plan) if args.apply else {
            "selected_attempt_id": plan["selected_attempt_id"], "validated_responses": len(plan["observations"]),
            "provider_elapsed_seconds": [r["provider_elapsed_seconds"] for r in plan["observations"]], "applied": False}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
