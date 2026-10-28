# Per-player 2×2 offline smoke

Completed 2026-09-17 using `feature/per-player-providers`. The implementation is integrated into the current checkout.

Config: [per_player_2x2_offline_1901.yaml](../config/examples/per_player_2x2_offline_1901.yaml), also copied into the feature worktree with matching hashes. The config uses per-player provider support, which is present in this checkout.

One game, fixed assignments, one year ending in 1901, one negotiation tick per eligible phase:

| Player | Source soul | Fake model | Power |
| --- | --- | --- | --- |
| cassandra_model_a | Cassandra | fake-model-a | Austria |
| cassandra_model_b | Cassandra | fake-model-b | England |
| dorian_model_a | Dorian | fake-model-a | France |
| dorian_model_b | Dorian | fake-model-b | Germany |

Marcellus, Seraphine, and Helena fill Italy, Russia, and Turkey with scripted runtimes. Each of the four focal players controls one power. All folios are isolated under this experiment's `source_folios`, not the normal agents directory.

The game executed; this was not a dry run. HTTP and connection helpers were blocked during execution. Only fake providers were selected, so no live model requests were made.

## Results

- One requested game completed through 1901; no budget stop or provider errors.
- Four focal players each produced 3 negotiation, 3 order, 3 reflection, and 1 cleanup output. Every output identified its expected model; mixed-provider summaries retained both model identities.
- Identical soul hashes within each pair, different hashes between Cassandra and Dorian, matching runtime/source soul files, and separate per-player persistent-memory files.
- 52 fake-provider attempts, including repairs; 20 fallbacks. The built-in fake provider emits empty orders and legacy reflection stubs, so order/reflection fallbacks are expected. This is a routing/identity/state-isolation smoke, not a strategy, rich-memory, or real-endpoint validation.
- Project security scan passed. No runtime code was changed for this smoke.

Results are in `experiments/per_player_2x2_offline_1901/`: `population_summary.md`, `pilot_report.json`, `smoke_verification.json`, source folios, and complete game artifacts. Generated artifacts remain ignored by Git.

## Run again

Run from the repository root. Choose a new experiment ID and fresh output directory before rerunning to preserve these results:

```powershell
.\.venv\Scripts\python.exe scripts/run_population_experiment.py --config config/examples/per_player_2x2_offline_1901.yaml --output-root .\experiments\offline-2x2-repeat --agents-root .\experiments\offline-2x2-repeat\source_folios
```

The feature was developed on `feature/per-player-providers`; it is integrated into this checkout. Generated game artifacts remain local. This historical smoke does not describe current remote publication status.
