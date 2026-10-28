from __future__ import annotations

import json

from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.scripted_agents import (
    GreedyCenterAgent,
    HoldAgent,
    RandomLegalAgent,
    ScriptedAgentContext,
)
from llm_diplomacy.agents.scripted_runtime import ScriptedAgentRuntime
from llm_diplomacy.map.loader import load_standard_map


def _state(phase: str, units: list[dict[str, object]], supply: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "map_id": "standard",
        "year": 1901,
        "season": "winter" if phase == "adjustment" else "spring",
        "phase": phase,
        "units": units,
        "supply_ownership": supply or {},
        "home_centers": {},
    }


def _unit(unit_id: str, owner: str, unit_type: str, province: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "owner": owner,
        "type": unit_type,
        "province": province,
        "coast": None,
        "status": "active",
        "must_retreat": False,
        "dislodged_from": None,
    }


def test_hold_agent_disbands_deterministically_when_required() -> None:
    ctx = ScriptedAgentContext(
        schema_version="1.0",
        agent_id="a_hold",
        country="france",
        phase_id="1901_winter_adjustment",
        state=_state(
            "adjustment",
            [
                _unit("u1", "france", "A", "par"),
                _unit("u2", "france", "A", "mar"),
            ],
            supply={"par": "france"},
        ),
    )
    orders = HoldAgent().submit_orders(ctx)
    assert len(orders) == 1
    assert orders[0]["type"] == "DISBAND"
    assert orders[0]["payload"]["unit_ref"] == "u1"


def test_random_legal_agent_emits_only_known_order_types() -> None:
    agent = RandomLegalAgent(map_def=load_standard_map(), seed=7)
    ctx = ScriptedAgentContext(
        schema_version="1.0",
        agent_id="a_rand",
        country="france",
        phase_id="1901_spring_movement",
        state=_state("movement", [_unit("u1", "france", "A", "par"), _unit("u2", "france", "F", "bre")]),
    )
    orders = agent.submit_orders(ctx)
    assert len(orders) == 2
    assert all(order["type"] in {"HOLD", "MOVE"} for order in orders)


def test_greedy_center_agent_prefers_supply_center_destination_when_available() -> None:
    agent = GreedyCenterAgent(map_def=load_standard_map(), seed=3)
    ctx = ScriptedAgentContext(
        schema_version="1.0",
        agent_id="a_greedy",
        country="france",
        phase_id="1901_spring_movement",
        state=_state(
            "movement",
            [
                _unit("u_fra_a_pic", "france", "A", "pic"),
            ],
            supply={"bel": "germany"},
        ),
    )
    orders = agent.submit_orders(ctx)
    assert len(orders) == 1
    assert orders[0]["type"] == "MOVE"
    assert orders[0]["payload"]["to"] == "bel"


def test_scripted_runtime_writes_filesystem_artifacts_for_full_lifecycle(tmp_path) -> None:
    runtime = ScriptedAgentRuntime(
        schema_version="1.0",
        runtime_root=tmp_path / "agents",
        map_def=load_standard_map(),
        seed=11,
    )
    runtime.register_agent(agent_id="a1", kind="hold")
    ctx = AgentContext(
        schema_version="1.0",
        experiment_id="exp",
        game_id="g1",
        phase_id="1901_spring_movement",
        agent_id="a1",
        country_assignment="france",
        visible_state=_state("movement", [_unit("u1", "france", "A", "par")]),
    )
    tick = runtime.negotiate_tick(ctx)
    orders = runtime.submit_orders(ctx)
    reflection = runtime.reflect(ctx)
    cleanup = runtime.game_end_cleanup(
        AgentContext(
            schema_version="1.0",
            experiment_id="exp",
            game_id="g1",
            phase_id="game_end_cleanup",
            agent_id="a1",
            country_assignment="france",
            visible_state={"schema_version": "1.0", "game_id": "g1"},
        )
    )

    assert tick.schema_version == "1.0"
    assert orders.schema_version == "1.0"
    assert reflection.schema_version == "1.0"
    assert cleanup.schema_version == "1.0"

    out_files = sorted((tmp_path / "agents" / "a1" / "runtime" / "phase_outputs").glob("*.json"))
    assert len(out_files) >= 4
    for path in out_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.0"
