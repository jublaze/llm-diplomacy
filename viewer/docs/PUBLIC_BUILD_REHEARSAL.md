# Public-safe static build rehearsal

This documents the implemented `postgame_public` path. It excludes private cognition and memories, so it cannot by itself satisfy the requested full Luna postgame release. That release needs an explicit reviewed policy and tested staging path before deployment; do not weaken these checks or substitute synthetic data for the selected experiment.

Phase 5B packages an already projected Phase 5A `postgame_public` collection.
It never projects or redacts omniscient data in the Viewer.

## Four separate locations

| Location | Purpose | Public deployment input? |
| --- | --- | --- |
| `public/reports` | Default local omniscient research catalog | No |
| `fixtures/phase5a_generated` | Framework-produced public projection source | Yes, after staging validation |
| `public-build-input` | Isolated, generated Vite public directory | Build input only |
| `dist-public` | Verified static deployment artifact | Candidate after human release approval |

Neither staging nor the public build modifies `public/reports`. The ordinary
`npm run build` continues to use that local research catalog and writes
`dist`. The public pipeline uses Vite mode `public`, which reads only
`public-build-input` and writes only `dist-public`.

## Exact rehearsal commands

Start from a clean workspace where `public-build-input` does not exist. The
staging command intentionally refuses overwrite.

```powershell
npm run stage:public-reports -- --source-root fixtures/phase5a_generated
npm run build:public
npm run verify:public-build
npm run test:e2e:public
```

`stage:public-reports` requires explicit `postgame_public` metadata,
`diplomacy_audience_policy_v1`, and static-distribution approval. It rejects
legacy, omniscient, unknown, mixed, malformed, catalog-mismatched, overlapping,
and raw-experiment inputs. It creates `public-build-input/public-build-audit.json`.

`build:public` re-verifies staging before invoking TypeScript and Vite. The
default base is `./`; a reviewed hosting subpath can be rehearsed without code
changes:

```powershell
$env:PUBLIC_BASE_PATH='/review/diplomacy/'
npm run build:public
Remove-Item Env:PUBLIC_BASE_PATH
```

Absolute URL schemes and parent traversal are refused. Source maps are
disabled.

`verify:public-build` scans the completed `dist-public` bytes. Its audit is
`public-build-audits/verification-audit.json`. A nonzero finding makes the
command fail.

## Local preview only

```powershell
npm run preview:public -- --port 4175
```

Open `http://127.0.0.1:4175/`. The command re-verifies `dist-public` before
serving and binds to loopback. Stop it with Ctrl+C. This is a deployment
rehearsal, not a deployment or publication approval.

The public UI calls negotiations a “postgame-released negotiation transcript.”
That wording does not claim the correspondence was public during play.
