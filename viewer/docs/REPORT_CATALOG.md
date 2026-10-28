# Viewer Report Catalog v1

For publication, the current pipeline accepts only its implemented public audience policy. The owner-requested full Luna postgame release also includes private-during-play memories and needs a separately reviewed policy and staging implementation. A UI filter or an edited approval flag cannot provide that boundary. The local commands below do not publish data.

The viewer loads a small catalog before it downloads any report bundle. The
catalog is a viewer-side asset index; it is deliberately separate from the
framework-owned `diplomacy_report_bundle_v1` contract.

## Browser asset layout

```text
public/reports/catalog.json
public/reports/bundles/<catalog-report-id>/report.json
```

The browser requests `reports/catalog.json` relative to Vite's configured base
URL. Every `report_url` in the catalog is relative to `catalog.json`. There are
no filesystem APIs or absolute workstation paths at runtime.

## Catalog contract

```json
{
  "schema_version": "diplomacy_report_catalog_v1",
  "reports": [
    {
      "id": "game_001",
      "experiment_id": "example_experiment",
      "artifact_manifest_sha256": "<64 lowercase hexadecimal characters>",
      "bundle_id": "example_experiment:game_001:v1",
      "label": "Game 1",
      "title": "Game 1: completed campaign",
      "years_played": [1901, 1910],
      "outcome_label": "Leader at the scheduled 1910 cutoff",
      "schema_version": "diplomacy_report_bundle_v1",
      "audience": "omniscient_postgame",
      "audience_mode": "omniscient_research",
      "audience_policy_version": "legacy_unversioned",
      "audience_description": "Legacy research bundle without explicit audience metadata; not approved for static public distribution.",
      "approved_for_static_public_distribution": false,
      "map_id": "standard_v1",
      "report_url": "bundles/game_001/report.json",
      "sha256": "<64 lowercase hexadecimal characters>",
      "fixture": false
    }
  ]
}
```

`source_game_id` is optional and is used when several audience projections of
one source game need distinct catalog IDs. `experiment_id` and
`artifact_manifest_sha256` are optional provenance summaries for compatibility
with older catalogs. An optional `narrative` reference is supported for a
separately approved, checksummed historian overlay; it never replaces the
factual report.

The catalog contains only list/detail metadata. Messages, orders, private
records, board states, and evidence records remain solely in the lazily loaded
report.

Validation rejects duplicate or unsafe IDs, unsupported versions and
audiences, invalid checksums, unsafe/absolute URLs, parent traversal, absolute
local paths in reader metadata, and invalid narrative bindings. A fetched
report is accepted only after checksum, JSON Schema, semantic, bundle/game ID,
and audience checks all pass.

## Current local collection

The approved Phase 4A sample is the default local development collection:

```powershell
npm run build:catalog
npm run dev
```

`build:catalog` recursively discovers the two exported reports under
`fixtures/phase4a_generated`, validates them, and stages unchanged copies into
`public/reports`. It intentionally uses the explicit `--force` option for that
generated destination. The historical hand-curated fixtures remain available
for contract/editorial regression testing and can be staged explicitly with:

```powershell
npm run build:catalog:fixtures
```

The current local collection is omniscient and contains private records. The
perspective and evidence labels are explanatory, not authentication. Do not
publish `public/reports` or `dist/reports` without first building a suitable
public audience projection.

For a public-safe catalog, first use the framework Phase 5A projector, then
use the dedicated `stage:public-reports` command. It wraps the catalog builder
in the Phase 5B isolated-input and artifact-audit boundary. Direct use of
`--require-public-safe` remains an implementation primitive, not the public
packaging entry point. See `PUBLIC_BUILD_REHEARSAL.md` for exact commands.

## Adding a future exported game

No TypeScript, JSX, CSS, or routing change is required.

1. Export completed games with the framework Phase 4A exporter into a new
   destination.
2. Build a new catalog destination and review it before replacement:

   ```powershell
   npm run build:catalog:production -- --source-root <export-root> --output-root <new-catalog-root>
   ```

3. Validate `catalog.json`, confirm its audience, hashes, IDs, and relative
   paths, then stage that reviewed collection at `public/reports`. Re-running
   against an existing destination fails unless the operator explicitly passes
   `--force`.
4. Run `npm test`, `npm run test:e2e`, and `npm run build`.

The builder refuses source/output overlap in either direction, validates every
report before replacing an explicitly forced destination, discovers arbitrary
nested `report.json` files deterministically, sorts entries, preserves report
bytes, and emits SHA-256 checksums. Raw framework experiments are never input
to this viewer command; use the framework exporter output instead.

## Open a completed local experiment

From the framework root, export into a new ignored destination:

```powershell
.\.venv\Scripts\python.exe scripts\export_report_bundle.py --experiment-root experiments\luna1912-1018-r2 --output-root viewer\exports\luna1912-1018-r2-local --all-games --audience omniscient_postgame
Set-Location viewer
npm run build:catalog:production -- --source-root exports/luna1912-1018-r2-local --output-root public/catalog/luna1912-1018-r2
$env:VITE_LOCAL_REPORT_CATALOG = '/catalog/luna1912-1018-r2/catalog.json'
npm run dev -- --host 127.0.0.1
```

The export and catalog commands refuse existing destinations. Once prepared,
repeat only the last two commands to reopen this campaign. This keeps the
sample collection intact. `VITE_LOCAL_REPORT_CATALOG` applies only to the dev
server; production builds retain the standard catalog path. Clear it with
`Remove-Item Env:VITE_LOCAL_REPORT_CATALOG` to return to the sample collection.
The omniscient local bundle contains private records. Keep this server on
loopback and use the audited public-projection workflow before distribution.

## Runtime behavior and limitations

The app caches validated bundles in memory for the browser session. Direct
hash links retain game, phase/year, power, and evidence state while the needed
bundle loads. Switching games clears game-specific route state. Catalog,
network, JSON, contract, checksum, and identity failures render a safe error
instead of partial report content.

This is a static local asset workflow. It is not an upload system, remote
catalog, authenticated store, deployment pipeline, or privacy boundary.
