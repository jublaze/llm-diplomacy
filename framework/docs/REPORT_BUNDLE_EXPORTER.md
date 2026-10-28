# Diplomacy Viewer v1 report-bundle exporter

The framework exports completed games with `scripts/export_report_bundle.py`.
The command is read-only with respect to the completed experiment and rejects
an output directory inside that experiment tree.

```powershell
.\.venv\Scripts\python.exe scripts\export_report_bundle.py `
  --experiment-root <completed-experiment-root> `
  --output-root <separate-export-destination> `
  --all-games `
  --audience omniscient_postgame
```

Use repeatable `--game <game-id>` arguments instead of `--all-games` to select
specific discovered games. The output root must not exist: the exporter always
refuses replacement and has no force mode. Phase 4A supports only the
`omniscient_postgame` audience. An installed checkout also exposes the same
command as `export-report-bundle`.

The output layout is:

```text
<output-root>/reports/<experiment-id>/<game-id>/omniscient/report.json
```

## Guarantees

- The input inspector requires raw layout/schema version `1.0`, a completed
  run summary, a completed `game_end` result, chronological snapshots, phase
  orders and resolutions, reporter history, roster folios, and root metadata.
- Game directories are discovered; production code does not contain a fixed
  Game 1/Game 2 list.
- Evidence paths are relative to the experiment root and carry SHA-256 hashes.
- Each normalized public order cites its exact final-public-order JSON pointer,
  plus the submitted-order and adjudication sources. The final public record,
  not private intent, is authoritative.
- Public/global correspondence stays public, bilateral correspondence and
  player records stay private, and selected postgame observatory records stay
  explicitly `omniscient_postgame`.
- Phase reflections, structured game memory, and persistent-memory text are
  retained as private records. This preserves belief summaries, objectives,
  plans, commitment/relationship updates, outcome reviews, and Winter
  assessments when those fields exist in the raw record.
- Safe structured observatory outputs include board evolution, Diplomacy
  metrics, and reporter summary. Prompt/reflection diagnostics and the bulky
  raw-evidence observability expansion are intentionally not embedded.
- Phase order is chronological rather than filename-lexicographic.
- Message IDs reused across phases or negotiation ticks receive stable
  phase/tick-qualified export IDs. Identical copies in inbox/outbox records
  collapse; conflicting content within one phase/tick still fails. Source
  pointers and message text remain exact.
- Absolute summary artifact paths inside the experiment become relative paths
  in exported metadata; paths outside the experiment are rejected. Raw summary
  files and their evidence hashes remain unchanged.
- Duplicate adjustment `unit_id` values retain every order using stable
  occurrence suffixes (`:2`, `:3`, and so on).
- Every selected game is built, checked against the packaged viewer v1 JSON
  Schema, semantically cross-checked, and evidence-audited in staging before
  the new output root is atomically published.
- Factual output contains no Game 1-specific editorial layer and no provider
  call. Identical raw inputs produce byte-identical JSON.

The packaged contract is
`src/llm_diplomacy/reporting/contracts/diplomacy_report_bundle_v1.schema.json`.
It must remain byte-identical to the viewer's
`contracts/diplomacy_report_bundle_v1.schema.json` until a deliberate contract
version change is approved.

## Verification

Framework-focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_report_bundle_exporter.py -q
```

After exporting, run the viewer's JSON Schema and semantic validators against
each generated `report.json`. The normal framework test suite does not require
or read a raw experiment directory.

## Compatibility and exclusions

The exporter currently supports completed raw-layout `1.0` experiments on the
standard map and produces factual `omniscient_postgame` bundles only. It does
not generate editorial narrative, public/player projections, SVG geometry,
catalogs, or archives. Older experiments fail with the exact missing path or
unsupported schema version rather than receiving invented defaults.

Runtime request/response transcripts, retry/error bodies, prompt and reflection
diagnostics, outbox staging, API configuration secrets, and Markdown/HTML
derivatives are excluded. Where a structured JSON artifact is the canonical
source, a rendered Markdown copy is not duplicated in the bundle. The raw
experiment remains the source of truth for every excluded or indexed artifact.

Phase 4A output is research-only input to the framework-owned audience
projector documented in `REPORT_BUNDLE_PROJECTOR.md`. Do not stage a Phase 4A
bundle for public static distribution merely because the Viewer can read it.
