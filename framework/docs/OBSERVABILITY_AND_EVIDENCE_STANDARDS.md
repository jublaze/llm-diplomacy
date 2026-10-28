# Observability and Evidence Standards

## 1. Purpose
This document defines the canonical observability vocabulary and evidence standards for `llm-diplomacy`.

It exists to keep experiment analysis rigorous and comparable across runs.

Core interpretation rules:
- Game Master and rule-engine artifacts are authoritative for game state/adjudication.
- Agent messages are evidence of what agents said, not proof of what they intended.
- Reflections and memory artifacts are subjective agent outputs.
- Observatory/reporter outputs are derived analyses, not authoritative state.
- Heuristic metrics are useful signals, not ground truth.

## 2. Artifact Authority Levels
| Authority Level | Definition | Examples |
| --- | --- | --- |
| Authoritative game facts | Canonical game truth produced by execution/adjudication | `games/<game_id>/state/snapshots/*.json`, `games/<game_id>/phases/*/orders/submitted_orders.json`, `games/<game_id>/phases/*/orders/validated_orders.json`, `games/<game_id>/phases/*/resolution/resolution.json`, `games/<game_id>/logs/events.jsonl` |
| Agent-authored subjective artifacts | What an agent said/believed/reported | `messages/global/global.jsonl`, `messages/private/*/inbox.jsonl`, `phases/*/reflection/*.json`, `agents/<agent_id>/folio/game_memory/*.json`, `persistent_memory.md`, `public_profile.md`, runtime parsed payload fields like `relationship_analysis`, `order_rationale` |
| Runtime-derived metadata | Execution diagnostics and safety metadata | `agents/<agent_id>/runtime/phase_outputs/*.json` (`fallback_applied`, `parse_error`, retries, grounding/validation), `metrics/budget/budget_state.json`, `experiments/<id>/logs/experiment_events.jsonl` |
| Reporter-derived analysis | Read-only narrative and synthesis | `observatory/reports/*.md`, `observatory/reports/reporter_summary.json`, `games/<game_id>/reporter/secret_history.{md,json}` |
| Heuristic observability metrics | Pattern detection/statistics over artifacts | `observatory/diplomacy_metrics.json` (`conversation_quality_heuristics`), `observatory/strategic_consistency.json` (`stagnation_loop_candidates`, `broken_commitment_candidates`) |

Warning:
- A derived/heuristic artifact may guide analysis but must not override authoritative game facts.

## 3. Event Taxonomy
This project has two event streams:
- Game lifecycle stream (authoritative game events): `games/<game_id>/logs/events.jsonl` via `EventLog`/`GameMaster`.
- Experiment trace stream (observability events): `experiments/<experiment_id>/logs/experiment_events.jsonl` via `ExperimentLogger` and runner emissions.

### 3.1 Authoritative game event types (`GameMaster` / `EventLog`)
| Event | Purpose | Emitted by | Typical Fields | Authority |
| --- | --- | --- | --- | --- |
| `game_initialized` | Initial snapshot created | `GameMaster.initialize_game_state` | `snapshot_id` | Authoritative |
| `game_started` | Game lifecycle start | `GameMaster.start_game` | empty payload | Authoritative |
| `phase_started` | Phase start | `GameMaster.start_phase` | `phase_id` | Authoritative |
| `phase_completed` | Phase completion/outcome | `GameMaster.complete_phase` | `phase_id`, `result` payload (including game-end result) | Authoritative |
| `snapshot_committed` | Snapshot commit recorded | `GameMaster.commit_snapshot` or runner snapshot append | `snapshot_id` | Authoritative |
| `negotiation_tick_staged` | Tick outputs staged for an agent | `GameMaster.stage_negotiation_tick_outputs` | `tick_index`, `agent_id`, `staged_count` | Authoritative |
| `negotiation_tick_committed` | Tick barrier satisfied and messages released | `GameMaster.commit_negotiation_tick` | `tick_index`, `released_count` | Authoritative |
| `game_end_cleanup_applied` | Cleanup payload application summary | `GameMaster.apply_game_end_cleanup` | `applied_count`, `skipped_count` | Authoritative |

### 3.2 Experiment trace event types (`ScriptedExperimentRunner` + `ExperimentLogger`)
| Event | Purpose | Typical Fields | Authority | Notes |
| --- | --- | --- | --- | --- |
| `experiment_started` | Experiment lifecycle start | `games_requested`, `max_years`, `negotiation_ticks` | Runtime metadata | |
| `experiment_completed` | Experiment finished normally | `completed_games`, `games_requested` | Runtime metadata | |
| `experiment_stopped` | Experiment stopped by guardrail/budget | `reason`, `stop_policy`, `completed_games` | Runtime metadata | Distinct from completed |
| `experiment_error` | Experiment-level failure | `error`, optional context | Runtime metadata | |
| `game_started` | Game start with assignment | `game_id`, `game_index`, `assignments` | Runtime metadata | |
| `game_completed` | Game ended normally | `game_id`, `outcome`, `winners` | Runtime metadata | Not emitted for budget-stopped game |
| `game_stopped` | Game stopped early | `game_id`, `reason`, `stop_policy` | Runtime metadata | |
| `game_error` | Game-level failure | `game_id`, `error` | Runtime metadata | |
| `game_summary` | End-of-game operational summary | leader, messages, fallbacks, parse failures | Reporter-derived runtime summary | Observational |
| `phase_started` | Runner phase start | `game_id`, `phase_id`, `phase` | Runtime metadata | Includes negotiation phases |
| `phase_resolved` | Orders resolved/adjudicated | `valid_orders`, `invalid_orders`, artifact refs | Runtime metadata | |
| `phase_completed` | Runner phase complete | `phase_id`, `phase`, snapshot id/path | Runtime metadata | |
| `agent_invocation_started` | Agent method invocation start | `agent_id`, `country`, `runtime_kind`, `method`, `tick` | Runtime metadata | |
| `agent_invocation_completed` | Agent method completed | fallback/parse/repair flags, usage, artifacts, optional prompt metadata | Runtime metadata | For LLM includes prompt diagnostics |
| `agent_invocation_failed` | Agent method error | `error`, agent/method context | Runtime metadata | |
| `messages_generated` | Negotiation output summary | `generated_count`, `dropped_count`, summaries | Heuristic/runtime metadata | |
| `message_committed` | Messages released after tick barrier | `released_count`, `released_global`, `released_private`, artifact refs | Runtime metadata | |
| `message_dropped` | Message rejected before commit | `reason`, `to_agent_id`, context | Runtime metadata | Reasons include `self_message`, `invalid_recipient`, `empty_body`, `schema_invalid`, `other` |
| `orders_generated` | Submitted/effective order summary | counts, `order_summaries` | Runtime metadata | |
| `orders_validated` | Validation output summary | counts and/or `invalid_reasons` | Runtime metadata | Emitted at both per-agent and per-phase levels |
| `orders_repaired` | Repair attempt metadata | `repair_attempts`, `repair_success` | Runtime metadata | |
| `orders_fallback_applied` | Fallback used for orders | `invalid_reasons` | Runtime metadata | |
| `reflection_generated` | Reflection summary emitted | `reflection_summary` | Runtime metadata | |
| `reflection_validated` | Reflection validation status | fallback/parse flags | Runtime metadata | |
| `reflection_repaired` | Reflection repair attempt | `repair_attempts`, `repair_success` | Runtime metadata | |
| `reflection_fallback_applied` | Reflection fallback metadata | parse/validation categories, truncation/repair flags | Runtime metadata | |
| `relationship_snapshot` | Negotiation relationship summary | `relationships` | Heuristic/runtime metadata | |
| `board_resolution_summary` | Per-phase board consequence summary | moves/retreats/adjustments/center changes | Derived from authoritative artifacts | |
| `budget_state` | Current budget totals | provider calls, token totals, cost estimate, stop state | Runtime metadata | |
| `budget_warning` | Threshold nearing limit | metric, usage, limit, ratio | Runtime metadata | |
| `budget_milestone` | 10/25/50/75/90/95/100% milestones | metric, milestone, usage/limit | Runtime metadata | |
| `budget_stop_triggered` | Stop condition reached | stop reason/policy + totals | Runtime metadata | |
| `prompt_assets_loaded` | Canonical prompt asset metadata | paths, existence, lengths, hashes | Runtime metadata | LLM runtime only |
| `prompt_check` | Final prompt concept checks | hash, size/tokens, checks, assets, artifact path | Runtime metadata | Must apply to final prompt text sent |
| `observatory_generated` | Observatory output generated | `observatory_path` | Runtime metadata | |

## 4. Outcome State Semantics
Canonical terms in current implementation:

- `result: "completed"`: game ended normally under runner termination path (including configured cap completion).
- `result: "stopped"` + `partial: true`: game stopped early by guardrail/budget; includes `stop_reason`.
- `solo_winner`: reporter interpretation only when one winner has `>= 18` centers.
- `leader_at_cap`: reporter interpretation when no solo threshold reached.
- `eliminated` / `survived`: represented by authoritative center/unit state and country results; not a dedicated top-level result enum.
- `error`: represented by lifecycle error events (`game_error`, `experiment_error`), not a standard game-result string in `country_results`.

Examples:
```json
{
  "result": "completed",
  "winners": [],
  "country_results": {}
}
```

```json
{
  "result": "stopped",
  "partial": true,
  "stop_reason": "max_input_tokens",
  "winners": [],
  "country_results": {}
}
```

Rules:
- `leader_at_cap` is not a solo win.
- Budget/guardrail stops must be treated as `stopped`/`partial`, not completed.
- Capped completion and budget stop are distinct terminal modes.

## 5. Claim Evidence Classes
| Class | Definition | Example |
| --- | --- | --- |
| Observed fact | Directly established by authoritative artifact | “England ordered F NTH -> BEL.” |
| Direct quote / message claim | What an agent said in a message artifact | “England said it would respect a border.” |
| Agent belief | Reflection/memory belief content | “France believed England was reliable.” |
| Agent stated intention | Reflection/rationale/relationship-analysis plan | “Austria intended to seek support in Bulgaria.” |
| Runtime inference | Runtime validation/repair/fallback interpretation | “Output required repair before acceptance.” |
| Reporter inference | Narrative interpretation over multiple artifacts | “This looks like boundary tension.” |
| Heuristic candidate | Pattern candidate from heuristic rules | “Possible stagnation loop candidate.” |
| Unsupported | Insufficient evidence; claim should not be made | “They exchanged many messages, therefore alliance.” |

## 6. Confidence Levels
- `high`: direct authoritative fact or multiple independent artifacts align with minimal interpretation.
- `medium`: good evidence exists, but interpretation is still required.
- `low`: weak/incomplete evidence; lead only.
- `unsupported`: claim must not be made.

Examples:
- High: center ownership change from resolution/snapshot artifacts.
- Medium: message assurance followed by contradictory move with consequence.
- Low: repetitive reassuring language suggests possible stagnation.
- Unsupported: “many messages == alliance.”

## 7. Alliance / Pact / Relationship Labels
Relationship labels must be conservative:

| Label | Minimum Evidence |
| --- | --- |
| contact | At least one bilateral exchange |
| active communication | Repeated bilateral exchanges |
| cordial relationship | Repeated non-hostile exchanges |
| non-aggression understanding | Specific non-attack/DMZ/border language |
| tactical cooperation | Concrete support/coordination proposals and compatible actions |
| durable alliance candidate | Multi-phase communication plus repeated tactical compatibility |
| confirmed alliance | Use rarely; requires explicit alliance language or sustained concrete cooperation |
| stagnation_loop_candidate | High volume + repeated reassurance + limited concrete asks + limited consequences |
| hostile relationship | Direct conflicting orders/attacks with little cooperative evidence |

Prohibited shortcut:
- Do not label alliance from message count alone.

## 8. Betrayal / Broken Commitment Standards
Canonical classification ladder:
- `ordinary_attack`
- `contradiction`
- `boundary_tension`
- `possible_broken_commitment`
- `plausible_betrayal`
- `confirmed_betrayal` (very rare)

Conservative thresholds:
- `ordinary_attack`: attack without relevant prior promise.
- `contradiction`: language/action tension without clear promise or consequence.
- `boundary_tension`: move pressures sensitive area after stability language, but pact violation is ambiguous.
- `possible_broken_commitment`: relevant assurance + inconsistent action, evidence incomplete.
- `plausible_betrayal`: explicit/near-explicit commitment + contradictory order + material board consequence + matching relationship/theater.
- `confirmed_betrayal`: very strong explicit commitment and unambiguous contradictory action with corroborating intent evidence.

Important constraint:
- Do not infer betrayal from cordial tone, high message volume, or generic cooperation language alone.

## 9. Conversation-Quality Metric Semantics
Current metric keys (from `conversation_quality_heuristics` and strategic consistency heuristics):
- `concrete_support_requests`
- `concrete_support_offers`
- `orders_with_specific_provinces_mentioned`
- `conditional_bargains`
- `third_party_claims`
- `rumor_or_warning_events`
- `claim_tests`
- `explicit_red_lines`
- `explicit_dmz_or_border_terms`
- `relationship_stagnation_markers`
- `repeated_confirmation_loops`
- `broken_commitment_candidates`
- `messages_with_question_marks`
- `messages_with_conditional_language`
- `messages_with_specific_orders_or_provinces`
- `messages_with_third_party_information`
- `messages_with_support_requests`
- `messages_with_support_offers`
- `messages_with_repeated_confirmation_language`
- strategic consistency: `stagnation_loop_candidate`, `possible_broken_commitment`, `plausible_betrayal`

Semantics:
- These indicate observed language patterns and potential strategic quality signals.
- They do **not** prove truth, usefulness, acceptance, or execution of proposals.
- Known false positives include keyword-based detection, sarcasm/negation handling, and indirect phrasing.

## 10. Strategic Consistency Evidence Standards
Strategic consistency is a derived chain analysis over artifacts:

Potential chain:
`objective -> relationship_use -> diplomatic_action -> order_alignment -> board_consequence -> memory_update`

Chain claim levels:
- `explicit chain`: objective + concrete diplomacy + matching order + clear consequence visible.
- `partial chain`: some links present, others missing.
- `heuristic chain`: inferred from weak language/timing only.
- `unsupported chain`: should not be reported as a claim.

Guidance:
- Prefer artifact-backed excerpts.
- Mark confidence and basis (`explicit`, `heuristic`, `inferred`).
- Do not treat chain score as game score.

## 11. Reporter / Secret History Rules
Reporter and Secret History outputs must:
- separate observed facts from interpretation,
- remain conservative on alliance/betrayal labels,
- connect negotiation context to later orders and board outcomes where evidence exists,
- label confidence explicitly,
- distinguish agent belief from objective fact.

Canonical rule:
- Secret History may be omniscient about artifacts, but not omniscient about motives.

Therefore:
- It can use all artifacts (including private messages/reflections).
- It must not invent hidden intent beyond evidence.

## 12. Known Limits
- Keyword heuristics can misclassify messages.
- Message/order summaries may lose nuance from full text.
- Reflection/memory can be self-serving, mistaken, or incomplete.
- Absence of evidence is not evidence of absence.
- Private messages are not binding commitments.
- Diplomacy allows deception; deception is analysis content, not parser failure.
- Confidence labels are interpretive, not mathematical certainty.

## 13. Maintenance and Test Expectations
- New event types must be added to this document.
- New reporter claim types must include explicit evidence thresholds.
- Prompt-check additions must be documented with expected meaning.
- Conversation-quality metrics must include definitions and false-positive notes.
- Tests should continue guarding against overclaims (for example: message volume alone => alliance; cordiality + attack => betrayal).
- Outcome semantics must remain consistent across logs, summaries, observatory, and reporter artifacts.

## Appendix: Canonical Artifact Paths
- Experiment trace logs: `experiments/<experiment_id>/logs/experiment.log`, `experiments/<experiment_id>/logs/experiment_events.jsonl`
- Game authoritative events: `experiments/<experiment_id>/games/<game_id>/logs/events.jsonl`
- Observatory diagnostics: `games/<game_id>/observatory/{reflection_diagnostics,prompt_diagnostics,diplomacy_metrics,strategic_consistency}.{md,json}`
- Reporter artifacts:
  - `games/<game_id>/observatory/reports/*`
  - `games/<game_id>/reporter/secret_history.{md,json}`
