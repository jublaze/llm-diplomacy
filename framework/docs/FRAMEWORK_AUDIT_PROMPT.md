# Offline Diplomacy framework audit

For a separate audit session, explicitly read `C:\Users\jaken\PycharmProjects\AGENTS.md` and `C:\Users\jaken\PycharmProjects\llm-dplomacy\AGENTS.md` before examining the repository. The project is `C:\Users\jaken\PycharmProjects\llm-dplomacy`.

Audit the local framework and existing experiment evidence for correctness, reproducibility and scientific validity. Work offline. Do not call a provider, use credentials, start a game, change source, alter an experiment, publish, or interfere with the live-run owner. Coordinate before reading or touching any live-run recovery state. Keep findings, test artifacts and historical run evidence immutable.

Start with the exact source revision, configuration, map/schema/profile assets and artifacts for each claim. Report the relevant commit or file hashes, a minimal deterministic reproduction, observed behavior, affected code versions and the specific runs or decisions that used them. Mark each statement as confirmed, tested only in a fixture, or a hypothesis. Do not generalize one reproducer into a claim that all previous games or results are invalid.

Audit these connected areas:

- Rule engine phase progression, order validation and adjudication, forced disbands, supply-center ownership, elimination, victory and draw termination.
- Legal-action catalogues versus engine acceptance and resolution, including split coasts, convoys, standoffs, retreats, builds and disbands. Identify where generated options can diverge from actual engine behavior.
- Provider routing, timeout semantics, retry/fallback limits, request identity, returned-response recovery, exception classification and unknown remote outcomes.
- Budget reservations, known usage, uncertain exposure, cached-response replay and per-game/report aggregation. Distinguish configured-price estimates from actual billing.
- Context construction, hidden/incomplete information, message delivery, memory reads/writes, reflection/negotiation normalization, truncation, dropped fields and response attribution.
- Checkpoint identity, journal integrity, staging and publication, resume idempotence, crash boundaries, locks, monitor/status signals and preservation of prior artifacts.
- Metrics, summaries, pilot reports, viewer/export paths and provenance. Check that evidence describes received, accepted, rejected, skipped, cached and replayed decisions without counting them as new inference.
- Historical validity: map each confirmed bug and behavior change to the exact runs, phases and decisions that encountered it. Separate adjudication errors, model invalid outputs, strict-guard false positives, provider failures and incomplete runs.

For every finding, include severity and practical impact, exact source/evidence references, a small offline reproducer or a clear reason one is unavailable, tests run and not run, and the narrowest safe repair. Prioritize failures that change game state, silently lose player intent, repeat paid requests, corrupt accounting, or make results impossible to reproduce. Do not revive unrelated retired providers or make broad claims about model capability from a single game.

Return the highest-impact findings first, then a version-to-evidence matrix and a short list of bounded repairs. Keep this audit separate from code changes and any live-game recovery decision.
