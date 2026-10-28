# SYSTEM_DESIGN

Current implementation ownership: [GameMaster and runners](ORCHESTRATION_OWNERSHIP.md). The architectural responsibilities below are design intent; phase execution currently resides in the scripted runner.

## Status
- Version: 0.4 (maintainability revision)
- Scope: Tournament framework architecture for persistent LLM agents, with Diplomacy as the first pluggable rule engine.
- Non-goal: This document is not an implementation guide and does not define Diplomacy rules in detail.

## Vision
The design aims to support repeatable, auditable tournaments between persistent LLM agents. This is design intent, not a production-readiness claim. Current supported execution and limits are documented in `ORCHESTRATION_OWNERSHIP.md`, `EXPERIMENT_CHECKPOINTS.md` and `PROVIDER_RECOVERY.md`.

Diplomacy is the first supported game, not the architectural center. The architecture is designed so future games can plug in new rule engines while reusing:
- experiment orchestration
- persistent agent identity management
- filesystem-mediated communication
- objective scoring and cost accounting
- replayable logs and artifacts

Core vision outcomes:
- objective truth is centralized
- agent internals remain opaque
- all game actions are reproducible from repository artifacts
- experiments can be compared scientifically across runs

## Foundational Principles

### 1) Game Master is sole objective truth
The Game Master (GM) is the only component allowed to:
- advance phase state
- enforce rules
- validate orders
- resolve outcomes
- deliver messages into visible channels
- update leaderboard and objective metrics
- persist authoritative game state

The GM is deterministic: given identical inputs, configuration, and seed controls, it must produce identical authoritative outputs.

The GM owns no persistent memory or subjective history. It reconstructs authority from current state artifacts, configuration, and logs.

The GM never performs social reasoning and never models:
- trust
- alliances
- betrayal
- personality
- reputation
- intention

Those concepts are private to agents and never become GM state.

### 2) Agents are opaque
The GM only requires the following agent-facing metadata:
- `agent_id`
- country assignment for current game
- conformance to required invocation interface for each phase

The GM does not know, inspect, or depend on agent internal files or architecture. Internal artifacts (for example `soul.md`, vector stores, memory graphs, strategy notebooks) are out of scope for GM concerns.

### 3) Filesystem-only communication
Agents never call each other directly. All inter-agent communication occurs through message files written to controlled filesystem locations. The GM controls access, validates release timing, and delivers visibility at tick barriers without interpreting social meaning.

### 4) Persistent identity
Agent identity persists across games and experiments. Country assignments are per-game and non-persistent. The same agent may play different countries across games.

### 5) Replayability
Every game must be replayable from repository data only. No hidden mutable state may exist outside repository-managed artifacts for a recorded run.

Every phase begins from an immutable state snapshot. Every resolution produces a new immutable state snapshot. Authoritative state is never mutated in place.

## Architecture Overview

### Major Components
- Experiment Runner: defines and executes experiment batches, seeds, and assignment policies.
- Game Master: authoritative coordinator of game lifecycle, phase progression, visibility, validation, and persistence.
- Rule Engine (Diplomacy plugin): deterministic validation and resolution for game rules.
- Agent Runtime: process boundary that invokes each agent via contract methods and mediates IO paths.
- Filesystem Artifact Store: canonical storage medium for all inputs/outputs/events.
- Logging Subsystem: append-only event streams and phase archives.
- Leaderboard Service: objective result aggregation (overall and by-country).
- Cost Tracker: objective usage and efficiency metrics per agent/game/experiment.

### High-Level Architecture Diagram
```mermaid
flowchart TD
    ER[Experiment Runner] --> GM[Game Master]
    GM --> RE[Rule Engine: Diplomacy]
    GM --> AR[Agent Runtime]
    AR --> A1[Agent A (opaque)]
    AR --> A2[Agent B (opaque)]
    AR --> A7[Agent G (opaque)]

    GM <--> FS[(Filesystem Artifacts)]
    RE --> FS
    AR --> FS

    GM --> LOG[Logging Subsystem]
    GM --> LB[Leaderboard]
    GM --> COST[Cost Tracker]

    LOG --> FS
    LB --> FS
    COST --> FS
```

### Boundary Emphasis
- GM orchestrates and adjudicates.
- Rule Engine decides rule legality/outcomes from explicit state + orders only.
- Agent Runtime executes opaque agents against phase contracts only.
- Filesystem is the sole shared integration surface.

### Objective vs Subjective State Separation
- Objective state (GM-owned): board position, phase progression, legal orders, adjudication outcomes, message delivery records, logs, leaderboard, filesystem permissions.
- Subjective state (agent-owned): beliefs, trust estimates, alliance models, betrayal narratives, strategic intent, and private reasoning artifacts.

Only objective state can affect official adjudication. Subjective state can influence agent outputs but never directly modifies official game state.

## Data Ownership Matrix
This matrix is the canonical authority reference for ownership, write rights, and default read visibility.

| Artifact | Owner | Writers | Readers |
| --- | --- | --- | --- |
| Board State | Game Master | Game Master | All |
| Orders (submitted) | Agent | Agent | Game Master |
| Orders (validated) | Game Master | Game Master | Rule Engine, Logs, Replay |
| Resolution | Rule Engine (committed by Game Master) | Rule Engine, Game Master (commit) | All |
| Reflection (in-game) | Agent | Agent | Agent, Game Master (for orchestration status only) |
| Persistent Memory | Agent | Agent | Agent |
| Public Profile | Agent | Agent | All |
| Message Outbox Staging | Agent | Agent | Game Master |
| Message Inbox / Global Channel | Game Master | Game Master | Authorized Agents, Replay |
| Leaderboard | Game Master | Game Master | All |
| Cost Logs | Game Master | Game Master | All |
| Event Log | Game Master | Game Master | All |

Ownership notes:
- Owner defines authoritative truth and conflict resolution for that artifact class.
- Writer access does not imply ownership authority.
- Read access is subject to phase visibility and permission policy.

## System Invariants
This section is the architectural constitution. Implementations may optimize internals but must not violate these invariants.

- The Game Master is the only authority on objective state.
- Every authoritative state transition is deterministic under identical artifacts, configuration, and seed controls.
- Every machine-readable artifact that participates in orchestration or replay includes `schema_version`.
- Every phase begins from an immutable input snapshot and produces a new immutable output snapshot.
- Agents never directly modify authoritative game state.
- Communication always occurs through Game Master-controlled filesystem channels.
- Agent Folios (all agent-private internal files and memory systems) remain opaque to the Game Master.
- Objective adjudication never depends on inferred trust, alliances, betrayal, reputation, psychology, or intent.
- Every authoritative transition is represented as an immutable event and linked to resulting state artifacts.
- Every completed game is replayable from repository artifacts alone.

## Event Model
The framework uses an immutable event model for all authoritative transitions. State snapshots capture results; event records capture the transition history.

Conceptual event families include:
- Negotiation Tick Started
- Negotiation Tick Completed
- Orders Submitted
- Orders Validated
- Resolution Completed
- Reflection Completed
- Cleanup Completed
- Leaderboard Updated
- Cost Metrics Committed

Event model guarantees:
- Events are append-only and never edited in place.
- Events are ordered deterministically within a phase/tick context.
- Each authoritative state artifact is traceable to one or more causal events.
- Replay consumes events plus immutable artifacts to reconstruct official history.

## Component Responsibilities

### Experiment Runner
Responsibilities:
- load experiment configuration
- instantiate game runs with seeds and assignments
- call GM lifecycle entrypoints
- collect run-level summaries

Non-responsibilities:
- no per-phase adjudication
- no direct agent social modeling
- no rule resolution logic

### Game Master
Responsibilities:
- initialize game state from configuration
- drive full phase/tick progression
- enforce barriers for simultaneous ticks
- collect/validate orders (via Rule Engine)
- commit authoritative phase transitions
- release visible messages after tick completion
- persist authoritative state snapshots
- trigger reflection and game-end cleanup contracts
- own leaderboard/cost/log writes

Explicit non-responsibilities:
- no inference about agent motives or beliefs
- no dependency on agent internal files
- no hidden mutable state outside artifacts
- no game-specific rule math beyond invoking the Rule Engine interface

### Rule Engine (Diplomacy)
Responsibilities:
- validate structured orders against current phase/state
- resolve legal outcomes deterministically
- emit machine-readable resolution artifacts

Non-responsibilities:
- no experiment orchestration
- no agent invocation
- no leaderboard or cost ownership

### Agent Runtime
Responsibilities:
- invoke required agent interface methods
- provide per-agent filesystem views and phase context
- enforce call deadlines/timeouts and capture outputs
- return standardized status objects to GM

Non-responsibilities:
- no order adjudication
- no state authority
- no interpretation of agent internals

### Logging Subsystem
Responsibilities:
- append-only event recording
- phase/tick trace archives
- replay-consumable artifacts

### Leaderboard
Responsibilities:
- maintain objective public statistics
- aggregate overall and by-country performance

### Cost Tracker
Responsibilities:
- record objective usage/cost metrics
- produce experiment-level comparative reports

## Public Interfaces
This section defines conceptual contracts between major components. These are interface boundaries, not language-specific APIs.

### Experiment Runner Interface
Ownership:
- owns experiment orchestration policy and run scheduling

Inputs:
- experiment configuration
- agent pool metadata
- framework defaults

Outputs:
- game instantiation requests to Game Master
- run-level summaries

Boundary:
- does not adjudicate game rules
- does not invoke agents directly for phase logic

### Game Master Interface
Ownership:
- sole authority for objective game state transitions, visibility, and finalization

Inputs:
- current immutable state snapshot
- phase artifacts (messages, orders, reflections)
- rule-engine adjudication outputs
- experiment/game configuration

Outputs:
- next immutable state snapshot
- authoritative phase outcomes
- visibility releases
- leaderboard/cost/log update intents

Boundary:
- does not perform subjective/social reasoning
- does not contain game-specific rule math beyond invoking the Rule Engine

### Rule Engine Interface
Ownership:
- deterministic legality checks and adjudication for a specific game

Inputs:
- game ruleset identifier
- immutable input state snapshot
- validated/submitted orders

Outputs:
- legality results
- deterministic resolution artifact
- derived state transition result (for GM commit)

Boundary:
- no orchestration, leaderboard, or cost ownership
- no agent invocation
- Diplomacy-specific semantics are defined in `DIPLOMACY_RULE_SPEC.md`

### Agent Runtime Interface
Ownership:
- process/sandbox invocation boundary for agents

Inputs:
- phase context package from GM
- filesystem view permissions
- timeout/retry policy

Outputs:
- standardized invocation status
- agent-produced artifacts (messages, orders, reflection, cleanup output)
- usage metadata for cost accounting

Boundary:
- no authoritative state writes
- no adjudication logic

### Leaderboard Interface
Ownership:
- objective aggregate statistics derived only from finalized game outcomes

Inputs:
- finalized game result artifacts

Outputs:
- overall and by-country leaderboard artifacts

Boundary:
- no direct influence on active-game adjudication

### Cost Tracker Interface
Ownership:
- objective usage/cost accounting and derived analysis

Inputs:
- invocation usage metadata
- runtime timing and call records
- game/phase identity context

Outputs:
- immutable cost logs
- derived per-phase/per-game/per-experiment summaries

Boundary:
- orthogonal to game mechanics and game outcomes

## Game Lifecycle
Hierarchy and meaning:
- Experiment: a configured batch of one or more games under shared controls.
- Game: one complete multi-power match from initial setup to terminal condition.
- Year: annual cycle containing Spring, Fall, Winter phase groups.
- Season: Spring, Fall, or Winter context.
- Phase: operational step within season (negotiation, orders, resolution, reflection, etc.).
- Tick: one simultaneous negotiation turn where each active agent executes once before message release.

Lifecycle nesting:
`Experiment -> Game -> Year -> Season -> Phase -> Tick`

## Phase Flow

### Spring Sequence
1. Negotiation (tick-based, simultaneous)
2. Movement Orders submission
3. Movement Resolution
4. Agent Reflection Phase
5. Optional Retreat Negotiation (if retreats required)
6. Retreat Orders
7. Retreat Resolution
8. Agent Reflection Phase

### Fall Sequence
1. Negotiation (tick-based, simultaneous)
2. Movement Orders submission
3. Movement Resolution
4. Agent Reflection Phase
5. Optional Retreat Negotiation (if retreats required)
6. Retreat Orders
7. Retreat Resolution
8. Agent Reflection Phase

### Winter Sequence
1. Negotiation
2. Adjustment Orders
3. Adjustment Resolution
4. Agent Reflection Phase

Game-end cleanup never occurs mid-game. It occurs once after terminal game condition is reached.

## Negotiation Model
Negotiation is simultaneous and tick-barriered.

Tick semantics:
1. GM opens tick `t` with visible inbox/global state snapshot.
2. Each active agent executes independently (`negotiate_tick`) and writes outbound message proposals.
3. GM waits for all agents to complete or timeout/fail under configured policy.
4. GM validates and commits message artifacts for tick `t`.
5. Newly committed messages become visible to recipients only after barrier completion.
6. GM evaluates negotiation stop criteria.

Stop criteria:
- all active agents signal done for the current negotiation phase
- or configured max tick count is reached

Rationale:
- enforces simultaneity and fairness
- prevents ordering artifacts from sequential execution leakage
- enables deterministic replay and debugging

## Filesystem Architecture

### Repository Layout (canonical)
```text
llm-diplomacy/
  docs/
    SYSTEM_DESIGN.md
    DIPLOMACY_RULE_SPEC.md        # referenced, authored separately
    AGENT_SPEC.md                 # referenced, authored separately
  config/
    experiments/
      <experiment_id>.yaml
    models/
      <model_profile>.yaml
  experiments/
    <experiment_id>/
      manifest.json
      games/
        <game_id>/
          game_config.json
          state/
            current_state.json
            snapshots/
              <phase_id>.json
          phases/
            <year>_<season>_<phase>/
              ticks/
                <tick_index>/
                  status.json
                  visibility_snapshot.json
              orders/
                submitted/
                  <agent_id>.json
                validated/
                  <agent_id>.json
              resolution/
                input_state.json
                adjudication.json
                output_state.json
              reflection/
                <agent_id>.json
          messages/
            private/
              <agent_id>/inbox.jsonl
            global/
              global.jsonl
            outbox_staging/
              <phase_id>/<tick_index>/<agent_id>.jsonl
          agents/
            <agent_id>/
              runtime/
                phase_inputs/
                phase_outputs/
                errors/
              game_memory/
                working_memory.json
              cleanup/
                game_summary.json
          metrics/
            cost/
              per_tick.jsonl
              per_phase.jsonl
              per_game.json
            communication/
              counts.json
          logs/
            events.jsonl
            phases/
              <phase_id>.jsonl
            errors.jsonl
            replay_index.json
      leaderboard/
        overall.json
        by_country.json
```

### Ownership and Write Permissions
- GM-owned (authoritative writes only by GM):
  - `state/`, `phases/*/resolution/`, `messages/private/*/inbox.jsonl`, `messages/global/global.jsonl`, `metrics/`, `logs/`, `leaderboard/`
- Agent-writable via runtime sandbox:
  - `messages/outbox_staging/.../<agent_id>.jsonl`
  - `phases/*/orders/submitted/<agent_id>.json`
  - `phases/*/reflection/<agent_id>.json`
  - `agents/<agent_id>/runtime/phase_outputs/`
- Rule Engine write surface (invoked by GM, effectively GM-authorized):
  - `phases/*/resolution/adjudication.json` (or return payload captured there by GM)

All authoritative state transitions are committed by GM, never by agents.

## Message Model

### Message Types
- Private: directed from one agent to one recipient (`to_agent_id`).
- Global: broadcast channel visible to all active agents.

### Storage Model
- Inbox files are append-only JSONL.
- Global channel is append-only JSONL.
- Agent writes are staged in per-tick outbox files.
- GM validates and releases staged messages at tick barrier.

### Canonical Message Record (schema sketch)
```json
{
  "schema_version": "1.0",
  "message_id": "uuid",
  "experiment_id": "exp_...",
  "game_id": "game_...",
  "phase_id": "1901_spring_negotiation",
  "tick": 3,
  "from_agent_id": "agent_alpha",
  "to": {"type": "private", "to_agent_id": "agent_beta"},
  "body": "free text payload",
  "created_at": "2026-06-26T12:00:00Z"
}
```

Constraints:
- append-only; no edits/deletes
- message visibility changes only by GM release
- deterministic ordering within a tick; current ownership and staged delivery are described in `ORCHESTRATION_OWNERSHIP.md`

## Order Submission
Orders are submitted as structured files per active agent and phase.

Interface:
- agents write `phases/<phase>/orders/submitted/<agent_id>.json`
- GM validates structure against JSON schema
- GM invokes Rule Engine for legality against current state

Schema authority:
- concrete schemas are defined in `DIPLOMACY_RULE_SPEC.md` (or engine-specific schema docs for future games)

Schema requirement:
- every machine-readable artifact MUST include `schema_version`

Validation outcomes:
- valid: eligible for adjudication
- invalid: handled by configured policy (forfeit/hold/default/etc. defined by rules spec and experiment config)

## Agent Reflection Phase
Agent Reflection Phase occurs after each resolution phase (movement, retreat, adjustment) and is a first-class framework phase.

Contractual intent:
- update in-game, per-game working memory only
- must not mutate cross-game persistent memory at this stage

Artifacts:
- agent reflection output stored per phase
- GM logs reflection invocation success/failure objectively

Persistent identity memory changes are deferred to game-end cleanup.

## Game End Cleanup
Executed exactly once per game after terminal condition.

Sequence:
1. GM finalizes official game result artifacts
2. Leaderboard updated (overall and by-country)
3. Agent produces game summary via cleanup contract
4. Agent persistent memory updated from summary
5. Public profile update optionally executed (if enabled)

Cleanup is idempotent by `game_id` guard; repeated invocations must not double-apply leaderboard or memory writes.

## Leaderboard
Leaderboard is GM-owned, objective, and public.

Minimum tracked dimensions:
- overall results across all games
- by-country performance aggregates

Example objective fields:
- games played
- wins / draws / eliminations
- survival rate
- average supply centers at end
- placement distribution

All leaderboard updates are derived from finalized game results only.

## Cost Accounting
Cost accounting is orthogonal to game mechanics and used for experiment analysis.

Tracked metrics (minimum):
- token usage (prompt/completion or equivalent)
- model/API call counts
- messages sent/received
- runtime latency per invocation
- estimated monetary cost
- efficiency ratios (for example outcome per dollar/token)

Cost artifacts are immutable logs plus derived summaries at phase/game/experiment levels.

## Logging and Replay

### Required Logs
- append-only global event log (`events.jsonl`)
- phase logs (`logs/phases/*.jsonl`)
- full message archive (private/global + staged provenance)
- submitted and validated orders archive
- resolution input/output archive
- cost logs
- error logs

### Replay Requirements
A replay run must be able to reconstruct every authoritative state transition using only repository artifacts.

Required replay guarantees:
- deterministic phase order
- deterministic visibility windows per tick
- reproducible adjudication inputs
- traceable mapping from agent outputs to GM decisions
- immutable phase-by-phase state lineage via input/output snapshots

TODO (Replay Determinism):
- finalize deterministic sort and tie-break policies for simultaneously released messages and equal-timestamp events.

## Agent Invocation Contract
GM depends only on required interfaces and return envelopes.

Required phase-level methods (conceptual):
- `negotiate_tick(context) -> TickResult`
- `submit_orders(context) -> OrderSet`
- `reflect(context) -> ReflectionResult`
- `game_end_cleanup(context) -> CleanupResult`

Contract requirements:
- explicit success/failure status
- machine-readable payload paths or objects
- optional usage metadata for cost tracking
- no assumptions about internal chain-of-thought or file layout

Detailed payload schemas and context objects belong in `AGENT_SPEC.md`.

## Configuration Model
Experiment configuration is declarative and versioned.

`schema_version` is mandatory in configuration and all machine-readable artifacts.

Minimum configuration domains:
- experiment identity and number of games
- agent pool and country assignment strategy
- phase and tick limits
- timeout/retry/failure policies
- model/runtime profiles
- visibility/channel rules
- scoring/leaderboard policy switches
- random seed and determinism controls
- logging verbosity and retention settings

Config precedence:
1. framework defaults
2. experiment file overrides
3. per-game derived materialization written to `game_config.json`

## Error Handling Architecture
Error handling is policy-driven and explicit in artifacts.

Classes:
- malformed agent output
- schema validation failure
- illegal orders
- runtime timeout
- invocation crash
- rule-engine failure
- filesystem contention/corruption detection

Architectural behavior:
- GM records error event with phase/tick context
- GM applies configured continuation policy (retry, default action, disqualify, abort game)
- GM preserves replayability by recording both error and policy decision as events

No silent recovery paths are allowed for authoritative state transitions.

## Extensibility for Non-Diplomacy Games
To support future games, only the Rule Engine and game-specific schemas should vary. Core framework components (GM, runner, logging, cost tracking, persistent agent lifecycle) remain stable.

Extension points:
- `RuleEngine` interface implementation
- phase template definitions
- order/message schema variants
- game-end scoring adapter

## Out of Scope for This Document
Intentionally excluded details:
- Diplomacy map topology and province graph
- movement/support/convoy/retreat/build rule mechanics
- exact order grammar semantics
- agent identity dimensions or psychology
- `soul.md` design
- persistent memory internal format

Those are specified in:
- `DIPLOMACY_RULE_SPEC.md`
- `AGENT_SPEC.md`

## ADR Recommendation
Use `docs/ADR/` for Architecture Decision Records to preserve rationale and change history for core architectural decisions.

Recommended ADR topics:
- Game Master is objective only
- Agent folio is opaque
- Tick-based negotiation
- Persistent agent identity
- Public profiles
- Convoy paradox resolution
- Filesystem-mediated communication

## Roadmap (Implementation Drops)
1. Drop 1: Core infrastructure (filesystem layout, GM skeleton, event logging, config loader)
2. Drop 2: Map model scaffolding (data model only, no advanced adjudication)
3. Drop 3: Movement resolution baseline
4. Drop 4: Support resolution
5. Drop 5: Convoys
6. Drop 6: Retreat phases
7. Drop 7: Winter builds/disbands
8. Drop 8: Negotiation tick runtime and message release barriers
9. Drop 9: Persistent agent lifecycle (cross-game identity + cleanup)
10. Drop 10: Full experiment framework (batch runner, leaderboard analytics, cost analysis)

## Implementation references and remaining design work
- The implemented event taxonomy and required fields are documented in [Core event contracts](CORE_EVENT_CONTRACTS.md); unknown event types receive envelope validation only.
- TODO: Finalize deterministic ordering rules for same-tick message release and log collation.
- Current invalid-response, timeout and opt-in correction policies are documented in [Provider recovery](PROVIDER_RECOVERY.md). They depend on configuration; the architecture does not imply one global fallback policy.
- TODO: Define migration/versioning protocol for schema evolution across framework releases.
