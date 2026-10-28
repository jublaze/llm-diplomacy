from __future__ import annotations

import json
from pathlib import Path

from llm_diplomacy.observatory.api import GameObservatoryData
from llm_diplomacy.observatory.event_index import build_canonical_event_index
from llm_diplomacy.observatory.strategic_consistency import (
    strategic_consistency_experiment_markdown,
    strategic_consistency_markdown,
    summarize_experiment_strategic_consistency,
    summarize_strategic_consistency,
)


def _base_data(tmp_path: Path) -> GameObservatoryData:
    return GameObservatoryData(
        game_dir=tmp_path / "game_001",
        phases=[],
        events=[],
        global_messages=[],
        private_messages=[],
        phase_submitted_orders={},
        phase_resolutions={},
        phase_reflections={},
        snapshots=[],
        assignments_by_power={"england": "a1", "france": "a2"},
        runtime_outputs_by_agent={},
    )


def test_strategic_consistency_detects_plausible_betrayal_with_evidence(tmp_path) -> None:
    data = GameObservatoryData(
        game_dir=tmp_path / "game_001",
        phases=["1901_spring_negotiation", "1901_spring_movement"],
        events=[],
        global_messages=[],
        private_messages=[
            {
                "schema_version": "1.0",
                "message_id": "m1",
                "phase_id": "1901_spring_negotiation",
                "tick": 1,
                "from_agent_id": "a1",
                "to": {"type": "private", "to_agent_id": "a2"},
                "body": "I will not attack BEL; let's keep a DMZ on Belgium.",
            }
        ],
        phase_submitted_orders={
            "1901_spring_movement": [
                {
                    "schema_version": "1.0",
                    "order_id": "o1",
                    "power": "england",
                    "unit_ref": "u_eng_f_nth",
                    "type": "MOVE",
                    "payload": {"to": "bel"},
                }
            ]
        },
        phase_resolutions={
            "1901_spring_movement": {
                "schema_version": "1.0",
                "phase": "movement",
                "output_state": {
                    "schema_version": "1.0",
                    "units": [
                        {"unit_id": "u_eng_f_nth", "owner": "england", "province": "bel", "status": "active"}
                    ],
                    "supply_ownership": {"bel": "england"},
                },
                "movement_outcomes": [{"unit_id": "u_eng_f_nth", "from": "nth", "to": "bel", "result": "moved"}],
            }
        },
        phase_reflections={},
        snapshots=[
            {
                "schema_version": "1.0",
                "snapshot_id": "initial",
                "payload": {
                    "schema_version": "1.0",
                    "units": [
                        {"unit_id": "u_fra_a_bel", "owner": "france", "province": "bel", "status": "active"},
                        {"unit_id": "u_eng_f_nth", "owner": "england", "province": "nth", "status": "active"},
                    ],
                    "supply_ownership": {"bel": "france"},
                },
            }
        ],
        assignments_by_power={"england": "a1", "france": "a2"},
        runtime_outputs_by_agent={},
    )
    payload = summarize_strategic_consistency(data)
    assert payload["summary"]["negotiation_messages_observed"] == 1
    assert payload["summary"]["order_submissions_observed"] == 1
    assert "chains" not in payload


def test_strategic_consistency_detects_stagnation_without_false_betrayal(tmp_path) -> None:
    rows = []
    for idx in range(30):
        rows.append(
            {
                "schema_version": "1.0",
                "message_id": f"m{idx:03d}",
                "phase_id": "1901_spring_negotiation",
                "tick": idx + 1,
                "from_agent_id": "a1" if idx % 2 == 0 else "a2",
                "to": {"type": "private", "to_agent_id": "a2" if idx % 2 == 0 else "a1"},
                "body": "Let's continue building trust and stay coordinated.",
            }
        )
    data = GameObservatoryData(
        game_dir=tmp_path / "game_001",
        phases=["1901_spring_negotiation"],
        events=[],
        global_messages=[],
        private_messages=rows,
        phase_submitted_orders={},
        phase_resolutions={},
        phase_reflections={},
        snapshots=[],
        assignments_by_power={"england": "a1", "france": "a2"},
        runtime_outputs_by_agent={},
    )
    payload = summarize_strategic_consistency(data)
    assert payload["summary"]["negotiation_messages_observed"] == 30
    assert "chains" not in payload


def test_strategic_consistency_markdown_and_population_summary(tmp_path) -> None:
    game_root = tmp_path / "experiments" / "exp_sc" / "games" / "game_001" / "observatory"
    game_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "report_type": "belief_and_evidence_observability",
        "game_id": "game_001",
        "summary": {"belief_summaries_detected": 2, "belief_summaries_persisted": 1, "belief_summaries_reused": 1},
        "belief_summary_metrics": {"reflection_count": 2, "summaries_present": 2},
    }
    (game_root / "belief_evidence_observability.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    md = strategic_consistency_markdown(payload)
    assert "Belief and Evidence Observability" in md
    summary = summarize_experiment_strategic_consistency(tmp_path / "experiments" / "exp_sc")
    assert summary["games_analyzed"] == 1
    summary_md = strategic_consistency_experiment_markdown(summary)
    assert "no strategic-chain scores" in summary_md


def test_strategic_consistency_tracks_structured_belief_update_persistence_and_reuse(tmp_path) -> None:
    belief_update = {
        "subject": "victor",
        "prior_belief": "Galicia DMZ was only a claim.",
        "new_belief": "Galicia DMZ is currently reliable.",
        "evidence": "Victor's message claimed a DMZ and the resolution kept Warsaw out of Galicia.",
        "strategic_consequence": "Balkan expansion can use less northern defense.",
        "next_test_or_action": "Verify the next Warsaw order stays outside Galicia.",
    }
    game_dir = tmp_path / "game_001"
    data = GameObservatoryData(
        game_dir=game_dir,
        phases=["p1", "p2"],
        events=[],
        global_messages=[],
        private_messages=[
            {
                "phase_id": "p1",
                "from_agent_id": "a2",
                "to": {"type": "private", "to_agent_id": "a1"},
                "body": "I will keep Warsaw out of Galicia under our DMZ.",
            }
        ],
        phase_submitted_orders={"p1": [{"power": "france", "type": "HOLD", "payload": {"at": "gal"}}]},
        phase_resolutions={"p1": {"movement_outcomes": []}},
        phase_reflections={"p1:a1": {"result": {"reflection": {"belief_updates": [belief_update]}}}},
        snapshots=[],
        assignments_by_power={"england": "a1", "france": "a2"},
        runtime_outputs_by_agent={
            "a1": {
                "p2_negotiate_tick": {
                    "llm_meta": {
                        "prompt": "Recent belief: Galicia DMZ is currently reliable. Verify Warsaw stays outside Galicia.",
                        "parsed_payload": {},
                    }
                }
            }
        },
    )
    memory_path = game_dir / "agents" / "a1" / "folio" / "game_memory" / "game_001.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(json.dumps({"belief_state": [{**belief_update, "phase_id": "p1"}]}), encoding="utf-8")
    board_deltas = [
        {"phase_id": "p1", "center_changes": [{"to_owner": "england", "from_owner": "france", "province": "bel"}]}
    ]
    payload = summarize_strategic_consistency(data, board_deltas=board_deltas)
    assert payload["summary"]["belief_summaries_detected"] == 1
    assert "chains" not in payload



def test_strategic_consistency_distinguishes_no_belief_update(tmp_path) -> None:
    data = GameObservatoryData(
        game_dir=tmp_path / "game_001",
        phases=["p1"],
        events=[],
        global_messages=[],
        private_messages=[],
        phase_submitted_orders={},
        phase_resolutions={},
        phase_reflections={
            "p1:a1": {
                "result": {
                    "reflection": {
                        "next_objective": "Hold position.",
                        "belief_updates": [],
                    }
                }
            }
        },
        snapshots=[],
        assignments_by_power={"england": "a1"},
        runtime_outputs_by_agent={},
    )
    payload = summarize_strategic_consistency(data)
    assert payload["belief_summary_metrics"]["summaries_present"] == 0


def test_strategic_consistency_reports_belief_summary_lifecycle_and_metrics(tmp_path) -> None:
    summary = (
        "Russia said Galicia would remain neutral, but the order and resolution moved into Galicia. "
        "That contradiction makes future Russian assurances uncertain; verify the next board position before trusting them."
    )
    game_dir = tmp_path / "game_001"
    data = GameObservatoryData(
        game_dir=game_dir,
        phases=["p1", "p2"],
        events=[],
        global_messages=[],
        private_messages=[],
        phase_submitted_orders={},
        phase_resolutions={},
        phase_reflections={
            "p1:a1": {
                "result": {
                    "reflection": {
                        "belief_summary": summary,
                        "belief_summary_status": "revised",
                    }
                }
            }
        },
        snapshots=[],
        assignments_by_power={"england": "a1"},
        runtime_outputs_by_agent={
            "a1": {
                "p2_negotiate_tick": {"llm_meta": {"prompt": f"# Current Belief Summary\n{summary}"}}
            }
        },
    )
    memory_path = game_dir / "agents" / "a1" / "folio" / "game_memory" / "game_001.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        json.dumps(
            {
                "belief_summary": summary,
                "belief_summary_phase_id": "p1",
                "belief_summary_history": [{"phase_id": "p0", "summary": "Earlier uncertainty."}],
            }
        ),
        encoding="utf-8",
    )
    payload = summarize_strategic_consistency(data)
    metrics = payload["belief_summary_metrics"]
    assert metrics["presence_rate"] == 1.0
    assert metrics["summaries_persisted"] == 1
    assert metrics["summaries_reused"] == 1


def test_canonical_index_links_cross_phase_northern_split_and_recovers_phase_ids(tmp_path) -> None:
    game_dir = tmp_path / "game_001"
    objective = "Secure Norway without creating a northern second front."
    belief_summary = "England retaining Norway while Russia retains Sweden and St Petersburg confirms a conditional northern split; hold Norway while testing Russian restraint."
    data = GameObservatoryData(
        game_dir=game_dir,
        phases=["1901_spring_movement", "1901_fall_movement"],
        events=[],
        global_messages=[],
        private_messages=[
            {
                "message_id": "norway_dmz",
                "phase_id": "1901_fall_negotiation",
                "from_agent_id": "dorian",
                "to": {"type": "private", "to_agent_id": "victor"},
                "body": "I will hold Norway if Russia holds Sweden and keeps St Petersburg out of the northern pressure lanes.",
            }
        ],
        phase_submitted_orders={
            "1901_fall_movement": [
                {"order_id": "eng_nwy_hold", "power": "england", "type": "HOLD", "payload": {"at": "nwy"}},
                {"order_id": "rus_swe_hold", "power": "russia", "type": "HOLD", "payload": {"at": "swe"}},
            ]
        },
        phase_resolutions={
            "1901_fall_movement": {
                "input_state": {"supply_ownership": {"nwy": "england", "swe": "russia", "stp": "russia"}},
                "output_state": {"supply_ownership": {"nwy": "england", "swe": "russia", "stp": "russia"}},
                "movement_outcomes": [
                    {"unit_id": "eng_f_nwy", "from": "nwy", "to": "nwy", "result": "held"},
                    {"unit_id": "rus_f_swe", "from": "swe", "to": "swe", "result": "held"},
                ],
            }
        },
        phase_reflections={
            "1901_spring_movement:dorian": {"reflection": {"objective_update": {"current_objective": objective}}},
            "1901_fall_movement:dorian": {"result": {"reflection": {"belief_summary": belief_summary, "belief_summary_status": "revised"}}},
        },
        snapshots=[],
        assignments_by_power={"england": "dorian", "russia": "victor"},
        runtime_outputs_by_agent={
            "dorian": {
                "1902_spring_negotiation_negotiate_tick": {
                    "llm_meta": {"prompt": f"# Current Belief Summary\n{belief_summary}"}
                }
            }
        },
    )
    memory_path = game_dir / "agents" / "dorian" / "folio" / "game_memory" / "game_001.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        json.dumps({"belief_summary": belief_summary, "belief_summary_phase_id": "1901_fall_movement", "belief_summary_history": [{"phase_id": "1901_fall_movement", "summary": belief_summary}]}),
        encoding="utf-8",
    )

    index = build_canonical_event_index(data)
    assert all(event["phase_id"] != "artifact_linkage_insufficient" for event in index)
    assert any(event["event_type"] == "board_resolution" and event["phase_id"] == "1901_fall_movement" for event in index)
    payload = summarize_strategic_consistency(data)
    assert payload["summary"]["negotiation_messages_observed"] == 1
    assert payload["summary"]["order_submissions_observed"] == 2
    assert payload["summary"]["board_resolutions_observed"] == 1
    assert payload["summary"]["belief_summaries_detected"] == 1
    assert payload["summary"]["belief_summaries_persisted"] == 1
    assert payload["summary"]["belief_summaries_reused"] == 1
    assert "chains" not in payload


def test_canonical_index_keeps_missing_phase_artifacts_distinct(tmp_path) -> None:
    data = _base_data(tmp_path)
    data.private_messages.append({"from_agent_id": "a1", "body": "Unlinked message"})
    events = build_canonical_event_index(data)
    assert events[0]["phase_id"] == "artifact_linkage_insufficient"


def test_belief_evidence_observability_excludes_strategic_chain_scores(tmp_path) -> None:
    data = _base_data(tmp_path)
    payload = summarize_strategic_consistency(data)
    banned = {
        "chains_detected",
        "complete_chains",
        "partial_chains",
        "high_confidence_chains",
        "low_confidence_chains",
        "objectives_with_aligned_orders",
        "strategic_conversion_score",
    }
    assert payload["report_type"] == "belief_and_evidence_observability"
    assert not (banned & set(payload["summary"]))
    assert "chains" not in payload
    assert "raw reflection JSON" in payload["raw_evidence"]["artifact_categories"]
