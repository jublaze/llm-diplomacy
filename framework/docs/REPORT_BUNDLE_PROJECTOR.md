# Diplomacy Viewer audience projector

`scripts/project_report_bundle.py` is the framework-owned Phase 5A privacy
boundary. It consumes valid Phase 4A report bundles and emits deterministic,
structurally projected bundles. It never reads prompts through an LLM, rewrites
private prose, or treats a browser filter as access control.

## Audience policy v1

Policy marker: `diplomacy_audience_policy_v1`.

| Mode | Purpose | Static public distribution |
| --- | --- | --- |
| `omniscient_research` | Complete research record, including private cognition and eligible observatory records | No |
| `postgame_public` | Public game facts plus player-authored negotiations deliberately released after play | Yes, after the generated audit and catalog checks pass |

`postgame_public` is a postgame release perspective. It is not a reconstruction
of what was public during the game. Message records retain their original
`public` or `private` visibility, while their new evidence entries carry
`release_context: postgame_released_negotiation_transcript`. The companion
`released/negotiation_transcript.json` is checksummed and contains the original
message text verbatim.

The public projection excludes reflections, beliefs and belief summaries,
campaign plans, commitment/game/persistent memory, outcome reviews, Winter
assessments, agent folios, raw prompt context, reporter narrative, strategic
editorial content, unreviewed postgame/observatory records, runtime/provider
metadata, and all evidence or references that lead to those records. Unknown
record types and visibility classes fail closed.

`power_limited` is reserved for a future policy. It is intentionally rejected
by this CLI. A future implementation must define which historical messages a
power may receive, whether postgame disclosure applies, how power reassignment
is handled, and a separate closure audit; it must not be inferred from the
current public policy.

## Command and layout

```powershell
.\.venv\Scripts\python.exe scripts\project_report_bundle.py `
  --source <phase-4a-report.json-or-export-root> `
  --output-root <new-separate-destination> `
  --audience-mode postgame_public `
  --raw-experiment-root <raw-experiment-root>
```

An installed checkout also exposes `project-report-bundle`. The raw root is
used only to reject unsafe destinations. Source may be one `report.json` or an
export root; reports are discovered recursively and sorted by experiment/game
identity.

```text
<output>/projection-audit.json
<output>/reports/<experiment>/<game>/<audience-mode>/report.json
<output>/reports/<experiment>/<game>/postgame_public/released/negotiation_transcript.json
```

The destination must not exist and must be separate from both source and raw
trees. Output is built in a sibling staging directory and renamed only after
every game validates. There is no force option. Source bytes and raw artifacts
are never modified. JSON uses stable ordering, stable IDs, LF newlines, and no
wall-clock values, so the same source bytes produce the same result bytes.

## Metadata and closure

New projections keep `diplomacy_report_bundle_v1` and add the backward-
compatible `extensions.audience` object:

- schema `diplomacy_report_audience_v1`;
- policy version;
- `omniscient_research` or `postgame_public` mode;
- reader-safe description;
- explicit static-distribution approval;
- generator, source bundle ID, and source-bundle SHA-256.

A legacy bundle without this object resolves to `omniscient_research` and is
never public-safe. The root audit reports included/excluded record counts,
included/excluded evidence by visibility, unresolved references, disallowed
private references, and absolute paths. Projection fails unless all three
error counts are zero.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_report_bundle_exporter.py tests\test_report_bundle_projector.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\scripts\security_scan.ps1
```

The Viewer must then run its schema/custom validators and build a catalog with
`--require-public-safe`. Historical Phase 4A bundles remain readable research
inputs. Historical layouts that cannot satisfy Phase 4A or contain an
unclassified record/path/reference are rejected instead of receiving guessed
redactions.
