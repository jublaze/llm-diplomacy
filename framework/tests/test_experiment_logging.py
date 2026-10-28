from __future__ import annotations

import json
from pathlib import Path

import yaml

from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner
import llm_diplomacy.runner.scripted_experiment_runner as scripted_runner_module
from llm_diplomacy.agents.llm_provider import FakeLLMProvider, LLMRequest, LLMResponse


class _MalformedLLMProvider:
    def generate(self, request: LLMRequest) -> LLMResponse:
        by_method = {
            "negotiate_tick": json.dumps(
                {
                    "done": False,
                    "communication_plan": {
                        "goal": "test",
                        "target_players": ["agent_llm"],
                        "channel_choices": ["private"],
                    },
                    "relationship_analysis": [
                        {
                            "agent_id": "agent_scripted_1",
                            "current_relationship": "uncertain",
                            "what_they_may_want": "quiet border",
                            "what_i_want_from_them": "non-aggression",
                            "how_this_serves_my_objectives": "stabilizes one front for better force allocation",
                            "risk_to_my_objectives": "vague commitments without tactical follow-through",
                            "specific_evidence": "recent private ping with border caution",
                            "next_strategic_use_of_relationship": "request province-specific non-aggression terms",
                            "next_conversation_move": "ask concrete border question",
                        }
                    ],
                    "messages": [{"to": "agent_llm", "body": "self ping"}],
                    "explanation": {"summary": "emit self message"},
                },
                sort_keys=True,
            ),
            "submit_orders": "not_json_orders",
            "reflect": "not_json_reflect",
            "game_end_cleanup": json.dumps(
                {
                    "persistent_memory_update": "",
                    "public_profile_update": None,
                    "explanation": {"summary": "ok"},
                },
                sort_keys=True,
            ),
        }
        return LLMResponse(
            schema_version=request.schema_version,
            content=by_method.get(request.method, "{}"),
            model="malformed-test-provider",
            raw={"provider": "malformed-test-provider", "usage": {}},
        )


def _event_rows(path: Path) -> list[dict[str, object]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _build_scripted_cfg(
    path: Path,
    *,
    experiment_id: str,
    budget: dict[str, object] | None = None,
    include_llm: bool = True,
) -> None:
    agents: list[dict[str, object]] = []
    if include_llm:
        agents.append({"agent_id": "agent_llm", "runtime_kind": "llm"})
    start_idx = 1 if include_llm else 0
    for idx in range(start_idx, 7):
        agents.append({"agent_id": f"agent_scripted_{idx}", "runtime_kind": "scripted", "scripted_kind": "hold"})
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "games": 1,
        "agents": agents,
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 7,
        "llm_provider": "fake",
    }
    if budget is not None:
        payload["budget"] = budget
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _first_index(rows: list[dict[str, object]], event_type: str) -> int:
    for idx, row in enumerate(rows):
        if row.get("event_type") == event_type:
            return idx
    return -1


def _assert_event_artifact_paths_exist(*, rows: list[dict[str, object]], experiment_root: Path) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if not isinstance(key, str) or not key.endswith("_artifact_path"):
                continue
            if not isinstance(value, str) or not value:
                continue
            artifact = experiment_root / value
            assert artifact.exists(), f"missing artifact referenced by {key}: {value}"


def test_experiment_logs_capture_lifecycle_prompt_checks_and_fallbacks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        scripted_runner_module,
        "build_provider_from_config",
        lambda _cfg: _MalformedLLMProvider(),
    )
    cfg_path = tmp_path / "cfg.json"
    _build_scripted_cfg(cfg_path, experiment_id="exp_logs_core")
    cfg = load_experiment_config(cfg_path)
    cfg.raw["reflection_protocol"] = "legacy_json_v1"
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    exp_root = tmp_path / "experiments" / "exp_logs_core"
    text_log = exp_root / "logs" / "experiment.log"
    events_log = exp_root / "logs" / "experiment_events.jsonl"
    assert text_log.exists()
    assert events_log.exists()
    assert "EXPERIMENT_STARTED" in text_log.read_text(encoding="utf-8")

    rows = _event_rows(events_log)
    assert rows
    for row in rows:
        assert isinstance(row, dict)
        assert row["schema_version"] == "1.0"
        assert "timestamp" in row
        assert "event_type" in row
        assert row["experiment_id"] == "exp_logs_core"

    assert _first_index(rows, "experiment_started") >= 0
    assert _first_index(rows, "game_started") >= 0
    assert _first_index(rows, "phase_started") >= 0
    assert _first_index(rows, "agent_invocation_started") >= 0
    assert _first_index(rows, "agent_invocation_completed") >= 0
    assert _first_index(rows, "game_completed") >= 0
    assert _first_index(rows, "experiment_completed") >= 0
    assert _first_index(rows, "experiment_started") < _first_index(rows, "game_started")
    assert _first_index(rows, "game_started") < _first_index(rows, "phase_started")
    assert _first_index(rows, "phase_started") < _first_index(rows, "agent_invocation_started")
    assert _first_index(rows, "agent_invocation_started") < _first_index(rows, "agent_invocation_completed")

    prompt_checks = [row for row in rows if row.get("event_type") == "prompt_check"]
    assert prompt_checks
    checks = prompt_checks[0].get("checks", {})
    assert isinstance(checks, dict)
    assert checks.get("contains_strategic_imperatives") is True
    assert checks.get("contains_victory_campaign_doctrine") is True
    assert checks.get("contains_diplomacy_primer") is True
    assert checks.get("contains_rules_reference") is True
    assert checks.get("contains_rules_reference_version") is True
    assert any(
        row.get("checks", {}).get("contains_outcome_review_instruction") is True
        for row in prompt_checks
        if isinstance(row.get("checks", {}), dict)
    )
    assert checks.get("contains_engine_authority") is True
    assert checks.get("contains_support_mechanics") is True
    assert checks.get("contains_convoy_mechanics") is True
    prompt_assets = prompt_checks[0].get("prompt_assets", {})
    assert isinstance(prompt_assets, dict)
    strategic = prompt_assets.get("strategic_imperatives", {})
    doctrine = prompt_assets.get("victory_campaign_doctrine", {})
    primer = prompt_assets.get("diplomacy_primer", {})
    assert isinstance(strategic, dict) and isinstance(doctrine, dict) and isinstance(primer, dict)
    assert strategic.get("exists") is True
    assert doctrine.get("exists") is True
    assert primer.get("exists") is True
    assert isinstance(strategic.get("char_length"), int) and strategic["char_length"] > 0
    assert isinstance(doctrine.get("char_length"), int) and doctrine["char_length"] > 0
    assert isinstance(primer.get("char_length"), int) and primer["char_length"] > 0
    assert isinstance(strategic.get("sha256"), str) and strategic["sha256"].startswith("sha256:")
    assert isinstance(doctrine.get("sha256"), str) and doctrine["sha256"].startswith("sha256:")
    assert strategic.get("strategic_imperatives_version") == "strategic_imperatives_v2_1_campaign_oriented"
    assert doctrine.get("victory_campaign_doctrine_version") == "victory_campaign_doctrine_v1_5_initiative_test"
    assert isinstance(primer.get("sha256"), str) and primer["sha256"].startswith("sha256:")
    assert primer.get("rules_reference_version") == "diplomacy_rules_reference_v5_convoyed_army_support_public_orders"
    outcome_review = prompt_assets.get("outcome_review", {})
    assert isinstance(outcome_review, dict)
    assert outcome_review.get("outcome_review_version") == "outcome_review_v1_public_adjudication"
    assert isinstance(outcome_review.get("sha256"), str) and outcome_review["sha256"].startswith("sha256:")
    assert any(
        isinstance(row.get("prompt_assets", {}), dict)
        and row["prompt_assets"].get("outcome_review", {}).get("included_in_prompt") is True
        for row in prompt_checks
    )
    assert strategic.get("included_in_prompt") is True
    assert doctrine.get("included_in_prompt") is True
    assert primer.get("included_in_prompt") is True

    loaded = [row for row in rows if row.get("event_type") == "prompt_assets_loaded"]
    assert loaded
    loaded_assets = loaded[0].get("prompt_assets", {})
    assert isinstance(loaded_assets, dict)
    assert loaded[0].get("prompt_profile") == "default"

    dropped = [
        row for row in rows if row.get("event_type") == "message_dropped" and row.get("reason") == "self_message"
    ]
    assert dropped
    assert any(row.get("event_type") == "orders_repaired" for row in rows)
    assert any(row.get("event_type") == "orders_fallback_applied" for row in rows)
    assert any(row.get("event_type") == "reflection_fallback_applied" for row in rows)
    assert any(row.get("event_type") == "board_resolution_summary" for row in rows)
    assert any(row.get("event_type") == "relationship_snapshot" for row in rows)
    assert any(row.get("event_type") == "game_summary" for row in rows)
    game_summary = next(row for row in rows if row.get("event_type") == "game_summary")
    assert game_summary["negotiation_calls"] > 0
    assert "negotiation_parse_failures" in game_summary
    assert "negotiation_schema_failures" in game_summary
    assert "negotiation_truncated_outputs" in game_summary
    assert "negotiation_fallback_rate" in game_summary
    assert "negotiation_outputs_trimmed" in game_summary

    generated_messages = [
        row
        for row in rows
        if row.get("event_type") == "messages_generated" and row.get("agent_id") == "agent_llm"
    ]
    assert generated_messages
    msg_summaries = generated_messages[0].get("message_summaries", [])
    assert isinstance(msg_summaries, list)
    assert generated_messages[0].get("dropped_count") == 1

    generated_orders = [row for row in rows if row.get("event_type") == "orders_generated"]
    assert generated_orders
    order_summaries = generated_orders[0].get("order_summaries", [])
    assert isinstance(order_summaries, list)

    reflection_rows = [row for row in rows if row.get("event_type") == "reflection_generated"]
    assert reflection_rows
    reflection_summary = reflection_rows[0].get("reflection_summary", {})
    assert isinstance(reflection_summary, dict)
    assert "memory_update" in reflection_summary

    _assert_event_artifact_paths_exist(rows=rows, experiment_root=exp_root)


def test_free_vocabulary_negotiation_labels_do_not_trigger_failure_metrics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        scripted_runner_module,
        "build_provider_from_config",
        lambda _cfg: FakeLLMProvider(
            responses_by_method={
                "negotiate_tick": json.dumps(
                    {
                        "private_messages": [
                            {
                                "recipient": "agent_scripted_1",
                                "body": "Will you avoid Burgundy while I hold Paris?",
                                "purpose": "conditional_defensive_bargain",
                            }
                        ],
                        "global_messages": [],
                        "relationship_updates": [
                            {
                                "agent_id": "agent_scripted_1",
                                "status": "tentative_ally",
                                "next_use": "Confirm the Burgundy boundary before orders.",
                            }
                        ],
                        "withheld_information": [],
                    }
                )
            }
        ),
    )
    cfg_path = tmp_path / "cfg_free_labels.json"
    _build_scripted_cfg(cfg_path, experiment_id="exp_free_labels")
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    rows = _event_rows(tmp_path / "experiments" / "exp_free_labels" / "logs" / "experiment_events.jsonl")
    game_summary = next(row for row in rows if row.get("event_type") == "game_summary")
    assert game_summary["negotiation_calls"] > 0
    assert game_summary["negotiation_parse_failures"] == 0
    assert game_summary["negotiation_schema_failures"] == 0
    assert game_summary["negotiation_fallbacks"] == 0


def test_budget_stop_event_is_logged(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_budget.json"
    _build_scripted_cfg(
        cfg_path,
        experiment_id="exp_logs_budget",
        budget={"max_provider_calls": 0, "stop_policy": "stop_experiment"},
    )
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    rows = _event_rows(tmp_path / "experiments" / "exp_logs_budget" / "logs" / "experiment_events.jsonl")
    assert any(row.get("event_type") == "budget_stop_triggered" for row in rows)
    assert any(row.get("event_type") == "experiment_stopped" for row in rows)


def test_budget_milestones_are_logged(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_budget_milestone.json"
    _build_scripted_cfg(
        cfg_path,
        experiment_id="exp_logs_budget_milestone",
        budget={"max_provider_calls": 5, "stop_policy": "stop_experiment"},
    )
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    rows = _event_rows(tmp_path / "experiments" / "exp_logs_budget_milestone" / "logs" / "experiment_events.jsonl")
    milestones = [row for row in rows if row.get("event_type") == "budget_milestone"]
    assert milestones
    assert any(row.get("metric") == "provider_calls" for row in milestones)


def test_log_files_are_rotated_between_runs_for_same_experiment_id(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_repeat.json"
    _build_scripted_cfg(cfg_path, experiment_id="exp_logs_repeat")
    cfg = load_experiment_config(cfg_path)
    runner = ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1)
    runner.run()
    runner.run()

    logs_dir = tmp_path / "experiments" / "exp_logs_repeat" / "logs"
    rows = _event_rows(logs_dir / "experiment_events.jsonl")
    assert sum(1 for row in rows if row.get("event_type") == "experiment_started") == 1
    archived = logs_dir / "archived"
    assert archived.exists()
    assert any(path.name.endswith("_experiment.log") for path in archived.iterdir())
    assert any(path.name.endswith("_experiment_events.jsonl") for path in archived.iterdir())


def test_scripted_agents_skip_prompt_check_events_and_are_marked_scripted(tmp_path) -> None:
    cfg_path = tmp_path / "cfg_scripted_only.json"
    _build_scripted_cfg(cfg_path, experiment_id="exp_logs_scripted_only", include_llm=False)
    cfg = load_experiment_config(cfg_path)
    ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1).run()

    rows = _event_rows(tmp_path / "experiments" / "exp_logs_scripted_only" / "logs" / "experiment_events.jsonl")
    assert not any(row.get("event_type") == "prompt_check" for row in rows)
    completed = [row for row in rows if row.get("event_type") == "agent_invocation_completed"]
    assert completed
    assert all(row.get("runtime_kind") == "scripted" for row in completed)
    assert all("prompt_assets" not in row for row in completed)
    assert all("prompt_checks" not in row for row in completed)


def test_population_summary_and_pilot_include_runtime_fields(tmp_path) -> None:
    cfg_path = tmp_path / "population_logging.yaml"
    payload = {
        "schema_version": "1.0",
        "experiment_id": "pop_logs_timing",
        "agent_state_mode": "fresh",
        "agents": [
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
        ],
        "runtime": {"kind": "baseline_llm", "provider": "fake", "max_ticks": 1},
        "games": {"count": 1, "max_year": 1901, "country_assignment": "fixed", "seed": 12},
        "outputs": {"observatory": True, "summary": True},
    }
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    cfg = load_population_experiment_config(cfg_path)
    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    pilot = json.loads(result.pilot_report_json_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    for row in (summary, pilot):
        assert isinstance(row.get("started_at"), str) and row["started_at"]
        assert isinstance(row.get("completed_at"), str) and row["completed_at"]
        assert isinstance(row.get("elapsed_seconds"), (int, float))

    summary_md = result.summary_md_path.read_text(encoding="utf-8")
    pilot_md = result.pilot_report_md_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "- started_at:" in summary_md
    assert "- completed_at:" in summary_md
    assert "- elapsed_seconds:" in summary_md
    assert "- started_at:" in pilot_md
    assert "- completed_at:" in pilot_md
    assert "- elapsed_seconds:" in pilot_md


def test_budget_stopped_game_status_is_consistent_across_logs_and_summaries(tmp_path) -> None:
    cfg_path = tmp_path / "population_budget_stop.yaml"
    payload = {
        "schema_version": "1.0",
        "experiment_id": "pop_budget_stop_consistency",
        "agent_state_mode": "fresh",
        "agents": [
            str((Path("blueprints") / "cassandra.yaml").resolve()),
            str((Path("blueprints") / "dorian.yaml").resolve()),
        ],
        "runtime": {"kind": "baseline_llm", "provider": "fake", "max_ticks": 1},
        "games": {"count": 1, "max_year": 1902, "country_assignment": "fixed", "seed": 12},
        "budget": {"max_provider_calls": 0, "stop_policy": "stop_experiment"},
        "outputs": {"observatory": True, "summary": True},
    }
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    cfg = load_population_experiment_config(cfg_path)
    result = PopulationExperimentRunner(config=cfg, base_dir=tmp_path, agents_root=tmp_path / "agents").run()

    rows = _event_rows(
        tmp_path
        / "experiments"
        / "pop_budget_stop_consistency"
        / "logs"
        / "experiment_events.jsonl"
    )
    assert any(row.get("event_type") == "game_stopped" for row in rows)
    assert not any(row.get("event_type") == "game_completed" for row in rows)
    summaries = [row for row in rows if row.get("event_type") == "game_summary"]
    assert summaries
    assert summaries[-1].get("result") == "stopped"

    summary_json = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    games = summary_json.get("games", [])
    assert isinstance(games, list) and games
    outcome = games[0].get("outcome", {})
    assert isinstance(outcome, dict)
    assert outcome.get("result") == "stopped"
    assert outcome.get("partial") is True

    summary_md = result.summary_md_path.read_text(encoding="utf-8")
    pilot_md = result.pilot_report_md_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "partial, stop_reason=" in summary_md
    assert "partial, stop_reason=" in pilot_md
