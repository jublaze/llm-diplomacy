# AGENT_SPEC

This document describes the agent design and contracts. Runtime behavior is configuration-dependent: current phase execution is in `ORCHESTRATION_OWNERSHIP.md`; free-text reflections are described in `REFLECTION_MEMORY.md`; default versus opt-in correction behavior is in `PROVIDER_RECOVERY.md`. Persistent postgame cleanup remains typed. Design goals below do not imply that all extension points or future games are implemented.

## Status
- Version: 1.0
- Scope: Agent identity, lifecycle, memory model, contracts, and extensibility for persistent-agent tournaments.
- Non-goal: Diplomacy adjudication, Game Master internals, filesystem architecture, logging internals, or cost accounting implementation.

## Purpose
This document defines the agent system for the tournament framework:
- what an agent is
- what an agent owns
- what an agent persists
- what capabilities an agent must expose
- how agents evolve across games
- how new agents are created

This document is intentionally game-agnostic where possible. Diplomacy is the first Rule Engine, but agent identity persists beyond any single game type.

## Relationship to Other Specifications
This document does not redefine concepts already owned elsewhere.

Authoritative references:
- `SYSTEM_DESIGN.md`: Game Master authority boundaries, phase orchestration, visibility model, filesystem ownership, logging, replayability, and deterministic runtime behavior.
- `DIPLOMACY_RULE_SPEC.md`: Diplomacy map, order grammar, legality, and adjudication semantics.

When terms overlap, this document follows the architecture defined in `SYSTEM_DESIGN.md`.

## Core Terms
The following terms are used with strict intent:
- `Game Master`: deterministic orchestrator and authoritative rule/state owner.
- `Rule Engine`: deterministic validator/adjudicator for game-specific rules.
- `Agent Runtime`: invocation boundary that executes agent interface methods.
- `Agent Folio`: complete private workspace owned by an agent.
- `Public Profile`: agent-authored public self-presentation artifact.
- `Leaderboard`: objective Game Master-owned aggregate results.
- `Negotiation Tick`: one simultaneous negotiation turn with barriered message release.
- `Reflection`: per-phase post-resolution agent update step for in-game memory.
- `Game End Cleanup`: once-per-game persistent update step.
- `Phase ID`: stable identifier for a specific phase instance within a game timeline.
- `Game ID`: stable identifier for one complete game instance.
- `Agent ID`: stable identifier for one persistent agent identity.
- `Country Assignment`: temporary mapping of an `Agent ID` to a game-specific country/power.

## What Is an Agent?
An agent is a persistent strategic identity keyed by `Agent ID`.

An agent is not a country. A country is a temporary `Country Assignment` for one `Game ID`.

The same `Agent ID` may play different countries across many games while retaining continuity of identity, learning, and self-presentation.

Separation constraints:
- The Game Master treats agents as opaque participants with required public contracts only.
- The Game Master does not inspect or reason about agent internals, motives, trust models, alliance beliefs, or psychological state.
- Agent implementation choices are intentionally unrestricted as long as required contracts are met.

### Agent vs Agent Runtime
`Agent` and `Agent Runtime` are distinct concepts.

`Agent`:
- persistent identity
- philosophy
- memory
- `Public Profile`
- private workspace
- long-term continuity

`Agent Runtime`:
- execution environment
- invokes required public interfaces
- presents context to the agent
- returns structured outputs
- may change without changing agent identity

Identity continuity principle:
- the same `Agent ID` may execute under different runtimes or model providers while remaining the same agent.

## Agent Lifecycle
Canonical lifecycle:

`Agent created -> Assigned to game -> Assigned country -> Repeated negotiation/order/reflection cycles -> Game ends -> Game End Cleanup -> Agent persists`

Operational interpretation:
1. Agent creation establishes a new `Agent ID` and initial Agent Folio.
2. For each `Game ID`, the Game Master applies a temporary `Country Assignment`.
3. During game phases, the agent repeatedly executes `negotiate_tick()`, `submit_orders()`, and `reflect()` according to phase context.
4. After terminal game condition, `game_end_cleanup()` runs exactly once for that `Game ID`.
5. Persistent artifacts are updated and carried into future games.

## Agent Perception Model
Per invocation, an agent reasons from its authorized view, not from hidden global state.

Conceptually, agent perception may include:
- current authoritative board state
- current phase and `Phase ID`
- current `Country Assignment`
- visible message history
- visible `Public Profile` artifacts
- public `Leaderboard` data
- in-game memory
- persistent memory
- soul
- experiment configuration intentionally exposed to agents

Perception principle:
- agents reason only from information available through their authorized view.
- this section defines perception semantics, not implementation mechanics.

## Information Boundaries
Information access is governed by authorization boundaries rather than agent assumptions.

### Objective Public Information
Examples:
- authoritative board state
- visible public profiles
- public leaderboard

### Objective Private Information
Examples:
- private messages addressed to the agent
- current country assignment for the active game

### Agent-Owned Private Information
Examples:
- soul
- persistent memory
- in-game memory
- private tools and internal artifacts in the Agent Folio

### Experimental Information
Examples:
- privileged information in experimental variants
- espionage-variant inputs
- additional research artifacts exposed by experiment design

## Agent Folio
### Definition
The Agent Folio is the complete private workspace owned by an agent.

### Ownership
- The Agent Folio is agent-owned.
- The Game Master does not interpret Folio contents.
- The Game Master only enforces execution and filesystem permission boundaries defined by system architecture.
- The framework intentionally places no restrictions on Folio internals except where public contracts are involved.

### Contents
The Folio is open-ended, implementation-independent, and opaque to the Game Master.

The Folio may include any private files and internal systems needed by the agent. Examples are illustrative, not prescriptive:
- `soul.md`
- `persistent_memory.md`
- `memory.md`
- `public_profile.md`
- markdown
- JSON
- SQLite
- strategy notebooks
- embeddings
- graph databases
- vector stores
- Python tools
- custom analysis tools
- future experimental artifacts
- future extensions

No specific internal schema is required unless exposed through required public contracts.

## Agent Manifest
The Agent Manifest is the portable public description of an agent.

It is not the Agent Folio.

Purpose:
- allow an agent to be installed into the framework
- expose public identity and compatibility metadata
- declare supported capabilities and runtime binding requirements

The manifest describes framework-facing identity and interface support, not internal reasoning.

Illustrative example:

```json
{
  "agent_id": "agent_cassandra",
  "display_name": "Cassandra",
  "runtime": {
    "kind": "agent_runtime_profile",
    "profile": "default_v1"
  },
  "public_profile_path": "agents/agent_cassandra/public_profile.md",
  "supported_capabilities": [
    "negotiate_tick",
    "submit_orders",
    "reflect",
    "game_end_cleanup"
  ]
}
```

This example is intentionally non-prescriptive and does not constrain implementation technology.

## Capability Profiles
Future versions may optionally define capability profiles layered above manifest-declared capabilities.

Illustrative examples:
- baseline agent
- research agent
- experimental agent
- privileged agent

Profile principle:
- capability profiles are optional descriptive groupings.
- they do not alter Game Master architectural responsibilities.

## Required Public Interface
The Agent Runtime invokes contract methods. This section specifies interface intent and contract obligations, not implementation.

### Required Capabilities
- `negotiate_tick(context) -> TickResult`
- `submit_orders(context) -> OrderSet`
- `reflect(context) -> ReflectionResult`
- `game_end_cleanup(context) -> CleanupResult`

### Contract Requirements
All required methods must:
- accept machine-readable context including `Agent ID`, `Game ID`, `Phase ID`, and current `Country Assignment`
- return explicit success/failure status
- return structured output suitable for deterministic processing by the Game Master and Rule Engine
- avoid hidden side effects on authoritative game state

### Method Semantics
`negotiate_tick()`:
- Executes once per `Negotiation Tick` when negotiation is active.
- Produces outbound communication artifacts for Game Master validation/release.
- Must treat visibility as bounded by current tick context.

`submit_orders()`:
- Executes for order-bearing phases.
- Produces structured orders for Rule Engine validation/adjudication.
- Must not assume privileged access beyond provided phase context.

`reflect()`:
- Executes after relevant phase resolutions.
- Updates in-game reasoning state only.
- Must not perform cross-game persistent updates.

`game_end_cleanup()`:
- Executes once after game termination.
- Summarizes completed game and applies persistent updates.
- May update `Public Profile` according to policy in this document.

## Memory Model
### In-Game Memory
Properties:
- temporary
- scoped to one `Game ID`
- reset for each new game
- updated after every `Reflection`

Purpose:
- maintain short-horizon state for current board evolution, negotiations, commitments, and tactical/strategic hypotheses.

### Persistent Memory
Properties:
- durable across games
- updated only during `Game End Cleanup`
- represents accumulated experience and long-horizon adaptation

Purpose:
- retain lessons, recurring patterns, historical priors, and identity-consistent strategic refinements.

### Soul
Properties:
- persistent identity artifact
- defines enduring philosophy and strategic character
- normally static
- may optionally evolve in explicitly configured experimental modes

Purpose:
- preserve coherence of agent identity across changing countries and game contexts.

### Public Profile
Properties:
- publicly readable
- agent-authored
- updatable only during `Game End Cleanup`
- subjective self-presentation, not objective truth

Purpose:
- communicate how the agent wants to be perceived by others.

### Identity Stability
Conceptual hierarchy:
- Soul defines who the agent strives to be.
- Persistent Memory records what the agent has learned.
- In-Game Memory captures the current game's evolving state.

Design implication:
- identity stability comes from continuity of soul, learning continuity in persistent memory, and game-specific adaptation in in-game memory.

### Identity Consistency
An agent should remain recognizably itself across:
- different countries
- different opponents
- different games
- accumulated experience

Consistency principle:
- learning should refine identity rather than replace it.
- this reinforces the distinction between Soul (enduring orientation) and Persistent Memory (accumulated experience).

## Universal Strategic Imperatives
All agents share a universal objective layer represented by `strategic_imperatives.md`.
The default repository-level location is the project root.

## Universal Diplomacy Primer
LLM runtimes also consume a shared game-orientation primer from `diplomacy_primer.md` (project root by default).
This primer explains core Diplomacy structure and communication context, independent of personality.

This layer answers:
- what am I fundamentally trying to accomplish?

Soul answers:
- how do I prefer to accomplish it?

Memory answers:
- what have I learned while trying?

Design rule:
- universal strategic imperatives are shared by all agents and are not personality traits.
- Soul modulates style and constraints for pursuing those objectives; it does not nullify them.

## Identity Dimensions
Identity dimensions are a design vocabulary.

They are:
- not numeric parameters
- not scoring vectors
- not behavior weights

Recommended practice:
- express dimensions as prose in `soul.md`
- favor coherent philosophy over trait checklists
- describe tendencies, conditions, and exceptions in natural language rather than assigning numeric values

### Social Dimensions
Examples:
- honesty
- loyalty
- forgiveness
- reciprocity
- suspicion

### Strategic Dimensions
Examples:
- aggression
- risk tolerance
- growth focus
- coalition preference
- planning horizon

### Cognitive Dimensions
Examples:
- spatial scope (regional vs global reasoning)
- historical diligence
- adaptability
- epistemic confidence
- information seeking

### Communication Dimensions
Examples:
- initiative
- communication frequency
- communication breadth
- transparency
- responsiveness

Clarification:
- communication frequency: how often an agent sends messages.
- communication breadth: how many different powers the agent engages.

Illustrative contrast:
- one agent may send many messages to a single ally (high frequency, low breadth).
- another may communicate lightly with every power (lower per-peer frequency, high breadth).

### Alliance Selection Philosophy
Examples:
- trust first
- geography first
- communication first
- capability first
- necessity first
- ideological similarity

### Evidence Preference
Examples:
- actions over words
- words over actions
- historical reputation
- recent behavior
- board incentives

### Information Brokerage / Information Ethics
This dimension must be treated explicitly during agent design.

Key questions:
- confidentiality: what is kept private and for how long
- willingness to share information: default sharing posture
- sharing scope: who receives what classes of information
- fidelity: exactness vs framing in relayed information
- attribution: whether sources are identified
- timing of disclosure: immediate vs delayed release

Illustrative behavior categories:
- truthful relay
- selective omission
- strategic framing
- fabrication

These are distinct strategic choices, not a single binary notion of "lying."

Important independence constraint:
- alliance commitment and information-sharing behavior are separate concepts
- an agent may maintain alliance commitment while selectively sharing information about alliance context
- this separation should be stated intentionally in identity design

## Agent Creation Process
The recommended process prioritizes coherent philosophy over disconnected behavior rules.

Guidance principle:
- avoid designing agents by maximizing isolated traits.
- instead design a worldview, priorities, decision principles, information philosophy, and communication philosophy.
- specific traits should emerge naturally from those higher-level commitments.

1. Define enduring philosophy first.
2. Describe decision principles before tactical heuristics.
3. Express identity dimensions as prose in `soul.md`.
4. Define memory update principles for `Reflection` and `Game End Cleanup`.
5. Draft initial `Public Profile` as self-presentation, not objective log.
6. Validate contract conformance for required interface methods.
7. Run trial games and refine coherence, not just win-rate overfitting.

Creation goal:
- produce a strategic identity that remains legible and consistent across many `Country Assignment` values and many `Game ID` instances.

## Sample Agents
The following initial sample population illustrates distinct but competitive identities. These are archetypes for seeding diversity, not hard constraints.

### Cassandra
- Core philosophy: radically honest alliance builder.
- Strengths: high credibility, rapid trust formation with partners who value transparency.
- Weaknesses: reduced tactical flexibility for deceptive maneuvers.
- Characteristic behaviors: explicit commitments, direct language, and visible consistency between words and actions.

### Dorian
- Core philosophy: loyal ally who never betrays first.
- Strengths: stable coalition building, long-horizon coordination, predictable alliance behavior.
- Weaknesses: vulnerability to preemptive betrayal by opportunistic peers.
- Characteristic behaviors: sustained alliance support, renegotiation before rupture, explicit reciprocity norms.

### Marcellus
- Core philosophy: conditional opportunist.
- Strengths: strong adaptation to board incentives, timing-sensitive realignment, pragmatic dealmaking.
- Weaknesses: can accumulate distrust when pivots are frequent or poorly explained.
- Characteristic behaviors: cooperative while incentives align, rapid stance shifts when coalition math changes.

### Seraphine
- Core philosophy: expansion-focused conqueror.
- Strengths: decisive initiative, pressure creation, strong conversion of tempo into territory.
- Weaknesses: visible growth invites balancing coalitions.
- Characteristic behaviors: proactive attack windows, assertive bargaining, willingness to accept calculated risk for growth.

### Helena
- Core philosophy: information broker and map-wide communicator.
- Strengths: broad situational awareness, influence through information flow, rapid narrative shaping.
- Weaknesses: over-brokering can erode trust if confidentiality and attribution are mismanaged.
- Characteristic behaviors: many concurrent communication channels, selective relays, timing-sensitive disclosures.

### Victor
- Core philosophy: reputation enforcer who punishes betrayal.
- Strengths: deterrence through credible retaliation, clear alliance boundary enforcement.
- Weaknesses: punitive follow-through can create short-term tactical inefficiencies.
- Characteristic behaviors: explicit red lines, predictable punishment after betrayal, preferential treatment for reliable partners.

### Selene
- Core philosophy: skeptical analyst who requires evidence before trust.
- Strengths: high deception resistance, disciplined updates, strong inference under uncertainty.
- Weaknesses: slower early coalition lock-in when peers expect immediate trust.
- Characteristic behaviors: requests verification actions, values observed behavior over promises, calibrates trust incrementally.

## Public Profile
### Purpose
The `Public Profile` allows an agent to present itself to others as part of strategic social play.

### Properties
It is:
- public
- agent-authored
- subjective
- self-presentational

It is not:
- an objective historical record
- Game Master-authored truth
- a guaranteed reflection of private reasoning

### Update Policy
- May be updated only during `Game End Cleanup`.
- Must remain attributable to the same `Agent ID` across games.

## Persistent Learning
Learning occurs at game boundaries.

During `Game End Cleanup`, the agent:
1. summarizes the completed game
2. updates persistent memory
3. optionally updates `Public Profile`

Constraints:
- The Game Master never edits agent-owned persistent artifacts.
- Persistent updates must be attributable to the completed `Game ID`.
- In-game adaptation belongs in `Reflection`; cross-game learning belongs in cleanup.

## Leaderboard Interaction
Agents may read the public `Leaderboard` and incorporate historical performance into strategic reasoning.

Boundary constraints:
- Leaderboard remains objective and Game Master-owned.
- Agents may interpret leaderboard data subjectively, but cannot mutate leaderboard data.

## Experimental Extensions
Future capabilities may expand agent sophistication without changing Game Master conceptual boundaries, because the Agent Folio is intentionally opaque.

Examples:
- evolving souls
- opening libraries
- specialized evaluators
- external strategy books
- historical databases
- statistical tools
- custom note systems
- graph analysis
- espionage experiments
- privileged experimental information (such as espionage variants)
- additional private files
- hidden-information experiments
- custom reasoning modules

Extension principle:
- as long as required public contracts remain stable and deterministic integration constraints are respected, internal agent evolution can proceed independently.
- different agents may possess different private capabilities without requiring Game Master changes.

## Agent Validity Checklist
An agent is framework-compatible when all items below are true:

- Has a stable `Agent ID` distinct from any country/power label.
- Accepts temporary `Country Assignment` per `Game ID`.
- Implements required capabilities: `negotiate_tick()`, `submit_orders()`, `reflect()`, `game_end_cleanup()`.
- Produces machine-readable outputs required by the Agent Runtime and Game Master contracts.
- Treats `Reflection` as in-game only.
- Restricts persistent memory updates to `Game End Cleanup`.
- Maintains a clear distinction among in-game memory, persistent memory, soul, and `Public Profile`.
- Treats `Public Profile` as subjective self-presentation.
- Can consume objective `Leaderboard` data without assuming write authority.
- Preserves deterministic interoperability by avoiding undocumented side channels into authoritative state.

## Out of Scope
This document does not define:
- Diplomacy rule adjudication
- map topology
- Game Master implementation
- filesystem architecture
- logging implementation
- cost accounting implementation

For those concerns, see:
- `SYSTEM_DESIGN.md`
- `DIPLOMACY_RULE_SPEC.md`

## Experimental Reproducibility Note
For long-term research comparability, the same `Agent ID` should be reproducible when initialized from the same:
- Soul
- Persistent Memory
- Public Profile
- Agent Manifest

Reproducibility principle:
- agent identity is an experimental artifact that should remain comparable across tournament runs.
