# Campaign reader and completed-run closeout

The narrative is the collection landing view. The existing library, boards, search, correspondence, dossiers, arcs and evidence drawers remain accessible. Catalogs without a story retain their existing behavior. No iframe or paid inference is involved.

The reviewed collection contains both Luna and both Terra games: 32 episodes. Player paths are scoped to the series. Identical player IDs across model arms do not merge; distinct IDs sharing a profile remain distinct. Each game binds its experiment ID, actual source game ID, report checksum and roster assignments. Terra kept identical fixed assignments in both games, with seeds 20261016/20261017.

## Authoring and assembly

1. Finish the experiment and verify terminal records before export. Preserve original histories. Export the factual report using the framework exporter; validate schema, chronology, assignments, outcome and original-source hashes. A release projection is a separate artifact.
2. Establish an outline from original correspondence, returned rationales, reflections, orders and adjudications before consulting existing interpretation. Discover connections through the evidence graph, then inspect original offers and responses. Distinguish board facts, messages, saved rationale, beliefs and retrospective inference.
3. Author connected episodes. The UTF-8 format is `@@ stable-episode-id|report-number|years|title|player-id,player-id`, followed by paragraphs with `[source-id-prefix]` citations. `>source-id-prefix|Exact quotation` creates a quote. `[g1:1901]` resolves a winter snapshot; a full phase can replace the year. Report numbers follow the ordered `--report` arguments, never a guessed source game ID.
4. Export selected graph extracts with `viewer/scripts/export_campaign_evidence.py --graph <evidence.sqlite> --experiment-root experiment-id=<original-root> --episodes <episodes.txt> --output <new-evidence.json>`. It verifies referenced original source hashes and refuses missing/ambiguous citations, unsafe source paths and existing output. This authoring packet does not approve publication. Legacy standalone HTML evidence inventories are also accepted.
5. Supply metadata JSON: `title`, `subtitle`, reviewed `memory_note`, optional `memory_notes` by series, and `games` keyed by catalog ID with `label` and `series`. State memory preparation/copy receipts, presence in saved prompts, evidence of use, and capture limitations separately. Do not inherit another campaign's memory conclusion.
6. Assemble with `python viewer/scripts/assemble_campaign_reader.py --evidence <inventory.json> --episodes <episodes.txt> --metadata <metadata.json> --report unique-catalog-id=<report.json> [--report ...] --output <new-directory>/reports`. It preserves report bytes, resolves citations, validates quotation substrings, and maps original path/hash/pointer to explorer evidence. Phase fallback is labeled. Repeated source game IDs and multiple games in one experiment remain separate. Keep the authored text, metadata and source packet with the local edition.

The process deterministically assembles a human-authored account; it does not claim automatic historical interpretation or editorial validation. Missing sources fail assembly rather than yielding invented replacements. New experiments require new source review and release authorization.

## Public boundary

Research catalogs can load local stories. Public collections require the separate `diplomacy_campaign_story_release_v1` policy on both story and catalog, full-postgame approved reports, exact report bindings and a pinned story checksum. Existing public/player-limited projections cannot fetch a private overlay. The four existing normalized released report files are unchanged.

The separate `campaign-story-v1` release asset contains only `story.json`. Its lock in `publication/campaign-story-release-lock.json` pins ZIP size/hash, exact member size/hash, report bindings and inventory counts. `publication/campaign_asset.py` verifies before extraction. The collection stage permits this exact optional file and verifies the locked hash again. The workflow downloads it separately from code and existing game-data assets.

Story source popups display readable extracts before expandable selected-domain JSON and provenance. Their `raw` field is a normalized selected extract, not an API response envelope. Original JSON manifests/transport records are withheld; relative locators and original source hashes identify provenance. Explicit game-facing Markdown identity/memory originals are downloadable. No prompts, API transport envelopes, credentials, journals or live-arm state are released. A saved-rationale locator containing `#decoded` identifies the original returned JSON field without publishing its surrounding transport.

Memory findings differ by series: Luna has receipt-backed copied historical memory but 606 available sparse successor prompt-memory sections. Terra's parent handoff disables reuse and all 644 first-game plus 628 second-game imported saved sections contain sparse starter memory. Neither supports assuming behavioral influence from the title or similar moves. Terra game one's final board ties England and Italy on eight while the saved outcome names only England; both facts are retained.

## Verification and delivery

Run assembler/export tests, campaign reader tests, existing portable viewer and publication-policy tests, typecheck, public-stage/build verification and the required security scan. Inspect desktop and mobile rendering, every episode, each series/player path, original downloads, source-to-explorer round trips, back/return navigation, quotation exactness and all four report hashes. Automated checks are separate from human editorial acceptance.

The complete static build requires HTTP serving (not file://): `python -m http.server 8847 --directory <bundle>`. Open the printed localhost URL; no API key or network connection is needed. Build with a relative base for an offline bundle, or the repository Pages base for hosting.

After authorized source review: scoped PR, independent review, checks, merge, upload the separately pinned asset, dispatch the reviewed completed-postgame workflow, and verify actual Pages HTML/JS, catalog/story/report hashes, navigation and mobile/desktop views. A successful workflow alone does not establish freshness. Record failures or unavailable review/QA in the issue. This contract does not launch the next experiment; Sol remains separate under #18.
