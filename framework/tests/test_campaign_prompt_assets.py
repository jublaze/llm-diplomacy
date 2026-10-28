from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from llm_diplomacy.factory.generator import AgentFactory
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_context import PromptContextBuilder
from llm_diplomacy.core.prompt_assets import (
    DEFAULT_STRATEGIC_IMPERATIVES,
    DEFAULT_VICTORY_CAMPAIGN_DOCTRINE,
    canonical_strategic_imperatives_path,
    canonical_victory_campaign_doctrine_path,
    read_prompt_asset,
)
from llm_diplomacy.runner.scripted_experiment_runner import ScriptedExperimentRunner


DOCTRINE_VERSION = "victory_campaign_doctrine_v1_5_initiative_test"
REQUIRED_INITIATIVE_TEST = (
    "Do not mistake a quiet border, a surviving alliance, or the absence of immediate danger for strategic progress. "
    "Your campaign plan should explain how your current position can plausibly produce relative growth. If it cannot, "
    "identify a self-interested diplomatic, coalition, or tactical initiative that could change the opportunity set. "
    "State what it could gain, what it risks, which other powers’ incentives it depends on, and what new evidence "
    "would make you pursue, defer, revise, or abandon it."
)
REFLECTION_REQUIRED_FIELDS = [
    "schema_version",
    "phase_summary",
    "strategic_position",
    "objective_update",
    "relationship_updates",
    "action_lessons",
    "memory_candidates",
    "belief_summary",
    "outcome_review",
    "campaign_plan",
    "commitment_memory",
    "winter_assessment",
]
REFLECTION_PROPERTY_FIELDS = {
    *REFLECTION_REQUIRED_FIELDS,
    "belief_updates",
    "explanation",
}


def _context() -> AgentContext:
    return AgentContext(
        schema_version="1.0", experiment_id="exp", game_id="campaign", phase_id="1901_spring_movement",
        agent_id="agent_england", country_assignment="england",
        visible_state={"game_id": "campaign", "year": 1901, "season": "spring", "phase": "movement", "units": [], "supply_ownership": {}},
    )


def test_campaign_assets_have_versions_and_consistent_fallbacks(tmp_path) -> None:
    strategic = canonical_strategic_imperatives_path().read_text(encoding="utf-8")
    doctrine = canonical_victory_campaign_doctrine_path().read_text(encoding="utf-8")
    assert "strategic_imperatives_version: strategic_imperatives_v2_1_campaign_oriented" in strategic
    assert f"victory_campaign_doctrine_version: {DOCTRINE_VERSION}" in doctrine
    assert "strategic_imperatives_version: strategic_imperatives_v2_1_campaign_oriented" in DEFAULT_STRATEGIC_IMPERATIVES
    assert f"victory_campaign_doctrine_version: {DOCTRINE_VERSION}" in DEFAULT_VICTORY_CAMPAIGN_DOCTRINE
    for text in (strategic, DEFAULT_STRATEGIC_IMPERATIVES):
        assert "A draw is preferable to surviving without sharing in the draw" in text
        assert "Send messages when they advance a current campaign" in text
        assert "need not be honest" not in text
    for text in (doctrine, DEFAULT_VICTORY_CAMPAIGN_DOCTRINE):
        assert "A plan may be uncertain, conditional, or abandoned" in text
        assert "Express the hypothesis in concise prose appropriate to the current position" in text
        assert "need not be honest" not in text
        assert "A campaign is not only a desired direction." in text
        assert "You may communicate a capability or conditional offer without disclosing every final order." in text
        assert "Do not repeat a failed route by default." in text
        assert "When your campaign is failing or your position is deteriorating, re-evaluate the whole board—including which powers may gain from checking your opponents—and pursue a coherent revised plan for survival and renewed leverage rather than repeating a defeated line." in text
    assert read_prompt_asset(tmp_path / "missing-strategic.md", fallback=DEFAULT_STRATEGIC_IMPERATIVES) == DEFAULT_STRATEGIC_IMPERATIVES
    assert read_prompt_asset(tmp_path / "missing.md", fallback=DEFAULT_VICTORY_CAMPAIGN_DOCTRINE) == DEFAULT_VICTORY_CAMPAIGN_DOCTRINE


def test_canonical_and_fallback_doctrine_contain_exact_initiative_test() -> None:
    doctrine = canonical_victory_campaign_doctrine_path().read_text(encoding="utf-8")
    assert doctrine.count(REQUIRED_INITIATIVE_TEST) == 1
    assert DEFAULT_VICTORY_CAMPAIGN_DOCTRINE.count(REQUIRED_INITIATIVE_TEST) == 1
    assert doctrine.index(REQUIRED_INITIATIVE_TEST) < doctrine.index("Do not repeat a failed route by default.")
    assert DEFAULT_VICTORY_CAMPAIGN_DOCTRINE.index(REQUIRED_INITIATIVE_TEST) < DEFAULT_VICTORY_CAMPAIGN_DOCTRINE.index(
        "Do not repeat a failed route by default."
    )


def test_legacy_prompt_metadata_without_campaign_asset_paths_is_readable() -> None:
    runner = object.__new__(ScriptedExperimentRunner)
    runner.logger = None
    metadata = runner._prompt_assets_in_prompt(prompt_text="", llm_meta={})
    assert set(metadata) >= {"strategic_imperatives", "victory_campaign_doctrine", "diplomacy_primer"}
    assert metadata["strategic_imperatives"]["included_in_prompt"] is False
    assert metadata["victory_campaign_doctrine"]["included_in_prompt"] is False


def test_new_doctrine_version_is_present_in_prompt_metadata() -> None:
    doctrine = canonical_victory_campaign_doctrine_path().read_text(encoding="utf-8").strip()
    runner = object.__new__(ScriptedExperimentRunner)
    runner.logger = None
    metadata = runner._prompt_assets_in_prompt(prompt_text=doctrine, llm_meta={})
    doctrine_metadata = metadata["victory_campaign_doctrine"]
    assert doctrine_metadata["victory_campaign_doctrine_version"] == DOCTRINE_VERSION
    assert doctrine_metadata["included_in_prompt"] is True


def test_doctrine_is_non_prescriptive_and_runtime_guidance_is_campaign_aware(tmp_path) -> None:
    canonical_doctrine = canonical_victory_campaign_doctrine_path().read_text(encoding="utf-8").strip()
    doctrine = canonical_doctrine.lower()
    prohibited = ("must support", "required alliance", "support quota", "attack quota", "always attack", "attack every phase", "england playbook", "strategic-chain scoring")
    assert not any(phrase in doctrine for phrase in prohibited)
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    built = builder.build(_context())
    reflect = builder.build_prompt(method="reflect", built_context=built)
    orders = builder.build_prompt(method="submit_orders", built_context=built)
    negotiate = builder.build_prompt(method="negotiate_tick", built_context=built)
    assert "current campaign hypothesis" in reflect
    assert "Apply the initiative test to campaign_plan" in reflect
    assert "a defended position or current alliance alone is incomplete" in reflect
    assert "a reasoned temporary posture that advances your campaign and the evidence that would trigger revision" in reflect
    assert '"campaign_plan"' in reflect
    assert "current campaign hypothesis" in orders
    assert "test or advance concrete campaign opportunities" in negotiate
    for prompt in (orders, negotiate):
        assert canonical_doctrine in prompt
        assert f"victory_campaign_doctrine_version: {DOCTRINE_VERSION}" in prompt
        assert REQUIRED_INITIATIVE_TEST in prompt


def test_advanced_profiles_keep_solo_primary_and_survival_secondary_in_generated_prompt(tmp_path) -> None:
    factory = AgentFactory(schema_version="1.0")
    for profile in ("birsan", "dolos", "metternich", "shaver", "major"):
        folio = factory.create_agent(Path("blueprints") / f"{profile}.yaml", agents_root=tmp_path / profile / "agents")
        soul = (folio / "soul.md").read_text(encoding="utf-8")
        assert len(soul) <= 3000, profile
        assert "pursue a solo victory at 18 centers; treat survival as secondary" in soul, profile
        runtime_root = tmp_path / profile / "runtime"
        runtime_folio = runtime_root / folio.name / "folio"
        runtime_folio.mkdir(parents=True)
        (runtime_folio / "soul.md").write_text(soul, encoding="utf-8")
        context = replace(_context(), agent_id=folio.name)
        builder = PromptContextBuilder(folio_root=runtime_root)
        built = builder.build(context)
        for method in ("submit_orders", "negotiate_tick", "reflect", "game_end_cleanup"):
            prompt = builder.build_prompt(method=method, built_context=built)
            assert "pursue a solo victory at 18 centers; treat survival as secondary" in prompt, (profile, method)
    metternich = (tmp_path / "metternich" / "agents" / "metternich" / "soul.md").read_text(encoding="utf-8")
    assert "convert coalition influence into my own path to 18 centers" in metternich


def test_reflection_schema_and_required_fields_are_unchanged(tmp_path) -> None:
    builder = PromptContextBuilder(folio_root=tmp_path / "folio")
    schema = builder._method_schema("reflect")
    assert schema["required"] == REFLECTION_REQUIRED_FIELDS
    assert set(schema["properties"]) == REFLECTION_PROPERTY_FIELDS
    assert schema["properties"]["campaign_plan"] == {
        "type": "string",
        "description": "Required free-form, revisable game-scoped plan; max 600 words.",
    }
    assert builder._method_example("reflect")["schema_version"] == "reflection_compact_v3_campaign_memory"
