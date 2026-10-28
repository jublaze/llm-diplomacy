# Agent Factory

## Purpose
The Agent Factory creates complete, persistent Agent Folios from human-authored identity blueprints.

It is a research and reproducibility tool. It does not optimize policy weights or tune gameplay parameters.

## Blueprint Philosophy
Blueprints are prose-first identity specifications, centered on:
- worldview
- priorities
- alliance philosophy
- communication philosophy
- information philosophy
- evidence philosophy
- planning horizon

The format intentionally avoids numeric personality vectors.

## Blueprint Format
Preferred format is YAML in `blueprints/*.yaml`.

Required top-level fields:
- `schema_version`
- `agent_id`
- `display_name`
- `runtime`
- `description`
- `identity` object

Required `identity` fields:
- `worldview`
- `priorities` (list of strings)
- `alliance_philosophy`
- `communication_philosophy`
- `information_philosophy`
- `evidence_philosophy`
- `planning_horizon`

## Generation Process
Factory entrypoint:
- `create_agent(blueprint)`

Generated folio:
- `manifest.json`
- `soul.md`
- `persistent_memory.md`
- `public_profile.md`
- `memory/`
- `games/`
- shared `strategic_imperatives.md` at the repository root (referenced by each agent manifest)

Generation is deterministic. Running the same blueprint twice produces equivalent output files.

## Soul Generation
`soul.md` is rendered as enduring principles:
- what the agent believes
- what the agent prefers
- what the agent seeks
- what the agent avoids

It is written to remain identity-consistent across country assignments.

## Strategic Layering
The factory separates three concerns:
- Strategic Imperatives: universal objective layer shared by all agents. Answers: what am I fundamentally trying to accomplish?
- Soul: identity/style layer. Answers: how do I prefer to accomplish it?
- Memory: learning layer (`persistent_memory.md` + per-game memory). Answers: what have I learned while trying?

The Soul does not replace universal objectives; it shapes the way those objectives are pursued.

## Public Profile Generation
`public_profile.md` is initial self-presentation:
- communication style
- diplomatic orientation
- intended reputation

It is not an objective truth log.

## Validation
`validate_agent_folio(...)` checks:
- required files/directories exist
- manifest JSON parses
- manifest required fields exist
- schema markers are present where expected

## CLI
Create from blueprint:

```powershell
.\.venv\Scripts\python.exe scripts/create_agent.py blueprints/cassandra.yaml
```

Optional output root:

```powershell
.\.venv\Scripts\python.exe scripts/create_agent.py blueprints/cassandra.yaml --agents-root agents
```

## Extension Points
Future extension can add:
- richer manifest metadata
- optional templating variants for soul/public profile voice
- population-level generation workflows

Without changing:
- Game Master authority model
- Diplomacy rule engine
- agent runtime interface contracts
