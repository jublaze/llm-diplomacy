# Current orchestration ownership

Verified against main on 2026-09-27. This describes implemented ownership; it does not propose a refactor.

| Component | Current responsibility |
| --- | --- |
| `population/runner.py` | Prepare player folios and provider routing, construct experiment config, invoke the scripted runner, aggregate population reports and provenance |
| `runner/scripted_experiment_runner.py` | Schedule games, years and phases; call agents; enforce budgets and elimination scheduling; invoke deterministic adjudication; write phase artifacts, summaries and reports |
| `gm/game_master.py` | Construct game paths and stores; expose lifecycle events, snapshot commits, staged message delivery and end-game cleanup |
| `rules/diplomacy` | Deterministic initial state, order validation and phase resolution |
| `runner/experiment_runner.py` | Earlier deterministic scaffold with synthetic results and zero usage; not the full gameplay execution path |

For a board phase, the scripted runner emits phase start, obtains orders, invokes rule validation and resolution, writes the named snapshot through `gm.snapshot_store`, records its event through `gm.event_log`, writes order/resolution artifacts, performs reflection unless a completed Fall phase ends the game by solo, then calls `gm.complete_phase` with the snapshot reference and phase. Terminal completion supplies the game result. GameMaster does not independently advance the season or adjudicate orders.

The runner directly uses GameMaster stores as well as its methods. These writes are sequential, not a transaction: a crash can leave partial artifacts. Completion evidence and export validation must continue to reject incomplete games. Moving writes behind a new facade or adding transactional recovery would be separate work with failure-injection tests.

Cleanup persistence belongs to GameMaster's lifecycle store; the runner requests it once after the result is recorded. A genuine solo is decided per power from final Fall ownership after retreats; cutoff leaders remain separate from winners. Final Fall ownership is applied before that phase's snapshot and resolution artifact are written, so the exported terminal board and result share one state.

Acceptance: document actual call ownership; remove obsolete phase-engine TODO claims; preserve runtime behavior. The map checklist now includes `bot` (Gulf of Bothnia), matching `maps/standard.json`; map topology is unchanged. Validation uses existing map and runner/lifecycle tests, plus a checklist-to-map ID comparison. No inference required.

Validation: 30 existing map, runner and lifecycle tests passed; all 75 province IDs match the spec; 370 protected files unchanged; security scan passed.
