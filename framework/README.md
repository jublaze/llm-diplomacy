# llm-diplomacy

`llm-diplomacy` is a research framework for running controlled Diplomacy games with persistent agent identities. It combines a deterministic rules engine, Game Master, scripted and provider-backed agent runtimes, artifact validation, experiment logging, and post-game reporting. The [orchestration ownership guide](docs/ORCHESTRATION_OWNERSHIP.md) describes the current execution path: `PopulationExperimentRunner` prepares player folios and provider routing; `ScriptedExperimentRunner` schedules games and phases, invokes agents and deterministic adjudication, and writes run artifacts; `GameMaster` owns lifecycle stores, snapshots, staged message delivery, and cleanup; the rule engine validates and resolves orders. Reports and observatory pages are derived views.

## What belongs here

- `src/llm_diplomacy/` contains the rule engine, orchestration, provider adapters, artifact contracts, and reporting code.
- `blueprints/` contains authored agent identity sources. `config/examples/`, `config/model_profiles/`, and `experiments/configs/` contain authored offline and provider-backed research configurations; this quickstart uses the explicitly named fake-provider example only.
- `agents/`, `agent_state/`, `logs/`, and `run_logs/` are generated or runtime state. `experiments/` holds generated runs, except for authored configs in `experiments/configs/`. Runtime directories can contain persistent memories and private experiment artifacts; they are not publication source.
- `viewer/` contains the report viewer source, schemas, tests, build scripts, and licensed standard-map asset. It excludes historical report fixtures and generated catalogs; see its [package README](../viewer/README.md) for portable checks. The publication exporter can still select an explicit external viewer root for comparison. See [publication readiness](docs/PUBLICATION_READINESS.md) and [offline viewer experiments](docs/VIEWER_EXPERIMENTS.md).

## Offline quickstart

Python 3.10 or later and Node.js 22.12+ (22.x), 24.x, or 26+ are required for the complete framework test suite because report-export tests invoke the local viewer validators. The fake game itself is Python-only. From PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[dev]
npm --prefix viewer ci --ignore-scripts
.\.venv\Scripts\python.exe -m pytest
$runRoot = Join-Path $env:TEMP ("llm-diplomacy-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe scripts/run_population_experiment.py --config config/examples/per_player_2x2_offline_1901.yaml --output-root $runRoot --agents-root (Join-Path $runRoot "agents")
```

The example uses fake providers, writes artifacts below the unique temporary output root, and makes no external model requests. Its `fresh` mode deletes folios with matching IDs under `--agents-root` before rebuilding them, so keep that directory inside a new isolated output root. On Windows, use short output paths under `$env:TEMP` if long profile paths approach `MAX_PATH`. The security scanner accepts a candidate directory via `-Root`:

```powershell
$candidateRoot = '.\experiments\build-loop-<run-id>\publication-candidate'
.\scripts\security_scan.ps1 -Root $candidateRoot
```

## Provider-backed runs

API-backed examples require their configured environment variable; the optional native Codex provider uses its saved ChatGPT login instead ([native provider setup and limits](docs/NATIVE_CODEX_PROVIDER.md)). Provider-backed examples are for separately authorized research and may incur cost. Do not treat a dry run as evidence that a live request was made or that an experiment is scientifically valid. Inspect provider budgets and output destinations before any live run.

Bundled OpenAI model profiles read `LLM_DIPLOMACY_OPENAI_API_KEY`. Set it in the PowerShell 7.1+ window used to launch a game (`-MaskInput` requires PowerShell 7.1+):

```powershell
$env:LLM_DIPLOMACY_OPENAI_API_KEY = Read-Host 'Diplomacy OpenAI API key' -MaskInput
```

To also persist it for future terminals:

```powershell
[Environment]::SetEnvironmentVariable('LLM_DIPLOMACY_OPENAI_API_KEY', $env:LLM_DIPLOMACY_OPENAI_API_KEY, 'User')
```

This project-specific variable name leaves other applications' `OPENAI_API_KEY` unchanged. These profiles require the project variable; they do not fall back to the shared key. Custom provider configs can choose another environment variable with `api_key_env_var`. The runner reads process environment variables; it does not automatically load a `.env` file.

## Current limits

The offline DATC gate currently covers 172 entries: 155 match source expectations, nine are declared project-policy divergences, one is an adapter limitation, and seven are historical alternatives. Ordinary 6.G.11's inferred adjacent-convoy intent is an adapter convention, not an existing gameplay rule. These results are not an unqualified standard-rules conformance claim. See [DATC coverage](docs/DATC_COVERAGE.md) and [publication readiness](docs/PUBLICATION_READINESS.md).

The viewer's historical reports and fixture corpora are excluded from the source-only candidate. Portable tests exercise synthetic report validation and year navigation; historical fixture regressions remain separate. LLM behavior, gameplay strength, and research conclusions require separate evaluation.

## Design and operations

- [System design](docs/SYSTEM_DESIGN.md)
- [Diplomacy rule specification](docs/DIPLOMACY_RULE_SPEC.md)
- [Agent specification](docs/AGENT_SPEC.md)
- [Artifact compatibility](docs/ARTIFACT_COMPATIBILITY.md)
- [Core event contracts](docs/CORE_EVENT_CONTRACTS.md)
- [Report bundle exporter](docs/REPORT_BUNDLE_EXPORTER.md)
- [Report bundle projector](docs/REPORT_BUNDLE_PROJECTOR.md)
- [Current orchestration ownership](docs/ORCHESTRATION_OWNERSHIP.md)
- [Interrupted experiment checkpoints](docs/EXPERIMENT_CHECKPOINTS.md)
- [Provider timeout recovery](docs/PROVIDER_RECOVERY.md)
- [Publication readiness](docs/PUBLICATION_READINESS.md)
- [Offline viewer experiments](docs/VIEWER_EXPERIMENTS.md)
- [Validation record](docs/VALIDATION.md)
- [Offline viewer experiments](docs/VIEWER_EXPERIMENTS.md)
