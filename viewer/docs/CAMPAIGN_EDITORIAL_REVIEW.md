# Source and editorial review contract

Keep source inventories, authored prose, evidence selections, review receipts and builds as separate versioned inputs. Generated research files stay local unless separately approved for publication. Preserve earlier editions and receipts; a revised artifact needs a fresh hash binding, not a rewritten old approval.

## Establish the account

Before reading the prior narrative, establish a source-based outline of each game and player: identity and power, objectives, negotiations, major orders, turning points and terminal outcome. Graph edges discover candidates; the original offer and response establish the actual bargain. Read messages in tick order with named recipients. Verify pivotal quotations as exact substrings of the original selected field.

Inventory the original tree, not only graph nodes or final saved files. Include:

- Delivered inboxes, staged-only attempts and message purpose fields. Undelivered text supplies sender-side evidence, not recipient knowledge.
- Decoded raw returns and saved result copies: explanations, passivity reviews, rationales, relationship updates, withheld information, reflections and cleanup. Record a missing field, generated placeholder or malformed return distinctly.
- Every retained earlier journal tick and rejected/timeout attempt, associated with decision/attempt IDs and acceptance, validation, delivery and commit evidence. A valid attempt alone is not proof of canonical execution. Do not resume an experiment to reconstruct history.
- Initial states, source manifests, identities, profiles, terminal events, actual board ownership and unit statuses. Destroyed entries in historical rosters are not active defenders.
- Preparation/copy receipts, retained initial and terminal memories, captured Persistent Memory, Current Game Memory and private prose inputs. A sparse Persistent Memory section does not mean the whole prompt lacks current-game memory. Copying and configured reuse do not establish supply or use.
- Recovery copies and normalization aliases, with exact duplicate provenance. Hash equality verifies bytes; reading receipts establish substantive review. Never convert retrieval into reading.

`inventory_campaign_returns.py --experiment id=<original-root> [--experiment ...] --output <new-inventory.json>` enumerates retained raw/result fields and journal attempts without exporting their text. It deliberately emits `unmapped` roles for new domain fields. It inventories returned-domain sources only: messages, boards, identities, memory inputs and terminal/configuration sources must be added by their respective inventories. It does not infer canonical acceptance, disposition, substantive reading or source-tree completeness.

Review unknown fields and extend the explicit role/selection allowlists before acceptance. Do not silently omit new authored fields as operational. Operational exclusions need a reason; game-facing scope exclusions also need a scope explanation. Exact duplicates must ultimately reference a substantively read origin with the same value hash. Preserve phase/agent/recipient context even when text repeats.

## Follow decisions and consequences

For every significant bargain, compare the offer, reply, later revision, available legal actions, attempted submission, accepted retry, selected order, adjudication and resulting board. Keep these distinctions explicit where they change interpretation. A support order does not move its unit. Reciprocal moves can bounce rather than exchange. Say a unit entered, displaced, recovered or gained only after checking outcomes and the next board; spring occupation alone is not fall ownership. An omitted retreat is not a voluntary loss when no legal retreat exists. Competing attackers, support cuts and supported-destination exceptions can change which help was necessary.

Evaluate plausible alternatives available at the time. A suspicion is a player's belief; a saved rationale is its recorded explanation; neither supplies unseen messages to another player. A later account can misremember a promise or a live unit. An actual contradiction can be stated firmly without automatically claiming deliberate deception. A decoded rejected attempt can establish attempted cooperation even when the final order does not.

Tell connected episodes with proposals, responses, action and reaction. Preserve quieter continuity concisely. Give space by significance and evidence. Player paths need genuine series-specific trajectory prose and links to material scenes; do not tag every player into every chapter because a source-year packet covered all seven. Mark eliminated periods explicitly. Compare the old draft only after the independent outline; retain interpretations that survive checking without manufacturing disagreement.

## Review ledger

Use `campaign_editorial_review_v2` JSON with these fields:

| Field | Contents |
|---|---|
| `value_encoding` | `canonical_json_utf8_v1`: SHA-256 of UTF-8 canonical JSON for **all** present values, including quoted strings; sorted keys, compact separators, non-ASCII preserved |
| `sources` | ID → `{path, sha256}` for immutable original files |
| `field_inventory` | Field ID → `{source, pointer, value_sha256, role}`; include known omitted and noncanonical fields |
| `field_dispositions` | Field ID → `{status, reviewer, reason, source_sha256, value_sha256, receipt:{path,sha256}}`; status `read`, `duplicate`, `excluded`, or `capture_gap` |
| Duplicate details | `duplicate_of` exact original field ID; exclude cycles and unread origins |
| Exclusion details | `scope_reason` for game-facing material outside the finite scope |
| `required_player_years` | Explicit `[experiment, source_game, player, year]` identities derived from the original roster and chronology |
| `semantic_coverage` | One row per identity: `{identity,status,episodes,reviewer,reason,receipt}`; status `scene`, `continuity`, `eliminated`, or `capture_gap` |
| `artifacts` | Named `{path,sha256}` entries including `story`, `html`, `bundle`, `samples`, `sample_evidence`; bundle is a complete asset-hash manifest with `schema_version: campaign_asset_manifest_v1`, `files: [{path,sha256}]` and `entrypoints: {html,story}` |
| `gates` | Gate name → `{status,reviewer,receipt,inputs:{artifactName:sha256}}` |

Inventories use `campaign_return_inventory_v2`. Earlier v1 raw-string hashes are incompatible; retain their receipts and regenerate a new version. Missing expected members have `presence: absent` and the SHA-256 of the literal non-JSON bytes `absent-member-v1`; the validator verifies absence and requires an exclusion or capture-gap disposition. Decoded empty objects and empty result objects receive explicit unmapped markers; four empty negotiation arrays remain four present fields.

Pointers use JSON pointers, optional `#decoded`, or one-based `/line/N` for JSONL. Keep text values out of a shareable ledger. A ledger can reference local private receipts without publishing them. The accepted role list is in `validate_campaign_review.py`; unmapped or pending fields fail. Capture gaps remain explicit limitations, not invented continuity or an excuse to leave the narrative unwritten.

Run `python viewer/scripts/validate_campaign_review.py <ledger.json>`. Use `--require-gates` for a fully reviewed candidate. The validator resolves original JSON/JSONL/decoded fields, recomputes typed value hashes, checks review bindings to the exact source/value hashes, verifies every bundle asset and its story/HTML entrypoints, rejects unlisted/escaping assets, and checks source/receipt/artifact hashes, dispositions, duplicate identity, required player/year coverage and current gate inputs. It cannot prove the inventory contains every retained source or that a reviewer actually read it. Human inventory reconciliation and semantic acceptance remain necessary. Filling episode tags is not semantic review.

## Independent gates and release hold

First review representative complete episodes factually and literarily before scaling. Preserve the exact samples/evidence hashes and review receipts. Then independently review the complete actual edition, including introductions, all player trajectories, chronology, identity/power mappings, outcomes, quotations and competing explanations. Sample approval is not full-edition approval. Rebind a gate after a change only with a documented comparison of the exact changed inputs and an applicable reviewer decision.

Required gates are `sample_factual`, `sample_literary`, `full_factual`, `full_literary`, `desktop`, and `mobile`. Full prose gates bind the actual story hash. Browser gates bind story, HTML and complete bundle-manifest hashes. Inspect every episode and player path, evidence expansion before raw records, original downloads/checksums, board/explorer round trips, back/forward navigation and responsive layout. Check actual asset requests and errors. Distinguish automated layout checks, visual inspection, independent literary judgment and owner acceptance.

Run assembly/provenance validation, focused viewer and publication-boundary tests, typecheck, and the repository security scan. Keep normalized report hashes unchanged unless an independently authorized factual-data update is part of scope. Deliver a complete local artifact/asset bundle and opening instructions with a short editorial note: material changes, interpretations retained after checking and unresolved limits.

A validated ledger always reports `release_authorized: false`. It does not authorize a merge, asset upload, deployment, next experiment or paid inference. Follow the current owner release hold and project instructions separately.
