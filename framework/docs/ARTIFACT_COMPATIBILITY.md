# Artifact compatibility sprint

Outcome: reject unsupported raw versions before publishing a report; keep supported legacy evidence unchanged.

Scope: raw envelopes consumed by report export, shared checks with ArtifactValidator, compatibility policy and offline regression tests. No migration, orchestration refactor, model calls, or historical artifact edits.

## Version inventory and policy

| Family | Supported contract | Legacy behavior |
| --- | --- | --- |
| Raw manifests, summaries, snapshots, orders, resolutions, messages, events, cost summaries | schema_version `1.0` | Version is required; missing or other versions rejected |
| Core event payloads | optional `core_events_v1` marker | Missing marker accepted; unknown event types retain envelope checks |
| Provider usage | raw `1.0`, accounting_version `provider_attempts_v1` | Missing ledger means actual attempt usage unavailable; old lifecycle cost is not equivalent |
| Viewer bundle | `diplomacy_report_bundle_v1` | Validated by its packaged JSON Schema, not the raw validator |
| Audience metadata | `diplomacy_report_audience_v1`, policy `diplomacy_audience_policy_v1` | Missing metadata is unsafe research-only; unsupported explicit versions rejected |
| Reflection and cognition payloads | Consumer-specific named versions | Opaque payloads in reports; fresh runtime gates and reflection readers own semantic compatibility |

Additive optional fields may retain the current version. Changed meanings, removed fields, or changed required types need a new version and an explicit reader adapter plus fixtures before adoption. Never guess that a future major version is compatible. The raw directory validator is not a universal validator for exported bundles or standalone cognition documents.

Absent legacy status and started/stopped/failed counters remain absent/unknown. Completion requires terminal game evidence; no absent failure counter is converted to a verified zero. Unknown provider costs remain unknown. Report export copies private cognition as evidence without claiming to validate its semantics.

Dependencies: existing raw writers, report schema and audience policy. Acceptance: current and legacy v1 exports succeed; missing, numeric and future raw versions fail with artifact location; failure publishes no report; source bytes remain unchanged. Offline tests cover manifests, costs, snapshots, orders, resolutions, chronology and terminal events. Existing message, audience, event and bundle tests remain required.

Risk: previously unchecked malformed or future raw artifacts now fail export. Recovery is an explicit compatible reader or corrected producer, never relabeling historical evidence. Generic ArtifactValidator shape checks remain stricter than the exporter's minimum required input fields.

Validation: full offline suite 435 passed, 2 skipped; final exporter suite 49 passed after adding four message-version cases and correcting its public-message diagnostic path. Security scan passed. No paid inference.
