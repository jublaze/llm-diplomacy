from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_diplomacy.compliance.artifacts import ArtifactValidator
from llm_diplomacy.compliance.replay import compare_experiment_dirs
from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.guardrails import BudgetController
from llm_diplomacy.observatory.api import load_game, summarize_agent, summarize_board_evolution, summarize_negotiations
from llm_diplomacy.observatory.generator import build_game_observatory
from llm_diplomacy.runner import ScriptedExperimentRunner


def _scripted_config(tmp_path: Path, *, experiment_id: str) -> Path:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "games": 1,
        "agents": [
            {"agent_id": "agent_random", "scripted_kind": "random_legal"},
            {"agent_id": "agent_hold", "scripted_kind": "hold"},
            {"agent_id": "agent_greedy", "scripted_kind": "greedy_center"},
        ],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 13,
    }
    path = tmp_path / f"{experiment_id}.json"
    path.write_text(json.dumps(cfg, sort_keys=True, indent=2), encoding="utf-8")
    return path


def test_scripted_runner_end_to_end_artifacts_and_metrics(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted")
    cfg = load_experiment_config(cfg_path)
    summary = ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    assert summary.games_completed == 1
    exp_root = tmp_path / "experiments" / cfg.experiment_id
    report = ArtifactValidator(schema_version="1.0").validate_experiment_dir(exp_root)
    assert report.ok, [str(err) for err in report.errors]

    overall = json.loads((exp_root / "leaderboard" / "overall.json").read_text(encoding="utf-8"))
    cost = json.loads((exp_root / "metrics" / "cost_summary.json").read_text(encoding="utf-8"))
    assert overall["schema_version"] == "1.0"
    assert overall["games_count"] == 1
    events = [json.loads(line) for line in (exp_root / "games" / "game_001" / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    game_end = next(event["payload"]["result"] for event in events if event.get("phase_id") == "game_end")
    assert game_end["result"] == "completed"
    assert game_end["winners"] == []
    assert game_end["cutoff_leaders"]
    leader_centers = max(row["owned_centers"] for row in game_end["country_results"].values())
    assert all(row["result"] == "leader_at_cutoff" for row in game_end["country_results"].values() if row["owned_centers"] == leader_centers)
    assert overall["wins_by_agent"] == {}
    assert cost["schema_version"] == "1.0"
    assert cost["records_count"] > 0
    assert set(cost["by_agent"].keys()) == {"agent_greedy", "agent_hold", "agent_random"}
    observatory = exp_root / "games" / "game_001" / "observatory"
    assert observatory.exists()
    assert (observatory / "england.md").exists()
    assert (observatory / "board_evolution.md").exists()
    assert (observatory / "board_evolution.json").exists()
    assert (observatory / "conversations" / "global.md").exists()
    assert (observatory / "index.html").exists()
    assert (observatory / "reflection_diagnostics.json").exists()
    assert (observatory / "prompt_diagnostics.json").exists()
    assert (observatory / "belief_evidence_observability.json").exists()
    assert (observatory / "belief_evidence_observability.md").exists()
    assert (exp_root / "games" / "game_001" / "phases").exists()
    assert list((exp_root / "games" / "game_001" / "phases").glob("*/orders/submitted_orders.json"))
    assert list((exp_root / "games" / "game_001" / "phases").glob("*/resolution/resolution.json"))
    reports = observatory / "reports"
    assert (reports / "aar_public.md").exists()
    assert (reports / "secret_history.md").exists()
    assert (reports / "agent_profiles.md").exists()
    assert (reports / "key_moments.md").exists()
    assert (reports / "reporter_summary.json").exists()
    report_payload = json.loads((reports / "reporter_summary.json").read_text(encoding="utf-8"))
    for field in (
        "schema_version",
        "game_id",
        "result",
        "winner_or_leader",
        "agents",
        "communication_summary",
        "alliance_candidates",
        "betrayal_candidates",
        "key_moments",
        "confidence_notes",
    ):
        assert field in report_payload
    index_html = (observatory / "index.html").read_text(encoding="utf-8")
    assert "reports/aar_public.md" in index_html
    assert "reports/secret_history.md" in index_html
    assert "reports/reporter_summary.json" in index_html
    assert "reflection_diagnostics.md" in index_html
    assert "prompt_diagnostics.md" in index_html
    assert "belief_evidence_observability.md" in index_html


def test_board_evolution_uses_initial_baseline_and_real_changes_only(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_board_baseline")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()
    board_path = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001" / "observatory" / "board_evolution.json"
    payload = json.loads(board_path.read_text(encoding="utf-8"))
    deltas = payload["deltas"]
    assert isinstance(deltas, list) and deltas
    first = deltas[0]
    assert first["phase_id"] == "1901_spring_movement"
    for key in ("moves", "center_changes", "builds", "disbands"):
        assert key in first
    changes = first["center_changes"]
    for power, row in sorted((first.get("powers") or {}).items()):
        assert row.get("units_gained", []) == []
        assert row.get("centers_gained", []) == sorted(
            change["province"] for change in changes if change["to_owner"] == power
        )
        assert row.get("centers_lost", []) == sorted(
            change["province"] for change in changes if change["from_owner"] == power
        )
    spring_moves = [row for row in deltas if str(row.get("phase_id", "")).endswith("spring_movement")]
    if spring_moves:
        assert spring_moves[0].get("center_changes", []) == []


def test_scripted_runner_cleanup_is_idempotent(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_cleanup")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    experiment_agent_root = tmp_path / "experiments" / cfg.experiment_id / "agents"
    for agent_id in ("agent_random", "agent_hold", "agent_greedy"):
        manifest = json.loads((experiment_agent_root / agent_id / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["games_played"] == 1
        applied = json.loads((experiment_agent_root / agent_id / "applied_games.json").read_text(encoding="utf-8"))
        assert applied["game_ids"] == ["game_001"]


def test_solo_requires_completed_fall_retreat_and_is_power_scoped() -> None:
    runner = object.__new__(ScriptedExperimentRunner)
    runner.config = SimpleNamespace(schema_version="1.0")
    runner.budget = None
    ownership = {f"sc_{index}": "france" for index in range(18)}
    state = {
        "year": 1901, "season": "fall", "phase": "movement",
        "supply_ownership": ownership,
        "units": [{"owner": "germany", "status": "dislodged", "must_retreat": True}],
    }
    assignments = {"france": "agent_france", "germany": "agent_germany", "england": "agent_germany"}

    assert runner._fall_solo_victor(state) is None
    spring = dict(state, season="spring", phase="movement", units=[])
    assert runner._fall_solo_victor(spring) is None
    pending = runner._game_result_payload(state, assignments)
    assert pending["result"] == "completed"
    assert pending["winners"] == []
    assert pending["country_results"]["france"]["result"] == "leader_at_cutoff"

    state["phase"] = "retreat"
    state["units"] = [{"owner": "germany", "status": "active", "must_retreat": False}]
    assert runner._fall_solo_victor(state) == "france"
    solo = runner._game_result_payload(state, assignments)
    assert solo["result"] == "solo_victory"
    assert solo["winners"] == ["agent_france"]
    assert solo["solo_victor"] == {"power": "france", "agent_id": "agent_france", "owned_centers": 18}
    assert solo["final_phase_id"] == "1901_fall_retreat"

    # Multiple countries controlled by one agent cannot combine centers into a solo.
    shared = dict(state, supply_ownership={**ownership, **{f"other_{i}": "germany" for i in range(10)}})
    assert runner._fall_solo_victor(shared) == "france"
    shared["supply_ownership"] = {**{f"sc_{i}": "france" for i in range(9)}, **{f"sc_{i+9}": "germany" for i in range(9)}}
    aggregate = runner._game_result_payload(shared, assignments)
    assert aggregate["result"] == "completed"
    assert aggregate["winners"] == []
    shared["supply_ownership"] = {**{f"fr_{i}": "france" for i in range(18)}, **{f"ge_{i}": "germany" for i in range(18)}}
    assert runner._fall_solo_victor(shared) is None


@pytest.mark.parametrize("pending_retreat", [False, True])
def test_runner_stops_on_final_fall_solo_and_snapshots_final_ownership(tmp_path, pending_retreat) -> None:
    cfg = load_experiment_config(_scripted_config(tmp_path, experiment_id="exp_final_fall_solo"))
    runner = ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=2)
    if pending_retreat:
        resolve_phase = runner.rule_engine.resolve_phase

        def resolve_with_retreat(state, orders):
            resolution = resolve_phase(state, orders)
            if state.get("season") == "fall" and state.get("phase") == "movement":
                unit = next(unit for unit in resolution["output_state"]["units"] if unit.get("status") == "active")
                unit["status"] = "dislodged"
                unit["must_retreat"] = True
                unit["dislodged_from"] = unit["province"]
            return resolution

        runner.rule_engine.resolve_phase = resolve_with_retreat
    apply_fall_ownership = runner._apply_fall_ownership
    centers = [province.id for province in runner.rule_engine.map_def.provinces if province.supply_center]

    def ensure_solo(state):
        updated = apply_fall_ownership(state)
        updated["supply_ownership"] = dict(updated["supply_ownership"])
        for province in centers[:18]:
            updated["supply_ownership"][province] = "france"
        return updated

    runner._apply_fall_ownership = ensure_solo
    summary = runner.run()
    assert summary.games_completed == 1
    game_root = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"
    events = [json.loads(line) for line in (game_root / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    terminal = next(event["payload"]["result"] for event in events if event.get("phase_id") == "game_end")
    assert terminal["result"] == "solo_victory"
    assert terminal["solo_victor"]["power"] == "france"
    assert terminal["solo_victor"]["owned_centers"] >= 18
    assert terminal["final_phase_id"] == f"1901_fall_{'retreat' if pending_retreat else 'movement'}"
    final_snapshot = game_root / "state" / "snapshots" / f"{terminal['final_phase_id']}_state.json"
    last = json.loads(final_snapshot.read_text(encoding="utf-8"))["payload"]
    assert f"{last['year']}_{last['season']}_{last['phase']}" == terminal["final_phase_id"]
    assert sum(owner == "france" for owner in last["supply_ownership"].values()) == terminal["solo_victor"]["owned_centers"]
    assert any(owner is None for owner in last["supply_ownership"].values())
    assert not list((game_root / "state" / "snapshots").glob("1901_winter_*_state.json"))
    assert sum(event.get("event_type") == "game_end_cleanup_applied" for event in events) == 1
    assert not any(
        event.get("event_type") == "agent_invocation_started"
        and event.get("phase_id") == terminal["final_phase_id"]
        and event.get("payload", {}).get("method") == "reflect"
        for event in events
    )


def test_solo_wins_simultaneous_phase_budget_stop_but_blocks_next_game(tmp_path, monkeypatch) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_solo_budget_boundary")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["games"] = 2
    raw["agents"] = [{"agent_id": f"agent_{index}", "scripted_kind": "hold"} for index in range(3)]
    raw["budget"] = {"stop_policy": "stop_game_after_phase"}
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")
    cfg = load_experiment_config(cfg_path)

    def stop_at_fall(cls, *, schema_version, experiment_id, artifact_root, raw):
        controller = BudgetController(
            schema_version=schema_version, experiment_id=experiment_id, artifact_root=artifact_root, config=None,
        )
        checks = 0

        def consume(*, game_id):
            nonlocal checks
            checks += 1
            if checks == 2:
                controller.stop_reason = "test_phase_budget"
                controller.stop_game_id = game_id
                return True
            return False

        controller.consume_stop_game_after_phase = consume
        return controller

    monkeypatch.setattr(BudgetController, "from_raw", classmethod(stop_at_fall))
    runner = ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=2)
    apply_fall_ownership = runner._apply_fall_ownership
    centers = [province.id for province in runner.rule_engine.map_def.provinces if province.supply_center]

    def ensure_solo(state):
        updated = apply_fall_ownership(state)
        updated["supply_ownership"] = dict(updated["supply_ownership"])
        for province in centers[:18]:
            updated["supply_ownership"][province] = "france"
        return updated

    runner._apply_fall_ownership = ensure_solo
    summary = runner.run()
    assert summary.games_requested == 2
    assert summary.games_started == 1
    assert summary.games_completed == 1
    game_root = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"
    events = [json.loads(line) for line in (game_root / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    terminal = next(event["payload"]["result"] for event in events if event.get("phase_id") == "game_end")
    assert terminal["result"] == "solo_victory"
    assert terminal.get("partial") is None
    assert not (tmp_path / "experiments" / cfg.experiment_id / "games" / "game_002").exists()


def test_scripted_runner_replay_comparison_is_deterministic(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_replay")
    cfg = load_experiment_config(cfg_path)
    root_a = tmp_path / "run_a"
    root_b = tmp_path / "run_b"
    ScriptedExperimentRunner(config=cfg, base_dir=root_a, max_years=1).run()
    ScriptedExperimentRunner(config=cfg, base_dir=root_b, max_years=1).run()

    result = compare_experiment_dirs(
        root_a / "experiments" / cfg.experiment_id,
        root_b / "experiments" / cfg.experiment_id,
    )
    assert result.deterministic, result.differences


def test_observatory_api_can_summarize_game(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_observatory_api")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()
    game_dir = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"
    data = load_game(game_dir)
    england = summarize_agent(data, "england")
    conversations = summarize_negotiations(data)
    board = summarize_board_evolution(data)
    assert "Invocation Records" in england
    assert "global" in conversations
    assert isinstance(board, list) and len(board) > 0


def test_omniscient_reporter_is_read_only_and_separates_private_content(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_reporter_read_only")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()
    game_dir = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"
    folio_files = sorted((game_dir / "agents").rglob("*"))
    folio_hashes: dict[str, str] = {}
    for path in folio_files:
        if path.is_file() and "folio" in path.parts:
            folio_hashes[str(path)] = sha256(path.read_bytes()).hexdigest()

    private_inbox = game_dir / "messages" / "private" / "agent_hold" / "inbox.jsonl"
    private_inbox.parent.mkdir(parents=True, exist_ok=True)
    private_inbox.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "message_id": "manual_private_001",
                "phase_id": "1901_spring_negotiation",
                "tick": 1,
                "from_agent_id": "agent_random",
                "to": {"type": "private", "to_agent_id": "agent_hold"},
                "body": "PRIVATE_ONLY_TEST_CONTENT",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    build_game_observatory(game_dir)

    public_aar = (game_dir / "observatory" / "reports" / "aar_public.md").read_text(encoding="utf-8")
    secret_history = (game_dir / "observatory" / "reports" / "secret_history.md").read_text(encoding="utf-8")
    assert "PRIVATE_ONLY_TEST_CONTENT" not in public_aar
    assert "PRIVATE_ONLY_TEST_CONTENT" in secret_history

    for path_str, digest in folio_hashes.items():
        path = Path(path_str)
        if path.exists():
            assert sha256(path.read_bytes()).hexdigest() == digest


def test_omniscient_reporter_degrades_gracefully_with_missing_optional_artifacts(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_reporter_missing_optional")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()
    game_dir = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"

    first_phase = sorted([p for p in (game_dir / "phases").iterdir() if p.is_dir()], key=lambda p: p.name)[0]
    reflection_dir = first_phase / "reflection"
    if reflection_dir.exists():
        for row in reflection_dir.glob("*.json"):
            row.unlink()
    build_game_observatory(game_dir)
    assert (game_dir / "observatory" / "reports" / "aar_public.md").exists()
    assert (game_dir / "observatory" / "reports" / "reporter_summary.json").exists()


def test_capped_game_reporter_uses_leader_at_cap_and_avoids_keyword_only_betrayal(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_reporter_cap")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()
    game_dir = tmp_path / "experiments" / cfg.experiment_id / "games" / "game_001"
    phase_dirs = sorted([p for p in (game_dir / "phases").iterdir() if p.is_dir()], key=lambda p: p.name)
    if phase_dirs:
        reflection_dir = phase_dirs[0] / "reflection"
        reflection_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(reflection_dir.glob("*.json"))
        if files:
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            payload["result"] = payload.get("result", {})
            payload["result"]["reflection"] = payload["result"].get("reflection", {})
            payload["result"]["reflection"]["memory_update"] = "Potential betrayal was considered."
            files[0].write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    build_game_observatory(game_dir)
    summary = json.loads((game_dir / "observatory" / "reports" / "reporter_summary.json").read_text(encoding="utf-8"))
    assert summary["winner_or_leader"]["kind"] == "leader_at_cap"
    assert summary["betrayal_candidates"] == []
    aar = (game_dir / "observatory" / "reports" / "aar_public.md").read_text(encoding="utf-8").lower()
    assert "no solo winner recorded" in aar


def test_scripted_game_cli_script(tmp_path) -> None:
    cfg_path = _scripted_config(tmp_path, experiment_id="exp_scripted_cli")
    output_root = tmp_path / "out"
    python_exe = Path(".venv") / "Scripts" / "python.exe"
    proc = subprocess.run(
        [
            str(python_exe),
            "scripts/run_scripted_game.py",
            "--config",
            str(cfg_path),
            "--output-root",
            str(output_root),
            "--max-years",
            "1",
        ],
        cwd=str(Path.cwd()),
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip())
    assert payload["summary"]["validation_ok"] is True
