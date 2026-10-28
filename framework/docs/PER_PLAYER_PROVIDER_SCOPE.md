# Per-player provider configuration

Status: implemented on `feature/per-player-providers`, 2026-09-17. Offline routing and shared-soul validation passed; see [the 2×2 smoke record](PER_PLAYER_2X2_OFFLINE_SMOKE.md).

The independent feature was hardened on `feature/reliability-provider-cost` and integrated into `main` on 2026-09-27; see [current outcome and usage contracts](RELIABILITY_PROVIDER_COST.md). Unrelated local changes and experiment state were preserved.

## Delivery contract

Deliver one feature branch implementing this document, with offline validation and a reviewable diff against the captured starting checkout. The implementation worker used `gpt-5.6-terra` with `high` reasoning.

Required deliverables:

- Backward-compatible player configuration, separate instances of a shared soul, per-player routing, and accurate provider/usage provenance.
- One runnable seven-player comparison example, using two instances of the same blueprint and five other players. Document a dedicated agents root and dry-run command.
- Acceptance tests below, updated user/factory documentation, and a final report listing changed files, validation results, limitations, and push decision.

Implement only the included behavior. Do not revise strategic instructions, souls, game rules, negotiation timing, or historical experiment artifacts.

## Outcome

Run two distinct players in the same game using identical soul content and different models or API endpoints. Each player has independent memory, messages, results, usage, and provider diagnostics. Provider selection remains fixed across all lifecycle methods and games in the experiment.

## Configuration contract

Extend the population `agents` list to accept existing blueprint-path strings and player objects:

```yaml
agents:
  - agent_id: cassandra_a
    blueprint: blueprints/cassandra.yaml
    provider_config:
      model_profile: openai_gpt_5_5
      max_output_tokens: 1200
      timeout_seconds: 120
  - agent_id: cassandra_b
    blueprint: blueprints/cassandra.yaml
    provider_config:
      model_profile: xai_grok_4_5
      max_output_tokens: 1200
      timeout_seconds: 120
  - blueprints/dorian.yaml
```

This illustrates the new agent entries, not a complete experiment config. Existing profiles are examples; profile support is resolved from local files at run time.

- A string entry keeps the blueprint's agent ID and existing behavior.
- An object requires `agent_id` and `blueprint`; `provider_config` is optional.
- The effective agent ID is the unique player instance ID everywhere downstream. Retain the source blueprint's agent ID separately as provenance.
- A player without `provider_config` uses the current experiment-wide provider selection and precedence.
- A player with `provider_config` supplies a complete provider selection, resolved by the existing parser and model-profile resolver. Do not merge it with the global provider: endpoint, API style, credentials, capability flags, and pricing must not leak between providers.
- An explicit selection must name `model_profile` or `kind`. A real provider without a profile must name a non-empty model; validate Responses selections as well as chat-completions selections. Empty objects, conflicting kind/API-style selections, unsupported kinds, and credential values supplied inline are invalid.
- Explicit configs support the existing fields: provider kind, model, endpoint/base URL, API style, credential environment-variable name, reasoning settings, temperature, output limit, timeout, and pricing. No new API adapters.
- `runtime.llm_agent_ids`, when supplied, refers to effective player IDs. Provider config on a scripted player is rejected rather than silently ignored.
- Normalize and validate all entries before creating, deleting, or reusing folios. Reject duplicate or unsafe IDs, malformed entries, unknown selected IDs, and invalid provider configurations.

The lower-level experiment runner accepts the same per-player `provider_config` on its existing `agents` objects; it does not generate blueprints. Presence of a per-player provider config enables the same strict boot preflight used by the new population player-object form. Record this mode explicitly when building the lower-level config so alias-only population objects also get strict preflight.

Represent normalized population players in one typed record containing effective ID, blueprint path, and optional provider config. Retain whether an entry used the new object form. Keep provider normalization in one shared resolver used by preflight, execution, and reporting; do not add a provider registry or a second set of adapter defaults.

## Identity and memory

Generate separate folios under `<agents_root>/<effective_agent_id>`. The factory accepts an optional instance ID without changing the blueprint's soul or public-profile rendering. Thus two instances of one blueprint have byte-identical `soul.md` content, even though their manifests and memory headers differ.

Record effective agent ID, source blueprint ID/path/hash, and actual soul hash. Distinct IDs also keep inboxes, phase outputs, cleanup, leaderboard entries, and reputation records separate. Retain the source display name; player IDs disambiguate records without adding model names to soul content.

Existing fresh/reuse semantics remain scoped to the effective player ID. For new object entries, reuse must verify matching source/soul provenance and effective provider configuration before live calls. A mismatch fails with instructions to use fresh state or another player ID; it must not silently carry learned memory into a different model condition. Existing string-entry reuse behavior remains compatible.

Compare blueprint content hash and rendered soul hash, not absolute source paths. Compare a canonical resolved provider configuration including credential environment-variable name, capabilities, limits, and pricing; never hash or store credential values. Missing provenance in an existing folio cannot verify a new object-form reuse and must fail with the same recovery instructions. This guard applies to these comparison instances; it does not redefine the existing agent-identity continuity principle or migrate legacy folios.

Use a dedicated agents root for comparison examples. Neither instance writes persistent memory back to the source blueprint identity's folio.

## Runtime routing and failures

Build a runtime/provider map keyed by effective agent ID for LLM players. Each runtime owns that player's provider and metadata; existing shared prompt assets and lifecycle logic remain unchanged. Route negotiation, orders, reflection, outcome review, and game-end cleanup through the map. Internal repair/retry calls use the same provider.

Wrap each provider with the existing budget wrapper and its own pricing, sharing one experiment-level budget controller. Preserve existing stop policies and elimination scheduling; do not introduce per-player budget limits.

For the new player-object configuration, validate real-provider credentials and initialize all selected LLM providers before the first live request. A boot failure identifies the player and stops the experiment; never replace a failed real provider with a fake model. Dry run validates config/provenance and reports planned routing without requiring credentials or making requests.

Keep existing per-call error/parse fallbacks and systemic-failure policy. Attribute each to the correct player/provider and expose fallback counts in summaries. Do not add a new retry or transient-failure policy in this change. Preserve legacy single-provider boot behavior for existing string-only configurations.

## Artifacts and reporting

- Add `providers_by_agent` to manifests, dry-run output, population summaries, pilot reports, and completed-game results. Store requested provider/model/profile and observed resolved model values per player; do not infer a whole experiment's model from the first output file.
- Add `provider_mode: single | mixed` based on canonical effective provider settings. Preserve legacy single-provider fields for homogeneous runs. In mixed runs, legacy provider-object fields contain an explicit mixed marker, and legacy scalar model/provider fields are null; `providers_by_agent` is authoritative. Record all observed resolved models per player when more than one is returned; do not pick one arbitrarily.
- Per-player summaries include provider calls, input/output tokens, estimated cost, errors, and fallbacks. Count retries as provider requests; retain existing lifecycle usage summaries where needed for compatibility.
- Calculate cost using each player's pricing even when no budget is configured. Missing usage/pricing remains unknown, not zero. Reconcile available per-player totals with the experiment totals.
- Carry safe per-player model provenance into exported report bundles. Keep existing bundle version and projection rules; projected reader reports continue to omit provider/runtime diagnostics. No viewer application work.

Use one provider-call accounting path for actual attempts, with or without a budget. Lifecycle invocations and provider requests are distinct counts; blocked calls and deterministic fallbacks are not requests to the selected model. Do not double count wrapper records and runtime outputs. Keep successful-call cost with that player's pricing and failed-call billing unknown when the provider does not return usage.

Unknown values must remain unknown in both per-player and aggregate totals. In particular, the existing budget controller must tolerate a known usage value after an earlier unknown value without assertions or resetting the unknown total. Fix this bounded accounting case if touched; avoid a general metrics redesign.

## Implementation slices

| Slice | Files and responsibility |
| --- | --- |
| 1. Normalize and validate | `population/config.py`, `population/runner.py`, `config/loader.py`: player entries, effective IDs, provider resolution, preflight before folio mutation. |
| 2. Instantiate and isolate | `factory/generator.py`, factory validation if needed: instance IDs, source/soul provenance, fresh/reuse guards. |
| 3. Route calls | `runner/scripted_experiment_runner.py`: provider/runtime map, registration, lifecycle dispatch, player-specific boot errors and metadata. |
| 4. Account and report | `agents/llm_provider.py`, `metrics/cost.py`, population summaries, report exporter: pricing without budgets, actual calls, per-player provider metadata and unknown values. Inspect budget/runtime code for minimal compatibility changes. |
| 5. Document and verify | Config examples, README, factory docs, focused tests, security scan, and push decision. |

Complete these as one feature. Per-player endpoints alone would leave the shared-soul use case dependent on duplicate blueprint files and collision-prone identities.

## Acceptance checks

1. Legacy string-only population configs and direct-runner single-provider configs retain their behavior and output fields.
2. Two players generated from one blueprint have identical soul hashes, distinct manifests/directories, and independent messages and memory.
3. A full seven-player offline game includes two instances of one soul on different fake models and five other players. Spy providers prove correct routing for every lifecycle method, including cleanup and repair attempts. Use seven players because a two-player runner fixture can assign multiple powers to each player.
4. A two-game fixture proves each instance receives only its own promoted persistent memory after country rotation. Reuse provenance/provider mismatches fail before live requests.
5. Mock HTTP calls prove different endpoints, API styles, models, and credential environment variables route correctly without recording credential values.
6. Mixed pricing, retries, provider errors, missing usage/pricing, aggregate budget stopping, and fallback accounting remain attributable and reconcile where values are known.
7. Duplicate/unsafe IDs, unknown profiles, invalid provider configs, and provider configs on scripted players fail before folio mutation. Missing credentials fail before any live call; dry run needs no credentials and makes zero requests. Cover both population objects and direct-runner per-player selections.
8. Exported bundles retain both players and their model provenance; projected reports retain their existing diagnostics exclusions.

Run focused config/factory/provider/population/runtime/metrics/report tests, then the full test suite and `scripts/security_scan.ps1`. A bounded live smoke is a separate, explicitly requested run; it is not required to validate the offline implementation.

## Worker branch and handoff

Observed starting checkout: `main`, HEAD `fa1eb97` (initial commit), approximately 100 modified or untracked paths. This is context, not a clean baseline; recheck at dispatch.

1. Create a separate worktree/task using the current saved working-tree state, including the uncommitted implementation this feature depends on. Do not start from committed `main` alone. Proposed feature branch name: `feature/per-player-providers`; choose/confirm it at dispatch.
2. Verify the worktree contains this scope, current provider/model-profile code, current population/runtime code, security scan, and their tests. Explicitly check required untracked files made it into the snapshot. If they did not, transfer only the needed saved files and verify their hashes. Do not copy raw experiments or personal runtime memory as feature inputs.
3. Capture starting status and hashes/diffs for the files the worker will touch. Treat all existing work as baseline; do not reset, stash, remove, or bulk-stage it. Leave the original checkout and any live experiment processes untouched.
4. Implement the five slices and acceptance checks. Record baseline test failures separately from regressions; do not expand into unrelated backlog work to make the suite green.
5. Run required validation and security scan. Stage only intentional feature changes. If the snapshot makes unrelated baseline changes appear in the feature's review/commit diff, repair the branch baseline before committing; do not bundle them into the feature commit.
6. Report the branch, captured baseline, file changes, tests, security result, and push decision. A local feature commit is part of delivery once safe; push, PR publication, and merge require a later explicit instruction.

Worker task text:

> Implement `docs/PER_PLAYER_PROVIDER_SCOPE.md` in an isolated feature worktree based on the current saved checkout, including required uncommitted code. Follow repository AGENTS.md. Verify the snapshot and capture the starting baseline before editing. Preserve all unrelated work and active experiments. Deliver the complete included feature, seven-player example, acceptance tests, documentation, and a focused local commit if safe. Run focused and full tests plus scripts/security_scan.ps1; distinguish baseline failures. Report branch, diff, validation, risks, and push decision. Do not make live API calls, push, publish a PR, or merge.

## Outside this change

Per-phase model selection, midgame switching, new provider SDKs/protocols, concurrency, per-player budget policies, shared learned memory, automated model benchmarking, statistical comparison design, viewer UI changes, and retroactive changes to existing experiment artifacts.

Same soul does not equal identical game conditions: powers, observations, and interactions differ. This feature enables comparisons; it does not establish a model-effect conclusion.
