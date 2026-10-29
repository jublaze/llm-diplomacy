import copy

from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.rules.diplomacy.engine import DiplomacyRuleEngine


def test_build_waivers_capacity_occupancy_and_disbands(tmp_path):
    builder = PromptContextBuilder(folio_root=tmp_path)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    engine = DiplomacyRuleEngine()
    state = dict(year=1908, season="winter", phase="adjustment", units=[
        dict(unit_id="bre", owner="france", type="A", province="bre", status="active"),
        dict(unit_id="par", owner="france", type="A", province="par", status="active"),
        dict(unit_id="mar", owner="france", type="F", province="mar", status="active"),
    ], supply_ownership={p: "france" for p in ("bre", "par", "mar", "bel")})

    def check(board, orders):
        adjustment = builder._adjustment_context(state=board, country="france")
        ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment",
                           "metternich", "france", board)
        return adjustment, runtime._validate_submit_orders_legality(
            context=ctx, built_context={"legal_order_context": {"adjustment": adjustment}}, orders=orders)

    blocked, issue = check(state, [])
    assert (blocked["minimum_count"], blocked["maximum_count"], issue) == (0, 0, None)
    assert engine._resolve_adjustment_phase(state, [])["adjustment_outcomes"] == []

    open_site = copy.deepcopy(state)
    open_site["units"] = open_site["units"][:2]
    options, issue = check(open_site, [])
    assert (options["delta"], options["maximum_count"], issue) == (2, 1, None)
    assert check(open_site, [options["legal_options"][0]])[1] is None
    assert check(open_site, options["legal_options"])[1] is not None
    assert check(open_site, [options["legal_options"][0]] * 2)[1] is not None
    hostile = copy.deepcopy(open_site)
    hostile["units"].append(dict(unit_id="enemy", owner="germany", type="A", province="mar", status="active"))
    assert check(hostile, [])[0]["legal_options"] == []
    assert not engine._can_build(power="france", unit_type="A", province_id="mar", coast=None,
        output_units=hostile["units"], owned_centers=list(hostile["supply_ownership"]),
        home_centers=["bre", "par", "mar"])
    disband = copy.deepcopy(state)
    disband["supply_ownership"] = {"par": "france", "bre": "france"}
    disband_context, issue = check(disband, [])
    assert issue == "adjustment requires exactly 1 DISBAND orders"
    assert check(disband, disband_context["legal_options"][:1])[1] is None


def test_split_coasts_and_army_share_one_physical_build_site(tmp_path):
    builder = PromptContextBuilder(folio_root=tmp_path)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    board = dict(year=1908, season="winter", phase="adjustment", units=[
        dict(unit_id="mos", owner="russia", type="A", province="mos", status="active"),
        dict(unit_id="sev", owner="russia", type="A", province="sev", status="active"),
    ], supply_ownership={p: "russia" for p in ("mos", "sev", "stp", "war")})
    adjustment = builder._adjustment_context(state=board, country="russia")
    stp = [x for x in adjustment["legal_options"] if x["payload"]["province"] == "stp"]
    assert len(stp) == 3 and adjustment["maximum_count"] == 2
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "russia", "russia", board)
    def issue(orders):
        return runtime._validate_submit_orders_legality(context=ctx,
            built_context={"legal_order_context": {"adjustment": adjustment}}, orders=orders)
    for option in stp:
        assert issue([option]) is None
    assert issue(stp[:2]) == "duplicate adjustment province: stp"
    disallowed = copy.deepcopy(stp[0])
    disallowed["payload"]["province"] = "par"
    assert issue([disallowed]) == "illegal adjustment order payload for BUILD"


def test_distinct_build_sites_do_not_override_entitlement():
    runtime = object.__new__(BaselineLLMAgentRuntime)
    board = {"phase": "adjustment"}
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "france", "france", board)
    options = [
        {"type": "BUILD", "payload": {"unit_type": "A", "province": province, "coast": None}}
        for province in ("bre", "par")
    ]
    adjustment = {"obligation": "build", "delta": 1, "required_count": 0,
                  "minimum_count": 0, "maximum_count": 1, "legal_options": options}
    def issue(orders):
        return runtime._validate_submit_orders_legality(context=ctx,
            built_context={"legal_order_context": {"adjustment": adjustment}}, orders=orders)
    assert issue([]) is None
    assert issue(options[:1]) is None
    assert issue(options) == "adjustment permits at most 1 BUILD orders"


def test_exact_disbands_reject_duplicate_unit_even_if_each_order_is_legal():
    runtime = object.__new__(BaselineLLMAgentRuntime)
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "france", "france", {"phase": "adjustment"})
    options = [{"type": "DISBAND", "payload": {"unit_ref": unit}} for unit in ("one", "two", "three")]
    adjustment = {"obligation": "disband", "delta": -2, "required_count": 2, "legal_options": options}
    def issue(orders):
        return runtime._validate_submit_orders_legality(context=ctx,
            built_context={"legal_order_context": {"adjustment": adjustment}}, orders=orders)
    assert issue(options[:1]) == "adjustment requires exactly 2 DISBAND orders"
    assert issue(options[:2]) is None
    assert issue([options[0], options[0]]) == "duplicate adjustment unit_ref: one"


def test_explicit_legacy_adjustment_preserves_original_prompt_fields_and_validation(tmp_path):
    builder = PromptContextBuilder(folio_root=tmp_path)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    board = dict(year=1908, season="winter", phase="adjustment", units=[
        dict(unit_id="bre", owner="france", type="A", province="bre", status="active"),
        dict(unit_id="par", owner="france", type="A", province="par", status="active"),
        dict(unit_id="mar", owner="france", type="F", province="mar", status="active"),
    ], supply_ownership={p: "france" for p in ("bre", "par", "mar", "bel")})
    legacy = builder._adjustment_context(state=board, country="france", legacy=True)
    assert legacy == {"obligation": "build", "delta": 1, "required_count": 1, "legal_options": []}
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "metternich", "france", board)
    built = {"legal_order_context": {"adjustment": legacy}}
    assert runtime._validate_submit_orders_legality(context=ctx, built_context=built, orders=[],
        legacy_adjustment=True) == "adjustment requires exactly 1 BUILD orders"
    assert builder._adjustment_context(state=board, country="france")["maximum_count"] == 0


def test_two_build_entitlement_one_site_builds_once_and_next_winter_recomputes(tmp_path):
    builder = PromptContextBuilder(folio_root=tmp_path)
    runtime = object.__new__(BaselineLLMAgentRuntime)
    engine = DiplomacyRuleEngine()
    board = dict(schema_version="1.0", map_id="standard", year=1908, season="winter", phase="adjustment",
        units=[dict(schema_version="1.0", unit_id=unit, owner="france", type="A", province=province,
                    coast=None, status="active", must_retreat=False, dislodged_from=None)
               for unit, province in (("fra_bre", "bre"), ("fra_par", "par"))],
        supply_ownership={province: "france" for province in ("bre", "par", "mar", "bel")}, home_centers={})
    adjustment = builder._adjustment_context(state=board, country="france")
    assert adjustment["delta"] == 2 and adjustment["maximum_count"] == 1
    option = next(row for row in adjustment["legal_options"] if row["payload"]["unit_type"] == "A")
    ctx = AgentContext("1.0", "synthetic", "game_001", "1908_winter_adjustment", "metternich", "france", board)
    assert runtime._validate_submit_orders_legality(context=ctx,
        built_context={"legal_order_context": {"adjustment": adjustment}}, orders=[option]) is None
    order = {"schema_version": "1.0", "order_id": "build_mar", "power": "france", **option}
    winter = engine.resolve_phase(board, engine.validate_orders(board, [order]))
    assert [row["result"] for row in winter["adjustment_outcomes"]].count("built") == 1
    after_winter = winter["output_state"]
    assert sum(unit["owner"] == "france" and unit["status"] == "active" for unit in after_winter["units"]) == 3
    spring = {**copy.deepcopy(after_winter), "year": 1909, "season": "spring", "phase": "movement"}
    spring_result = engine.resolve_phase(spring, engine.validate_orders(spring, []))
    spring_state = spring_result["output_state"]
    assert sum(unit["owner"] == "france" and unit["status"] == "active" for unit in spring_state["units"]) == 3
    built_unit = next(unit for unit in spring_state["units"] if unit["owner"] == "france" and unit["province"] == "mar")
    fall = {**copy.deepcopy(spring_state), "year": 1909, "season": "fall", "phase": "movement"}
    move = {"order_id": "move_from_mar", "unit_ref": built_unit["unit_id"], "power": "france",
            "type": "MOVE", "payload": {"from": "mar", "to": "bur"}}
    fall_state = engine.resolve_phase(fall, engine.validate_orders(fall, [move]))["output_state"]
    assert any(unit["unit_id"] == built_unit["unit_id"] and unit["province"] == "bur" for unit in fall_state["units"])
    next_open_winter = {**copy.deepcopy(fall_state), "year": 1909, "season": "winter", "phase": "adjustment"}
    next_available = builder._adjustment_context(state=next_open_winter, country="france")
    assert (next_available["delta"], next_available["maximum_count"]) == (1, 1)
    assert {row["payload"]["province"] for row in next_available["legal_options"]} == {"mar"}
    next_winter = {**copy.deepcopy(spring_state), "year": 1909, "season": "winter", "phase": "adjustment"}
    next_winter["supply_ownership"]["bel"] = "england"
    recalculated = builder._adjustment_context(state=next_winter, country="france")
    assert recalculated["delta"] == 0 and recalculated["obligation"] == "none"
