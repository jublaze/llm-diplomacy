> Publication staging update: original code/docs now have the root MIT LICENSE (copyright 2026 jublaze). This staging copy implements the reviewed full-postgame publisher described in `publication/README.md` at the monorepo root. Older preparation notes below record why the prior synthetic/public-only route was insufficient; they are not a claim that this implementation is still absent. No external release has occurred.

# Pages preparation and release boundary

Current publication scope (2026-10-28): prepare a fresh framework source repository and a separate deployment artifact containing the completed Luna experiment. The owner requested actual correspondence and player memories in the viewer, not a synthetic substitute. Public `jublaze/llm-diplomacy` and MIT for original code are proposed; the final reviewed contents and copyright line remain with the release owner. No repository, upload or deployment has been created by this preparation.

The existing synthetic workflow below is a build rehearsal only. It does not implement the requested Luna publication. Luna's local bundle has 56 phases, 1,503 bilateral messages, 877 orders and 357 private records (343 reflections, seven game memories and seven persistent memories). Private visibility describes information during play; deliberate postgame release must preserve that distinction.

The current `postgame_public` projector deliberately excludes private cognition and memory. Using it alone would lose requested material. The current static builder also rejects omniscient/research bundles. A reviewed release policy and tested staging path are required before publishing the selected full postgame content. Do not merely flip an approval flag, weaken the existing policy or upload the raw experiment tree. Retain original evidence bytes and hashes, and record every derived-data omission or redaction in a separate manifest.

Keep the main source branch framework-only. A proposed deployment workflow can download a separately reviewed, checksummed data asset from a release in the same repository and combine it with viewer source; both the asset and Pages output would be public. A dedicated deployment branch is another option, but also publishes its data/history. Neither route has been selected or enabled. No extra repository is required.

`viewer/` is the maintained package. The sibling is a backup, per commits
`c5a60a7` and `1fb6b21` and `MAINTENANCE_BACKLOG.md`.

`.github/workflows/viewer-pages-preview.yml` is manually triggered and builds a
synthetic public example. It uploads a Pages artifact but has no deployment job
or Pages write permission. Nothing has been pushed or published.

For a local project-site build, from `viewer/`, choose new source/stage paths:

```powershell
node --experimental-strip-types scripts/build_public_demo.ts public-build-demo
node --experimental-strip-types scripts/public_build.ts stage --source-root public-build-demo --output-root public-build-input-pages-demo
$env:PUBLIC_BUILD_INPUT_ROOT='public-build-input-pages-demo'
$env:PUBLIC_BASE_PATH='/example-project/'
npm run build:public
node --experimental-strip-types scripts/public_build.ts verify-build --dist-root dist-public --audit-path public-build-audits/pages-demo.json
```

The generator and staging commands refuse existing destinations. Repeat just
build/verify for an existing prepared stage. The synthetic content has no model
outputs, correspondence, player state, or historical results. Ordinary `npm run
build` copies the local development asset collection and must not be deployed.
Only the audited public-mode output is a publication candidate.

Owner decisions before deployment: GitHub owner/repository and visibility,
license, user-site `/` versus project-site `/<repository>/` base, Actions/Pages
availability for that account, Pages publishing source, and deployment environment
protections. A later deploy job needs `pages: write`, `id-token: write`, a dependency
on the build, and the `github-pages` environment. No such job is enabled here.
See [GitHub's workflow requirements](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

MIT is proposed for original code; no project license file has been added yet. Keep the map's CC BY-SA 3.0 attribution and
DATC MIT provenance. Dependency license metadata is present but legal compatibility
and current advisories remain unverified. Use `export_publication_candidate.py`
for the source-only boundary and separately review any additional model/player
artifacts for provenance and intended audience. Such artifacts are not inherently
credentials. The owner has requested Luna's full postgame content; the existing local bundle has not yet passed the content review and release-specific staging checks.

Local validation: public build at `/example-project/` passed; audit found one
synthetic bundle, three phases, zero private/omniscient records, absolute paths,
raw artifacts, unresolved references or source maps. The artifact-only workflow
has not run on GitHub. No new browser/network QA was attempted; earlier approved
local Luna QA remains separate from this static audit.
