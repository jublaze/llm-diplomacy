# Outcomes, isolated providers, and operating-cost diagnostics

Implemented on `feature/reliability-provider-cost` at `4bb5ca1`, based on the independent provider feature `801b0bb`. Its prerequisite source snapshot `aae166f` matched the original checkout's implementation. On 2026-09-27, the user authorized controlled integration and `main` was fast-forwarded to `4bb5ca1`. Unrelated local changes, memory, experiment artifacts, and the other worktrees were preserved.

## Outcome contract

`run_summary.json` retains schema version 1.0 and adds `games_started`, `games_stopped`, `games_failed`, and `status`.

- `games_completed` now counts games that finished their configured schedule, not stopped games that merely returned from execution.
- `games_started = games_completed + games_stopped + games_failed` for a normally returned or caught-exception run.
- `status` is `failed` after a caught exception, `stopped` when games stop or the requested schedule is incomplete, otherwise `completed`.
- Exceptions still propagate after diagnostic summaries are written. A hard process kill may leave no summary; missing terminal evidence is not completion.
- `stop_experiment` and `stop_game_after_phase` produce stopped, partial game results. `switch_to_fallback` can complete the schedule and records `budget_fallback_used` on game results.
- Completion is not a solo-victory claim. Existing scoring and adjudication are unchanged.

Population summaries distinguish completed, stopped, and incomplete games. The exporter checks both summary status and terminal game evidence, including legacy summaries whose old counters included stopped games. Partial, stopped, failed, unknown, or missing terminal results are rejected with an input error. Historical known completed/solo/draw results remain readable. A cutoff year alone never converts unknown or partial evidence into completion. Export does not rewrite source artifacts.

Compatibility: consumers that used `games_completed` to mean attempted games should use `games_started` for new runs. Legacy runs are not backfilled. No artifact migration or live strategy change is included.

## Provider integration

The independent feature supports object-form player entries with separate instance IDs, source blueprints, and complete provider selections. Per-player selections do not inherit endpoint or credential settings from the global provider. All lifecycle methods and repairs use the selected provider; object-form preflight fails before folio mutation. Inherited global providers are included in reuse provenance, so changing the global model cannot silently reuse learned memory for a new comparison.

The seven-player offline acceptance fixture uses two copies of Cassandra with distinct fake models, plus five scripted players, over two rotating games. It checks equal souls, separate memories, all lifecycle routes, repairs, mixed prices, and reconciled totals. HTTP adapter tests mock both API styles and distinct endpoints/credential variables; they make no network requests.

Mixed-provider summary fields carry a mixed marker and null scalar model; `providers_by_agent` retains each configuration and all observed resolved models. Budget fallbacks are not observed selected-model responses. Export bundles preserve per-player configuration provenance, while postgame-public projections omit provider diagnostics.

Use dedicated output and agents roots with the existing `config/examples/per_player_2x2_offline_1901.yaml` example. No live comparison is part of this delivery.

## Cost diagnostics

New runs write:

- `metrics/provider_attempts.jsonl`: append-only records for requests, blocked attempts, LLM lifecycle completions, and eliminated skips. Contains identifiers, methods, models, usage, and error classes; no prompts, response bodies, or credential values.
- `metrics/provider_usage.json`: totals, per-agent, per-method, per-agent/method, repair usage, and post-elimination cleanup usage.
- `metrics/provider_usage.md`: a readable stage breakdown linked from the population summary.

Every request to a provider adapter counts once, including failed requests and repair attempts. Budget-blocked attempts do not count as provider requests. Fake-provider attempts are identified by their provider metadata; they are not paid inference. Legacy boot substitution retains explicit fake-provider provenance.

Missing usage or pricing makes the corresponding aggregate null. `known_*` subtotals remain lower bounds, accompanied by unknown-call counts. Failed calls without billing data remain unknown even when a later repair succeeds. Budget guards continue checking known lower bounds after an unknown call; unknown billing means a currency ceiling cannot be guaranteed.

`repair_usage` and `post_elimination_cleanup` are overlapping subsets of the totals, not extra charges. Skipped eliminated lifecycle invocations do not imply an exact number of avoided paid attempts or a dollar savings estimate. Legacy `cost_summary.json` remains lifecycle-based; actual request accounting is in `provider_usage.json`. Historical artifacts without the ledger are not silently reconstructed.

## Verification and release boundary

Offline regression and acceptance tests cover the implementation. No live API calls, model-quality claims, or realized savings claims are required. Run the full test suite and `scripts/security_scan.ps1` before committing. The controlled adoption described here is complete.

## Verified 2026-09-27

- Full offline suite: 318 passed, 2 skipped. The skips are the existing optional/external checks.
- Final focused outcome/provider/population suite after review: 36 passed.
- Repository security scan and `git diff --check`: passed.
- Initial test-environment issues were resolved with a short temporary directory and a junction to the existing Python environment; no runtime dependency was installed.
- At the 2026-09-27 verification, delivery remained local because publication had not been requested. That was the status at that historical closeout; later delivery follows current authorization.
- Main compatibility changes: completion counters exclude stopped games; missing usage remains null; changed inherited providers fail object-form memory reuse. No game rules or strategic prompts changed.

## Controlled integration recovery

Before integration, only the source paths being adopted were stashed. Other local changes remained in place. All 530 unrelated files in the tracked/untracked inventory matched their pre-integration SHA-256 hashes immediately after the fast-forward.

Recovery copies and a path/hash manifest are retained locally at `experiments/integration-backup-20260927-112319/`. The source-only recovery stash is `96bde13ddd150071907bb15d8ce4b50c5210c378`. Do not apply that stash wholesale: most of its code is now committed, while its old AGENTS/backlog instructions are superseded. Use the saved files for selective recovery if needed.

No provider inference, push, worktree deletion, memory reset, or historical-artifact rewrite was performed during that 2026-09-27 integration.

Integrated-main validation: **318 passed, 2 skipped**. Security scan passed. The 530 unrelated-file hashes also matched after testing.
