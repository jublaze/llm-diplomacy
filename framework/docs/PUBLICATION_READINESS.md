> Publication staging update: original code/docs now have the root MIT LICENSE (copyright 2026 jublaze). This staging copy implements the reviewed full-postgame publisher described in `publication/README.md` at the monorepo root. Older preparation notes below record why the prior synthetic/public-only route was insufficient; they are not a claim that this implementation is still absent. No external release has occurred.

# Publication readiness

Current release direction (2026-10-28): retain this checkout and its history locally; prepare a fresh framework source repository plus a separately reviewed deployment of the actual completed Luna viewer data. See [Pages preparation](PAGES_PREPARATION.md). The synthetic example remains a build test, not the selected experiment. MIT for original code is proposed; no project license file or external publication has been applied here.

This repository now tracks the report viewer under `viewer/`, imported from the separate sibling checkout without modifying that checkout. The candidate export validates a proposed `framework/` + `viewer/` source layout; it leaves both original workspaces and their runtime data in place.

## Candidate contents

Create the candidate from an explicit source allowlist:

```powershell
$runId = Get-Date -Format 'yyyyMMdd'
$outputBase = Join-Path 'experiments' ("build-loop-" + $runId)
New-Item -ItemType Directory -Path $outputBase -Force | Out-Null
$destination = Join-Path $outputBase ("publication-candidate-" + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe scripts\export_publication_candidate.py --source-root . --destination $destination
```

The exporter refuses an existing destination, overlapping selected source paths, source symlinks/junctions, and paths escaping declared roots. It writes a deterministic `SOURCE_MANIFEST.json` containing relative paths, byte counts, and SHA-256 hashes. The candidate contains framework source, blueprints, all authored config examples, model profiles, and experiment configs, scripts, selected technical documents, Python tests and the pinned DATC corpus; viewer source, schemas, build scripts and tests; and the standard map SVG with its attribution notice. The offline quickstart uses `per_player_2x2_offline_1901.yaml`; provider-backed configs are included as research source and are not run by this workflow.

The export excludes `.git` and history, agent/runtime memories, local agent state, logs, experiment output, generated viewer reports, historical viewer fixtures, dependencies, build products, caches, and credentials. Generated agent files and historical persistent-memory paths are omitted. Ignore rules and index cleanup keep generated files local; untracking a file does not delete its working copy or rewrite repository history.

## Validation

Run exporter guard tests, export to a new candidate path, then scan the candidate with redacted diagnostics:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_publication_export.py tests\test_security_scan.py -q
$runId = Get-Date -Format 'yyyyMMdd'
$outputBase = Join-Path 'experiments' ("build-loop-" + $runId)
New-Item -ItemType Directory -Path $outputBase -Force | Out-Null
$destination = Join-Path $outputBase ("publication-candidate-" + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe scripts\export_publication_candidate.py --source-root . --destination $destination
.\scripts\security_scan.ps1 -Root $destination
```

The viewer package does not include `node_modules`, report catalogs, `public/reports`, or historical fixtures. Supported Node releases are 22.12 or later within 22.x, 24.x, and 26 or later. From `viewer/`, install locked dependencies with `npm ci --ignore-scripts`, then run `npm run typecheck`, `npm test`, and `npm run build`. These commands do not generate catalogs or read excluded fixture trees. The portable synthetic test covers schema and semantic validation, catalog loading, rendered navigation, and played years including 1912 and 1930. Historical fixture-based regressions remain optional and are not run in a clean checkout.

## Open release decisions

- No project-wide license has been supplied. This workflow does not invent one. The adapted `standard_clean.svg` carries its own CC BY-SA 3.0 attribution and license notice. The upstream DATC corpus includes its MIT license and reuse provenance.
- The DATC gate reports 172 entries: 155 matching expectations, nine project-policy divergences, one adapter limitation (ordinary 6.G.11 convoy-intent inference), and seven historical alternatives. See [the coverage report](DATC_COVERAGE.md) for attribution and case-level evidence. These totals do not establish unqualified standard-rules conformance.
- The candidate is a review artifact, not a release. Human review must approve the package contents, legal terms, and any remaining research limitations before publication.

See the [validation record](VALIDATION.md) for the accepted test totals, offline study scope, and remaining evidence limits.
