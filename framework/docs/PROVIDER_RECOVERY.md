# Provider timeout recovery and budget admission

## Opt-in repeated received-invalid corrections for new populations

`provider_failure_policy.received_invalid_policy: repeat_with_feedback_v1`
requires strict recovery and finite positive total call/input/output/USD guards.
It changes new-run identity and cannot be enabled on historical evidence by
rewriting a checkpoint. Defaults and existing exact saved-run exceptions remain.

Each correction binds the immediately preceding actual received response and
false validation, full escaped raw answer, parser error where applicable,
required schema, and the same player's permitted context/legal options. The
append-only `repeat_correction_intent` records parent attempt/actual prompt,
response/validation/feedback hashes and the distinct actual corrective hash.
Runtime and engine rule failures can receive further corrections within the
shared total allowance. Accepted cached answers are replayed without generation.
No numerical per-player correction ceiling is introduced.

Transport unknown, auth/credit, budget, integrity and execution ambiguity remain
halts; this flag never authorizes replacing an unknown original. Pending reserves
remain accounted. An opt-in conservative prompt bound (`UTF8 bytes + 256`)
must not exceed 272,000 before a new dispatch, preserving the configured
short-context price assumptions without truncating context. Configured rates
remain estimates, not a vendor billing cap; cached/write/tier/regional accounting
must not be invented.

Optional positive integer `provider_failure_policy.max_wall_seconds` binds a
wall-time guard to the durable first-created UTC timestamp. Restarts and
`--resume` do not reset it. Cooperative checkpoints/pre-dispatch checks halt on
expiry. It does not kill in-flight responses; receipt/accounting is recorded
before the next safe-halt check. A request already in flight can finish after
the wall deadline. Neither guard permits source/accounting edits or budget
increases. Whole-game cached replay is still required on restart; no direct
board-state continuation shortcut is implemented.

For an interrupted original-response import that already settled usage and cached the verified answer but lacks its final request-ledger entry, `scripts/recover_provider_responses.py --complete-interrupted-import` validates a repair without writing it. Supply the same config, agents root, output root, and fetched manifest as the original import; add `--apply` only after reviewing the validation. This requires a halted matching identity, a retained import transaction, exact original input/output and usage, recorded process-interruption evidence, and one missing final request. It generates no answer and does not increment provider calls.

Completion appends one provenance-bearing request row, preserves the exact prior ledger prefix and historical uncertain reserves, and reconciles against all dispatches. It leaves an unsaved local attempt index unspecified. Older transient HTTP failures keep their uncertain reserves only when a recorded resume led to a validated retry for that same decision. A durable intent and receipt prevent duplicate appends after another interruption. Changed history, unmatched usage, unrelated reservations, or an incomplete ledger line cause refusal. Normal resume is permitted only after reconciliation succeeds.

HTTP providers use a 600-second socket read timeout by default. `timeout_seconds` accepts an integer from 1 to 600 and preserves the configured value. Native Codex CLI keeps its existing 30-second default and configured subprocess timeout. A socket timeout means this client stopped waiting for the HTTP response; it does not prove the API failed to run or did not bill the request.

The default runtime decision limit is three requests total, including repairs, transport retries and explicitly configured alternatives. The opt-in repeated received-invalid policy above replaces that numerical correction ceiling with the finite shared total budgets; it does not permit automatic transport replacement. In strict recovery, a transport failure halts for reconciliation. The following transient retry and alternative-routing behavior describes the non-strict runtime: it retries the same provider, then advances to an explicitly configured alternative after repeated transient failure. Authentication, quota/credit exhaustion and other permanent HTTP errors skip retries on that provider. Quota codes are classified from the structured error body. Unknown exceptions stop recovery. Routes must be explicitly configured.

The runtime derives a finite decision window as the larger of 390 seconds or three times the largest configured route timeout plus 60 seconds for bounded backoff. Before each retry it checks that the selected provider timeout and delay fit in the remaining window. This check cannot cancel an in-flight synchronous request. Transient retries use bounded exponential delay, or a numeric `Retry-After` value capped at 30 seconds. A result is parsed and applied once after a route returns a valid response. Current HTTP request bodies send the full prompt each time and do not use `previous_response_id` or a conversation handle. Responses background creation, polling, cancellation and retention are not implemented here.

An ordered alternative list is opt-in and may be placed in a global `llm_provider`, per-agent `provider_config`, or population runtime provider config:

```yaml
llm_provider:
  kind: responses
  model: primary-model
  max_output_tokens: 1600
  timeout_seconds: 600
  pricing:
    input_cost_per_1k_tokens: 2.0
    output_cost_per_1k_tokens: 8.0
  fallback_providers:
    - kind: responses
      provider_name: alternate
      model: alternate-model
      max_output_tokens: 1600
      timeout_seconds: 600
      pricing:
        input_cost_per_1k_tokens: 1.0
        output_cost_per_1k_tokens: 4.0
```

Every request reserves budget exposure before dispatch. The input bound is the UTF-8 byte length of the actual prompt plus 256 tokens for request framing. This is a conservative bound for the current plain-text, tool-free request shape, not a general tokenizer guarantee. The output bound is the configured `max_output_tokens` only when the selected adapter sends that ceiling. With a monetary budget, an absent output ceiling or missing price rates require `budget.safe_reserve_usd_per_call` to supply an explicit positive reserve. An output-token budget denies admission when the selected adapter cannot enforce an output ceiling. If the request cannot be bounded, admission is denied. A caller-provided reserve never overrides a computable price-based exposure.

Known usage and estimated cost, pending/uncertain reservation exposure, and budget-admission totals are separate fields in `budget_state.json`. A timeout, malformed usage, or unknown result retains its reservation even when a later retry succeeds; malformed usage cannot lower known totals, and unknown actual totals stay unknown. A preflight-known unavailable route is recorded in decision metadata but does not consume billable-call or cost exposure. Reopening an experiment restores reservations only after validating required aggregates, reservation rows, numeric values, and identity/config fields. Incomplete, malformed, nonfinite, negative, or inconsistent state fails closed without rewriting the state file. Reusing the artifact directory with changed identity or budget configuration also fails closed. Estimates use configured rates and do not guarantee an exact vendor invoice or vendor-enforced spending cap; provider pricing, caching and service tiers can differ.

Each HTTP request sends its local attempt UUID in `X-Client-Request-Id`; each attempt gets a distinct ID. Per-attempt records keep that local ID, elapsed time, provider route metadata, failure category, and provider `x-request-id`/response ID when returned, including server IDs delivered on HTTP errors. The client ID is a support correlation aid, not an idempotency key or proof of exact charges for a timed-out request.

## Strict order correction

This section describes the default single-correction policy. The opt-in repeated received-invalid policy above can correct subsequent runtime or engine rejections, preserving each predecessor and all total guards.

In strict recovery mode, a deterministic order-legality rejection or missing required movement order can produce one corrective request to the offending LLM player. The correction includes the original answer, exact validator errors, and that player's legal-order catalogue. It asks for every required unit and limits choices to those legal options. Other players' accepted responses remain cached; adjudication waits until corrected orders pass validation. Empty retreat orders remain valid when the engine reports no legal retreat destination.

The corrective request is the same logical decision with a separately hashed actual prompt. Its durable intent binds the exact rejected response, rejection record, feedback, and correction-prompt hash. The original and corrected raw outputs and provider usage remain separate. The correction consumes the same three-request decision cap as every earlier attempt. A failed or still-invalid correction halts the phase; it does not authorize another correction or fallback. If its request outcome is unknown, retrieve that exact request using its corrective prompt hash through the existing provider-response recovery process. Previously reviewed unknown attempts remain uncertain and reserved; importing the correction does not retrieve or clear them. Never resend an unknown request automatically.

Resuming a run after this implementation change requires the narrow, hash-bound order-correction code approval chained from any prior relationship and retreat approvals. It preserves the original prompt/configuration identity and received responses; it does not approve other source changes or reinterpret prior invalid/skipped responses.

## Strict reflection correction

This section describes legacy typed reflections. New runs default to `free_text_reflection_v1`: exact prose, including empty/non-JSON output, is accepted without format correction. See `REFLECTION_MEMORY.md`. Typed cross-game cleanup remains separate.

In strict recovery mode, one parseable reflection rejected by the deterministic schema, lossless-normalization, or semantic checks may receive one corrective request. That prompt includes the rejected raw answer, exact validator issue, required `reflection_compact_v3_campaign_memory` schema and example, and the unchanged reflection context. It does not repair malformed JSON or provider, budget, or integrity failures. The corrected output must pass the same checks before any game-memory write; a failure halts with no fallback or second correction. Original and corrected responses have separate prompt hashes and raw-output files, and replay reuses both cached responses without repeating accepted provider calls. Unknown correction outcomes follow the existing exact-hash GET-recovery path, retaining any earlier uncertain reservation.

The reflection runtime and recovery-validator change requires a narrow code-identity approval chained after the relationship, retreat, and order-correction approvals. It binds the complete package manifests and preserves prior response, validation, skipped-attempt, snapshot, and journal history.

## Strict negotiation correction

This section describes the default single-correction policy. With `repeat_with_feedback_v1`, subsequent received-invalid responses can receive exact feedback under the same total budgets; malformed JSON is eligible too. Transport, budget and integrity failures remain halts.

In strict recovery mode, one parseable negotiation response rejected by deterministic schema, list-limit, or message-normalization checks may receive one corrective request for that same player and tick. The correction includes the original raw answer, exact validator issue, required fields and the existing `negotiate_tick` output example. It does not guess a missing recipient or add strategic advice. A response that would lose messages or relationship rows during normalization is rejected before negotiation effects are written. Lossless legacy aliases remain supported.

The corrected response must pass the same checks before messages, relationship updates, or beliefs are applied. Malformed JSON, provider, budget, and recovery-integrity failures still halt without correction. A second invalid response halts; the original and corrected requests share the three-request decision cap, keep separate raw outputs and prompt hashes, and replay from the journal without repeating accepted requests. GET recovery applies the same strict candidate checks before importing a corrective negotiation response.

Resuming the affected saved run requires a narrow negotiation-correction code approval chained after the relationship, retreat, order, and reflection approvals. It binds the complete package manifests and preserves prior prompt, response, validation, skipped-attempt, snapshot, and journal history.

## Saved input-budget increase

The following one-run migration and cleanup sections are historical operator records, not instructions to repeat consumed actions. `luna1912-1018-r2` completed all seven cleanups and population reports on 2026-10-28. Preserve its evidence; do not relaunch it or reapply these authorizations.

`scripts/raise_saved_input_budget.py` is an offline, one-run migration for the human-approved doubling of the luna1912-1018-r2 input cap from 19,919,602 to 39,839,204. It preserves the USD 74.75 guard, accounting, reserves, original responses and journal prefix. Default execution only preflights; `--apply` requires that explicit approval. The exact nondispatched Winter 1911 budget block is skipped once. An append-only identity receipt binds the config delta, recovery code, prior checkpoint and request ledger. Other config/source changes fail closed. Normal `--resume` follows successful migration; no inference runs in the migration.

# One-run fourth cleanup authorization

`scripts/authorize_fourth_cleanup.py` preflights the explicitly approved fourth Austria
cleanup request for `luna1912-1018-r2`; `--apply` appends its authorization under the
writer lock. It binds the rejected third attempt, original prompt, prior journal and
ledger prefixes, and exact source transition. Other decisions keep the three-request
cap; Austria's same cleanup prompt stops at four. Configuration, budget accounting,
and historical responses stay unchanged. Reapplying validates the existing receipt
without granting another request. This command does not launch or call a provider.

The later explicit approval for Austria's fifth and sixth paid cleanup passes is
handled by `scripts/authorize_cleanup_two_passes.py`. Preflight is offline; `--apply`
records the exact code/history/prompt binding and prepares pass five's feedback.
After a received-invalid fifth response, `--apply --pass-number 6` prepares feedback
from that fifth response. Each pass has its own durable prompt hash and ceiling;
pass five cannot silently spend pass six. Unknown transport outcomes still require
original-response reconciliation. All other decision caps and budget limits stay
unchanged. A sixth failure requires another human decision.
# Russia postgame cleanup authorization (2026-10-27)

The saved `luna1912-1018-r2` run has a separate human authorization for at most
two additional Russia cleanup requests (logical attempts three and four).
`scripts/authorize_russia_cleanup.py` prepares the exact received-invalid parent
feedback offline; `--apply --pass-number 3` records the authorization and first
feedback intent. Attempt four may be prepared with `--apply --pass-number 4`
only after attempt three returns a rejected received response and the writer has
exited with no request in flight. Each intent preserves the base context and adds
the exact JSON parser error, rejected raw text as escaped data, and a valid shape
example. Use the existing exact-parent `retry_invalid` action once per prepared
pass, then verify the first uncached dispatch matches the receipt's prompt hash.

This authorization does not permit a fifth Russia cleanup request, replacement
of an unknown transport outcome, changes to other decisions, or accounting edits.
It validates the prior Austria source transition with its original manifest and
records the new source identity append-only; historical checkpoint and response
bytes are preserved. Stop on success without spending the unused fourth pass.


England terminal cleanup has a separate, exact saved-run authorization: `scripts/authorize_england_cleanup.py --apply --pass-number 2` prepares the second request; pass 3 is conditional on a received-invalid second answer. Both bind the original visible context, exact rejected raw answer, parser error, and valid JSON example. Existing cap 3 and all budget guards remain binding. No fourth England request is authorized.


Italy cleanup authorization (2026-10-28): the saved luna1912-1018-r2 Italy/major game_end_cleanup decision permits three additional paid attempts, total ceiling four. `scripts/authorize_italy_cleanup.py` binds the source transition and creates exact-error/raw-response feedback for pass 2, then conditional passes 3 and 4 only after the preceding received-invalid answer. Success ends retries; unknown transport requires original-only recovery; no fifth attempt or changes to other decision caps. Historical responses, accounting, and reserves remain preserved.
