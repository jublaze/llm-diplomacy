# Offline viewer-pipeline studies

`scripts/run_viewer_studies.py` runs seven one-game demonstrations through the framework runner, report exporter, audience projector, and local `viewer/` package's validators and catalog builder. Every effective provider must be the built-in `fake` provider. It ignores prompts and persona semantics, so these runs test plumbing only; they do not test negotiation quality, specialist skill, or memory learning.

The selectable comparisons are:

- `ticks`: one versus three negotiation ticks, with the same seven Rowan identities, powers, seed, game bound, and fake-provider configuration.
- `roster`: seven Rowan-derived identities versus Atlas, Dorian, Helena, Iris, Metternich, Selene, and Shaver. This is a compound roster intervention, not an estimate of any individual specialty.
- `memory`: a warmup followed by a treatment follow-up that reuses explicitly synthetic marker text seeded into the treatment folios, compared with a fresh control follow-up. The harness checks the saved provider prompt for the marker in every treatment player's input and its absence in the control. The marker is a visibility probe, not learned memory or game evidence.

Run from the framework root with Node available and dependencies installed in the local `viewer/` package. The harness does not install packages or invoke npm scripts. Choose a new short output directory outside the checkout; it must not already exist. For example:

```powershell
$studyRoot = Join-Path $env:TEMP ("ld-study-" + [guid]::NewGuid().ToString("N"))
python scripts/run_viewer_studies.py --study all --output-root $studyRoot
```

The `--viewer-root` option remains available for a separate checkout or an exported monorepo candidate. From the candidate's `framework/` directory, point it to the adjacent package:

```powershell
$studyRoot = Join-Path $env:TEMP ("ld-study-" + [guid]::NewGuid().ToString("N"))
python scripts/run_viewer_studies.py --study all --output-root $studyRoot --viewer-root ..\viewer
```

Use a short path on Windows to avoid path-length failures. The command preflights every selected source config, effective provider override, game bound, viewer tool, and output path before creating the destination. It rejects existing destinations, path links/junctions, source overlap, and non-fake effective provider configuration. It leaves partial evidence in place if a run fails; inspect `study_manifest.json` and do not reuse that destination.

The manifest records each arm's effective configuration hash, source template hash, stable player-to-power mapping, provider settings, exact subprocess commands and captured output, measured run metrics, report/catalog paths, and memory-fixture provenance. Negotiation tick counts come from `negotiation_tick_participants` events; provider request attempts come from `provider_attempts.jsonl` and are reconciled with `provider_usage.json`. Phase-output files can be overwritten when negotiation has multiple ticks, so file counts are not reported as invocation counts. The fixed fake provider may produce outputs that trigger runtime repair or fallback; these counts are reported as mock plumbing behavior. Each arm has its own fresh viewer catalog because the report bundle uses the same game ID (`game_001`) in every independent run. The manifest indexes all arm catalogs; IDs are not rewritten to combine separate games.

The harness checks each one-game run completed, exports an omniscient postgame bundle, creates a postgame-public projection with a zero-leak audit, runs the viewer schema and semantic validators on both reports, and builds a new catalog with `--require-public-safe`. The warmup report follows this same pipeline; the synthetic memory seed happens only after the warmup completes. All study outputs are local generated artifacts and may contain private omniscient data. Do not publish them wholesale; use only the separately audited public projection/catalog and review it before distribution.

The paired seed and assignment hold fixed the listed setup variables but do not make these mock runs evidence about strategic outcomes. Live-provider research requires a separate approved plan, budget, and execution authorization. Historical viewer fixture tests remain distinct from the fresh-bundle validation performed here.

## Unlaunched advanced-profile proposal

`config/examples/advanced_solo_1912_unlaunched.yaml` is a dry-run-only proposal, not a launch command. It uses five advanced profiles (Birsan, Dolos, Metternich, Shaver and Major) plus isolated Birsan II and Major II copies, fixed assignments, one game ending no later than 1912, three negotiation ticks and 2,000 output tokens per response. The proposed budget caps 3,500 provider attempts, 20 million input tokens, 5 million output tokens and $75 estimated standard-tier uncached input/output cost. At the configured rates, filling both token ceilings would cost up to $90, so the $75 cost guard would stop the run first; these limits do not authorize spending.

An offline starting-board prompt-size probe using `PromptContextBuilder` measured 24,416–26,923 characters (about 6,104–6,731 tokens using a rough four-characters-per-token estimate) for the seven submit-orders prompts, and 22,217–23,756 characters (about 5,555–5,939 rough tokens) for negotiation prompts. This is a size estimate, not model-tokenizer accounting. It shows why the former 5-million input-token ceiling was likely to stop a full 1912 run early; it does not predict later prompt growth or actual spend. Reconcile the roster, provider-call estimate, token caps and spend guard against the intended experiment before launch.

`config/model_profiles/openai_gpt_6_1_sol.yaml` targets OpenAI Responses with `gpt-6.1-sol` at low reasoning effort and no temperature. Supported reasoning efforts are low, medium, high, xhigh and max. The configured $0.002/1K input and $0.010/1K output rates are standard uncached estimates from the [model documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [pricing page](https://platform.openai.com/pricing), checked 2026-10-16. Cached input, cache writes, long-context, service-tier and regional prices are not represented; the estimate is not a hard cost ceiling. Recheck pricing and projected use before launch. No API call or game launch is part of this preparation.
