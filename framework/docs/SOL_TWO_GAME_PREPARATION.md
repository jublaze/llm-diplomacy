# Sol medium: two-child arm readiness

Preparation is not launch approval. The owner must initiate the new arm and approve
the model/provider route and the finite shared budget below. No historical run is
resumed or migrated. All generated run data stays under the ignored experiments/
directory. The viewer has a separate implementation and publication owner.

## Baseline and explicit differences

The seven stable player IDs, in power order, are birsan, dolos, metternich, shaver,
major, birsan_ii, major_ii. Game 1 assigns Austria, England, France, Germany, Italy,
Russia, Turkey in that order. This matches the recorded Terra first-game setup.
The five blueprint files match the recorded first-game hashes; Major instances use
schema 1.1_major_adaptive_counterweights, and the other profiles use schema 1.0.
Duplicate profile names do not merge stable player identities.

Both recorded Terra children used fixed assignments. The requested new game 2 is
randomized using the recorded Luna-successor shuffle seed 20261028. The runner uses
Python Random(seed) to shuffle the ordered player list, then zips it to power order.
The prepared arm-series.json records the exact mapping. Child 1 uses seed 20261016.
Both run through 1912 or a winner, with three negotiation ticks. Each child records
game_001; the composite identity is experiment_id + "--game_001". A series reader
must not collapse those two games.

The requested route is gpt-6.1-sol, medium reasoning, Standard service_tier=default,
explicit-only prompt caching, no fallback route, max_output_tokens=32000 and HTTP
timeout 600 seconds. Existing profile defaults are overridden explicitly. Official
capability/pricing reference checked 2026-11-01:
https://developers.openai.com/api/docs/models/gpt-6.1-sol
Standard short-context uncached estimates are $2/M input and $10/M output. Actual
account access is unverified. Cached/write, regional, long-context and other tiers
have different billing; configured estimates are not exact provider billing.

Proposed shared limits, requiring new owner approval: USD350, 6968 provider calls,
79678408 input tokens, 222976000 output tokens and 518400 seconds. USD350 is a new
proposal reflecting Sol's higher token prices, not an inherited Terra approval.
The second child receives the remainder after known and uncertain admission
exposure, with an absolute deadline anchored to child 1's creation. No counter reset
or reserve clearing is allowed. A larger budget or later deadline needs new approval.

## Commands and supervision

Use a Python environment with this framework's dependencies, from framework/ in the
isolated runner checkout. Keep that checkout/source sealed for the full arm.

1. `python scripts/prepare_sol_two_games.py prepare --experiment-id sol61-medium-1101`
   creates experiments/sol61-medium-1101-g1/prepared.yaml, second-template.yaml,
   preparation.json and arm-series.json. It does not create live state or dispatch.
2. After explicit owner initiation and approval, the supervisor may run:
   `python scripts/prepare_sol_two_games.py launch --run-root experiments/sol61-medium-1101-g1 --owner-reference OWNER_APPROVAL_REFERENCE --approve-total-usd 350 --approve-live-sol-medium`
   The wrapper checks the sealed code/config/template, writes launch.yaml,
   owner-initiation.json and arm-policy.json, and invokes the existing runner. A
   repeated initial launch is refused. Use a reviewed `--resume` only after inspecting
   the actual current state; the wrapper does not authorize unknown replacement.
3. When child 1 reaches population_reports_completed, preserve its logs and verify
   no unresolved request, pending commit, or exact writer. First use Get-Process
   python*, then bounded per-PID command/creation inspection with escalated visibility.
   Never use an unfiltered CIM inventory or infer absence from a sandbox-only view.
   Record a fresh proof JSON with verified_utc, source_experiment_id, exact_writers=[],
   escalated_bounded_inventory_verified=true. This is a supervisor assertion, not a
   self-generated process guarantee. It must be no more than 120 seconds old.
4. Run prepare_sol_second_game.py with --policy child1/arm-policy.json,
   --template child1/second-template.yaml, --source child1, --destination child2,
   --no-writer-proof PROOF, --expected-code-sha256 CODE,
   --expected-entrypoint-sha256 ENTRY and --expected-wrapper-sha256 WRAPPER.
   CODE/ENTRY/WRAPPER come from the sealed arm-series.json. The tool validates
   checkpoint/accounting and copies exact prepared folios to a new sibling child.
   A partial handoff directory requires review; never overwrite it or rerun blindly.
5. Under the initial approval for the complete arm, verify the handoff receipt and
   start child 2 with scripts/run_sealed_population.py and the same CODE/ENTRY pins,
   --prompt-checkpoint-backup child1/recovery/checkpoint.json,
   --prompt-checkpoint-sha256 SHA256_OF_THAT_FILE, --config child2/launch.yaml,
   --output-root . and --agents-root child2/agents. The handoff verifies WRAPPER too.
   Add --resume only for reviewed continuation of that child. Keep distinct hidden
   supervisor stdout/stderr/exit receipts and exact PID/UTC-creation records.

Check status with run_population_experiment.py --config CHILD/launch.yaml
--output-root . --agents-root CHILD/agents --status. Cooperative interruption uses
--request-halt and a recorded reason. Inspect writer ownership before every launch.
The runner's OS lock prevents a second writer; supervision must also verify process
identity. No arbitrary deadline applies to an original provider job. Unknown
transport outcomes require original-only recovery and exact response/usage evidence;
never automatically retry_unknown. Credentials, exhausted budgets, integrity mismatch
or ambiguous process loss are review blockers. Received-invalid correction uses
the existing exact-error/raw feedback policy within the shared limits. Accepted
decisions must never be regenerated and invalid memory must never be adopted.

## Memory and offline evidence

Child 1 starts fresh. Its completed cleanup promotes distilled lessons, labeled by
source experiment, source game and power, into the prepared persistent folio.
Child 2 reuses exact manifest/soul/profile/persistent-memory bytes; its game-specific
board and relationships start anew. Persistent memory is historical evidence, not
an active promise or current board fact. The existing 3500-character prompt bound
still applies and any clipping must be reported honestly.

Issue #19 reproduced a copy/input discrepancy: strict recovery previously ignored
the copied canonical folios while creating its empty stage. The new-run fix seeds
inherited folios before preparation and the initial snapshot, including a resumed
bootstrap with no saved snapshot or provider attempts. Later replay uses the
snapshot instead of refreshing outside memory. Historical records are not rewritten.

tests/test_sol_two_games.py runs two real strict-recovery single-game children with
mocked transport and complete mocked usage receipts. It checks fixed/shuffled
assignments, shared-limit deduction, source/copy memory hashes, all seven actual
child-two prompts, interrupted bootstrap/resume, unchanged parent memory, and zero
new requests on terminal resume. The test uses 1901 and one negotiation tick for
speed; it does not establish a completed live 1912 run or provider account access.
Launch-gate tests forbid unapproved dispatch and reject changed sealed inputs.

Readiness validation (2026-11-01): 75 changed-scope and adjacent offline tests
passed, plus an ordered 11-test check ending with interruption/recovery replay.
Security scan passed; fresh preparation dry-run executed zero provider calls and
all 110 sealed input hashes matched. An earlier resume identity failure did not
recur in isolated or ordered reruns; its cause is unproven. Six stale legacy Terra
handoff fixtures fail on unchanged main and are tracked separately in issue #20.

## Completion/export contract for the viewer owner

Both children must have a matching-source valid terminal checkpoint at
population_reports_completed, no halt/inflight/pending commit, and completed
population_summary.json, run_summary.json and games/game_001/. Validate accounting
separately against actual requests and preserve uncertain reserves.

Run `python scripts/verify_sol_two_games.py --first CHILD1 --second CHILD2 --arm-id ARM --output NEW_SERIES_JSON`.
It checks terminal identities/journals, the handoff and saved incoming memory, and
the persistent-memory section of retained opening prompt output. It emits grouping,
actual assignments, source/checkpoint/journal/ledger hashes and player memory input,
presented-input, outgoing and prompt hashes. It rejects changed/missing inputs; it
does not claim that every raw memory byte bypassed the existing clipping policy.

Export each completed child using scripts/export_report_bundle.py --experiment-root
CHILD --output-root NEW_LOCAL_EXPORT --all-games --audience omniscient_postgame.
These are local review artifacts. Give the separate viewer owner the series receipt,
child roots, normalized report bundles, handoff receipt and hashes; preserve stable
composite IDs and player IDs. Publication approval and deployed freshness checks
belong to that pipeline. Do not upload raw checkpoints, prompts, ledgers, credentials
or operational histories as a substitute for a reviewed normalized export.
