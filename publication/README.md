# Full completed Luna release

`diplomacy_full_postgame_release_v1` is a separate, source-locked postgame release policy. It retains private-during-play information intentionally. It does not reinterpret that information as public during play or relax the existing `postgame_public` policy. `isStaticPublicBundle` remains false for full-postgame data; the new staging/verifier is the only deployment path for this policy.

The initial publisher requires the exact reviewed original normalized report plus exact completed run-summary/checkpoint hashes. It allows the report root and known message/order/private/evidence row fields, retains the intended game payloads, and removes declared operational metadata. Source hashing rejects any unreviewed addition, including inside an intentionally retained domain payload. It preserves all 56 phases, 1,503 messages, 877 orders, 343 reflections, seven game memories, seven persistent memories, seven roster entries and 2,914 evidence entries. Subsequent staging requires the independently reviewable source lock for the exact released report and receipt, not a boolean flag alone.

Original evidence hashes identify retained raw sources, not sanitized public bytes. Every evidence entry states that raw files are unavailable and lists JSON pointers to any included normalized content. Five entries have no normalized target and explicitly say `raw_unavailable`. Seven game-memory archive locators are normalized relative to the original experiment and labeled unpublished. Raw API transcripts, prompts, invalid attempts, error bodies, journals and new live-arm state are not included. `release-receipt.json` declares the exact omissions and terminal provenance.

## Separate source and data

The main source tree contains code, contracts, authored profiles/configs, license notices and the small release lock only. The actual report and receipt live in `luna1912-full-postgame-v1.zip`, uploaded separately only after release-owner review. The workflow downloads that asset from the same repository's `luna1912-full-postgame-v1` release, checks its locked SHA-256 before extraction, permits exactly two filenames and checks their hashes/counts/closure again. No old repository history is required.

Local verification, from the monorepo root, with a separately obtained reviewed zip:

```powershell
python publication/release_asset.py extract --asset <reviewed-zip> --data-root <new-data-directory> --lock publication/luna-release-lock.json
$env:LUNA_RELEASE_DATA=(Resolve-Path <new-data-directory>).Path
python -m unittest discover -s publication -p test_full_postgame.py
Set-Location viewer
npm ci --ignore-scripts
npm test
npm run test:full-postgame
npm run typecheck
node --experimental-strip-types scripts/full_postgame_build.ts stage $env:LUNA_RELEASE_DATA full-postgame-input
node --experimental-strip-types scripts/full_postgame_build.ts verify-stage full-postgame-input
$env:PUBLIC_BUILD_INPUT_ROOT='full-postgame-input'
$env:PUBLIC_BUILD_OUTPUT_ROOT='dist-full-postgame'
$env:PUBLIC_BASE_PATH='/llm-diplomacy/'
npx --no-install vite build --mode public
node --experimental-strip-types scripts/full_postgame_build.ts verify-build dist-full-postgame
```

Staging and extraction refuse existing destinations. Tests requiring the original private report are preparation-only and skip in public CI; the locked released-data tests run with `LUNA_RELEASE_DATA`. Temporary test directories must be writable and outside a path segment named `experiments`, because the original public-only pipeline deliberately rejects such locations. None of these commands runs a model or a game.

## Licenses and delivery

Root LICENSE covers original code and authored docs, copyright 2026 jublaze. Map adaptations remain CC BY-SA 3.0 with Martin Asal attribution, source, license and modifications visible via the site's credits link. DATC retains its upstream MIT notice. `THIRD_PARTY_NOTICES.json` includes locked dependency metadata and locally supplied license/copyright notices; absent optional platform packages and saxes have metadata-only notices where no license text was supplied locally. The package does not redistribute node_modules or those optional binaries. Original map/data/third-party rights are not relicensed under the original-code MIT grant.

The manually triggered full-postgame workflow uses standard Ubuntu runners. Build has only `contents: read`; the separate deploy job has `pages: write` and `id-token: write`, scoped to the `github-pages` environment. It uses GitHub's ephemeral token/OIDC; no PAT or persistent secret is needed. The separate synthetic preview workflow remains an offline build rehearsal and cannot deploy.

Before external writes, the release owner must review the source/asset manifests, license boundaries and exact workflow. Future authorized actions are: create public `jublaze/llm-diplomacy`; commit only the clean source manifest into fresh history; push that new history without rewriting the local archive; upload the pinned data asset in the named release; set Pages source to GitHub Actions; set environment protections as desired; manually dispatch this workflow and verify deployment/checksums. No step has been performed by preparation. Repository/workflow write authority is needed for creation/push and release-asset upload; Pages settings authority is needed for configuration. The workflow itself does not need `contents: write`.
