# Portable archive operations

Phase 4D packages an audience-specific report catalog and its exact transitive
inputs into a relative-base Vite site. It then produces a deterministic ZIP.
The package contains no backend and does not require the raw experiment.

## Build a package

The dated paths below refer to local historical fixtures, which are omitted from a clean source checkout. Supply an existing reviewed catalog and a new output directory for your own package; these commands do not generate the missing reports or grant publication approval.

```powershell
npm run package:report -- `
  --catalog exports/phase-4b-production-catalog-2026-08-08/catalog.json `
  --output-root exports/phase-4d-package-2026-08-08 `
  --audience omniscient_postgame `
  --report-ids game_001,game_002
```

Use `--force` only when intentionally replacing an existing package. The
builder validates catalog and report checksums, JSON Schema, semantic
cross-references, identities, and any approved narrative before staging. Vite
sees only the selected reports, narratives, and public assets.

## Create and verify the ZIP

```powershell
npm run archive:report -- `
  --package-root exports/phase-4d-package-2026-08-08 `
  --output exports/phase-4d-package-2026-08-08.zip

npm run verify:archive -- `
  --archive exports/phase-4d-package-2026-08-08.zip `
  --extract-to exports/verified-phase-4d-package
```

Verification rejects unsafe ZIP paths, duplicate paths, symlinks, missing or
extra files, and size or SHA-256 mismatches. The extraction destination must
not already exist.

For the full clean-extraction browser check:

```powershell
npm run verify:archive:browser -- `
  --archive exports/phase-4d-package-2026-08-08.zip `
  --screenshot-root docs/phase-4d-screenshots
```

This extracts into a fresh temporary directory, verifies the manifest, serves
only the extracted `site/` under a subpath, opens both reports at desktop and
narrow widths, and fails if a request escapes the mounted archive or fails.

## Open an extracted archive

From the extraction root:

```powershell
python -m http.server 8000 --directory site
```

Then open `http://127.0.0.1:8000/`. Direct `file://` loading is not supported.

## Audience safety

The Phase 4D deliverable is `omniscient_postgame`. It contains private
messages and reflections and must not be published as a public archive. The
package builder rejects an attempt to label the current omniscient catalog as
`public`; it does not redact or project sensitive data inside the viewer.

A public archive may be created only from a catalog and reports that already
satisfy the public projection contract. Phase 5A now supplies those isolated
catalogs. Never relabel an omniscient/player catalog as public.

Phase 5C packages also include `ATTRIBUTION.md`, `LICENSE-INVENTORY.md`,
`LICENSE-NOTICE.txt`, and `EVIDENCE-AUDIT-CHECKLIST.md`. Review them alongside
the integrity manifest before redistribution. The versioned source guidance is
under `docs/reproducibility/v1/`.
