# Audience-safe report operations

The framework projector is the authoritative privacy boundary. Viewer-side
filtering is not authorization: static files already delivered to a browser
cannot be hidden securely by React, CSS, search filters, or evidence drawers.

## Supported Phase 5A modes

| Mode | Meaning | Public static staging |
| --- | --- | --- |
| `omniscient_research` | Complete postgame research record, including eligible private cognition | Refused |
| `postgame_public` | Public game record plus a disclosed, postgame-released negotiation transcript | Allowed when explicit metadata and closure checks pass |

Legacy bundles without `extensions.audience` remain readable locally, but are
resolved as `omniscient_research`, `legacy_unversioned`, and not public-safe.
The older Viewer-side `public` / `player-*` projection utilities remain only
for historical compatibility tests. They are not an approved deployment
producer. `power_limited` is deferred and is not an accepted Phase 5A mode.

Negotiations in `postgame_public` retain their original message visibility.
“Private” therefore means private during play, not excluded from the deliberate
postgame release. The UI labels this material “Postgame-released negotiation
transcript” and states that it was not an in-game public fact.

## Produce and stage a public-safe collection

First run the framework command described in its
`docs/REPORT_BUNDLE_PROJECTOR.md`:

```powershell
# Run in llm-dplomacy
.\.venv\Scripts\python.exe scripts\project_report_bundle.py `
  --source <phase-4a-export-root> `
  --output-root <new-phase-5a-output> `
  --audience-mode postgame_public `
  --raw-experiment-root <completed-experiment-root>
```

Then use the dedicated Viewer public-build pipeline:

```powershell
npm run stage:public-reports -- --source-root <new-phase-5a-output>
npm run build:public
npm run verify:public-build
```

The staging command refuses missing/unknown metadata, `omniscient_research`,
mixed audiences, raw experiment paths, source/output overlap, and overwrite.
It validates bundle schema/semantics, audience metadata, report hashes, and
checksummed released-transcript companions before atomic publication to the
isolated `public-build-input` tree.

Do not point a public-safe command at `public/reports` while that directory is
the local omniscient research catalog. Do not publish `public/reports`, `dist`,
the Phase 4A fixtures, or an omniscient catalog.

## Current verification assets

```text
fixtures/phase4a_generated/   # omniscient research source; not public-safe
fixtures/phase5a_generated/   # postgame_public verification output
```

Each projected game carries its own `released/negotiation_transcript.json`.
The catalog builder copies that companion beside the staged report so its
relative evidence path and SHA-256 remain valid.

See `PUBLIC_BUILD_REHEARSAL.md` for the four distinct data locations and the
localhost-only preview command. This workflow does not add hosting,
authentication, uploads, deployment, or a claim that the repository is
publication-approved.
