# Narrative pipeline operations

Phase 4C keeps every narrative artifact separate from the factual
`report.json`. Normal CI and the commands below make no live provider call.

## Build a review-pending package

```text
npm run build:narrative -- --report <report.json> --output-root <narratives> --audience omniscient_postgame
```

The command validates the factual report, computes its SHA-256, and writes only
under `<narratives>/<game_id>/`:

- `plan.json` — structured chronology, factual observations, visible source
  IDs, and allowed targets; it contains no prose claims.
- `outline.json` — deterministic proposed sections, allowed claim types, and
  minimum citations.
- `template-candidate.json` — deterministic, cited sample prose for review.
- `request.json` — saved provider request metadata plus the exact plan and
  outline. Its fixture model, version, temperature, seed, and prompt-profile
  version make normal tests reproducible.

Existing game output is not overwritten without `--force`. Generation verifies
that the factual report checksum is unchanged before publishing the staged
narrative directory.

## Validate a package

```text
npm run validate:narrative -- --report <report.json> --plan <plan.json> --outline <outline.json> --narrative <candidate.json>
```

Validation covers all three narrative contracts plus the factual report:
bundle and checksum binding, phase chronology, deterministic observations,
target/source resolution, evidence-type agreement, audience visibility, plan
allow-lists, and at least one citation for every claim.

## Provider boundary and quarantine

`NarrativeProviderAdapter` accepts a saved `NarrativeProviderRequestV1` and
returns an unknown raw response. Normal tests use
`FixtureNarrativeProviderAdapter`; no HTTP/provider implementation or
credential handling is included.

`processFixtureProviderResponse` validates a raw response before replacing
`provider-candidate.json`. Invalid output is retained under
`<narratives>/quarantine/<run_id>/` with its validation errors, while the last
valid candidate remains unchanged. A valid provider response is still a
candidate, not an approval.

## Human approval and catalog association

Automated generation never creates `approved.json`. After the required human
review, an authorized operator may place approved artifacts at
`<approved-root>/<game_id>/approved.json` and explicitly build a catalog with:

```text
npm run build:catalog:production -- --source-root <reports> --output-root <catalog-output> --approved-narrative-root <approved-root>
```

The catalog builder revalidates each approved artifact, binds it to the exact
report checksum and audience, copies it behind a relative URL, and aborts the
whole staged catalog if any approved artifact is missing or invalid.

The viewer independently verifies the narrative file checksum, report
checksum, audience, JSON Schema, citations, and semantic contract. A rejected
overlay never hides the factual reader.
