from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .contracts import AgentContext
from llm_diplomacy.core.prompt_assets import (
    DEFAULT_DIPLOMACY_PRIMER,
    DEFAULT_STRATEGIC_IMPERATIVES,
    DEFAULT_VICTORY_CAMPAIGN_DOCTRINE,
    OUTCOME_REVIEW_INSTRUCTION,
    canonical_diplomacy_primer_path,
    canonical_strategic_imperatives_path,
    canonical_victory_campaign_doctrine_path,
    read_prompt_asset,
)
from llm_diplomacy.map.loader import load_standard_map


class _ReflectionPhaseUnavailable(Exception):
    pass


class PromptContextBuilder:
    """Builds authorized prompt context only."""

    def __init__(
        self,
        *,
        folio_root: str | Path,
        strategic_imperatives_path: str | Path | None = None,
        victory_campaign_doctrine_path: str | Path | None = None,
        diplomacy_primer_path: str | Path | None = None,
    ) -> None:
        self.folio_root = Path(folio_root)
        self.strategic_imperatives_path = (
            Path(strategic_imperatives_path)
            if strategic_imperatives_path is not None
            else canonical_strategic_imperatives_path()
        )
        self.victory_campaign_doctrine_path = (
            Path(victory_campaign_doctrine_path)
            if victory_campaign_doctrine_path is not None
            else canonical_victory_campaign_doctrine_path()
        )
        self.diplomacy_primer_path = (
            Path(diplomacy_primer_path)
            if diplomacy_primer_path is not None
            else canonical_diplomacy_primer_path()
        )
        self.map_def = load_standard_map()
        self.province_by_id = {province.id: province for province in self.map_def.provinces}
        self.coast_by_id = {coast.id: coast for coast in self.map_def.coasts}
        self.convoy_passable_seas = set(self.map_def.convoy_passable_seas)

    def build(self, context: AgentContext) -> dict[str, Any]:
        visible_state = dict(context.visible_state)
        context_warnings: list[str] = []
        private_messages = [
            row
            for row in visible_state.get("private_messages", [])
            if isinstance(row, dict) and str((row.get("to") or {}).get("to_agent_id", "")) == context.agent_id
        ]
        if len(private_messages) > 40:
            private_messages = private_messages[-40:]
            context_warnings.append("private_messages_truncated_to_last_40")
        global_messages = [
            row for row in visible_state.get("global_messages", []) if isinstance(row, dict)
        ]
        if len(global_messages) > 40:
            global_messages = global_messages[-40:]
            context_warnings.append("global_messages_truncated_to_last_40")
        board_summary = self._board_summary(visible_state, context.country_assignment)
        authoritative_board_update = self._authoritative_board_update(visible_state)
        folio = self._read_folio(context.agent_id, visible_state.get("game_id"))
        structured_memory = (
            folio.get("game_memory_structured", {})
            if isinstance(folio.get("game_memory_structured"), dict)
            else {}
        )
        structured_memory = self._trim_structured_memory(structured_memory, context_warnings)
        belief_summary = self._trim_belief_summary(
            str(structured_memory.get("belief_summary", "")),
            context_warnings,
        )
        previous_commitments = {
            "strategic_intent": structured_memory.get("strategic_intent", {}),
            "belief_summary_phase_id": structured_memory.get("belief_summary_phase_id", ""),
            "campaign_plan": structured_memory.get("campaign_plan", ""),
            "campaign_plan_phase_id": structured_memory.get("campaign_plan_phase_id", ""),
            "commitment_memory": structured_memory.get("commitment_memory", ""),
            "commitment_memory_phase_id": structured_memory.get("commitment_memory_phase_id", ""),
            "winter_assessment": structured_memory.get("winter_assessment", ""),
            "winter_assessment_phase_id": structured_memory.get("winter_assessment_phase_id", ""),
        }
        persistent_memory = self._clip_text(
            str(folio.get("persistent_memory", "")),
            max_chars=3500,
            warning_key="persistent_memory_truncated",
            warnings=context_warnings,
        )
        in_game_memory = self._clip_text(
            json.dumps(
                {key: value for key, value in structured_memory.items() if key != "free_text_reflection"},
                sort_keys=True,
            ),
            max_chars=6000,
            warning_key="in_game_memory_truncated",
            warnings=context_warnings,
        )
        free_text_reflection = ""
        reflection_meta = structured_memory.get("free_text_reflection", {})
        if isinstance(reflection_meta, dict) and isinstance(reflection_meta.get("phase_id"), str):
            try:
                reflection_order = self._phase_order(reflection_meta["phase_id"])
                context_order = ((9999, 4, 4) if context.phase_id == "game_end_cleanup"
                                 else self._phase_order(context.phase_id))
                if reflection_order is None or context_order is None:
                    context_warnings.append("free_text_reflection_phase_order_unknown")
                    raise _ReflectionPhaseUnavailable
                if reflection_order >= context_order:
                    context_warnings.append("free_text_reflection_not_from_prior_phase")
                    raise _ReflectionPhaseUnavailable
                memory_game_id = str(visible_state.get("game_id", context.game_id))
                if Path(memory_game_id).name != memory_game_id or memory_game_id in {".", ".."}:
                    raise ValueError("unsafe reflection game identifier")
                archive_path = (
                    self.folio_root / context.agent_id / "folio" / "game_memory"
                    / memory_game_id / "reflections"
                    / f"{reflection_meta['phase_id']}.txt"
                )
                expected_root = (
                    self.folio_root / context.agent_id / "folio" / "game_memory"
                    / memory_game_id / "reflections"
                ).resolve()
                if Path(reflection_meta["phase_id"]).name != reflection_meta["phase_id"]:
                    raise ValueError("unsafe reflection phase identifier")
                archive_path.resolve().relative_to(expected_root)
                archive_bytes = archive_path.read_bytes()
                if hashlib.sha256(archive_bytes).hexdigest() != reflection_meta.get("sha256"):
                    raise ValueError("private reflection archive hash mismatch")
                full_reflection = archive_bytes.decode("utf-8")
                raw_bytes = full_reflection.encode("utf-8")
                max_bytes = 4096
                excerpt_bytes = raw_bytes[-max_bytes:]
                while True:
                    try:
                        free_text_reflection = excerpt_bytes.decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        excerpt_bytes = excerpt_bytes[1:]
                if len(raw_bytes) > len(excerpt_bytes):
                    warnings_count = len(raw_bytes) - len(excerpt_bytes)
                    context_warnings.append(f"free_text_reflection_excerpt_truncated_bytes:{warnings_count}")
                free_text_reflection = (
                    f"[Latest private reflection; archived in full. Prompt excerpt is at most {max_bytes} UTF-8 bytes. "
                    "UTF-8 bytes are a conservative upper bound for token count; exact tokenizer is unavailable.]\n"
                    + free_text_reflection
                )
            except (OSError, UnicodeError):
                context_warnings.append("free_text_reflection_archive_unavailable")
            except _ReflectionPhaseUnavailable:
                pass
        soul = self._clip_text(
            str(folio.get("soul", "")),
            max_chars=3000,
            warning_key="soul_truncated",
            warnings=context_warnings,
        )
        legal_order_context = self._legal_order_context(state=visible_state, country=context.country_assignment)
        return {
            "schema_version": context.schema_version,
            "agent_id": context.agent_id,
            "game_id": context.game_id,
            "country_assignment": context.country_assignment,
            "phase_id": context.phase_id,
            "board_summary": board_summary,
            "authoritative_board_update": authoritative_board_update,
            "visible_messages": {
                "private": private_messages,
                "global": global_messages,
            },
            "public_profiles": visible_state.get("public_profiles", {}),
            "leaderboard": visible_state.get("leaderboard", {}),
            "in_game_memory": in_game_memory,
            "free_text_reflection": free_text_reflection,
            "previous_commitments": previous_commitments,
            "belief_summary": belief_summary,
            "persistent_memory": persistent_memory,
            "soul": soul,
            "legal_order_context": legal_order_context,
            "context_warnings": context_warnings,
        }

    def build_prompt(self, *, method: str, built_context: dict[str, Any], legacy_relationship_limit: bool = False,
                     free_text_reflection: bool = False) -> str:
        schema = self._method_schema(method) if not (method == "reflect" and free_text_reflection) else None
        if legacy_relationship_limit and method in {"reflect", "negotiate_tick"}:
            schema["properties"]["relationship_updates"].pop("maxItems", None)
        example = self._method_example(method)
        strategic_imperatives = self._read_strategic_imperatives()
        victory_campaign_doctrine = self._read_victory_campaign_doctrine()
        diplomacy_primer = self._read_diplomacy_primer()
        if method == "reflect" and free_text_reflection:
            board = built_context.get("board_summary", {})
            phase_rules = (
                f"Game: {built_context.get('game_id')} | Year: {board.get('year')} | "
                f"Season: {board.get('season')} | Phase: {board.get('phase')}\n"
                "Invocation method: reflect\n"
                "Use only visible information. Consider what changed this phase and what you learned."
            )
        else:
            phase_rules = self._phase_rules_block(built_context=built_context, method=method)
        if legacy_relationship_limit:
            phase_rules = phase_rules.replace("relationship_updates: maximum 6", "relationship_updates: maximum 3")
            phase_rules = phase_rules.replace("- Track up to six other powers in relationship_updates (maximum 6 entries).\n", "")
        visible_messages = built_context.get("visible_messages", {})
        legal_order_context_section = ""
        if method == "submit_orders":
            legal_order_context_section = (
                "# Legal Order Context\n"
                f"{json.dumps(built_context.get('legal_order_context', {}), sort_keys=True)}\n\n"
            )
        common = (
            "# Strategic Imperatives\n"
            f"{strategic_imperatives}\n\n"
            "# Victory and Campaign Doctrine\n"
            f"{victory_campaign_doctrine}\n\n"
            "# Diplomacy Primer\n"
            f"{diplomacy_primer}\n\n"
            "# Current Game Rules / Phase\n"
            f"{phase_rules}\n\n"
            "# Soul\n"
            f"{built_context.get('soul', '')}\n\n"
            "# Persistent Memory\n"
            f"{built_context.get('persistent_memory', '')}\n\n"
            "# Authoritative Current Board State\n"
            f"{json.dumps(built_context.get('board_summary', {}), sort_keys=True)}\n\n"
            "# Latest Authoritative Resolution / Board Delta\n"
            f"{json.dumps(built_context.get('authoritative_board_update', {}), sort_keys=True)}\n"
            "The board state and adjudicated resolution above are authoritative public facts. Use them to evaluate what powers actually did and compare actions with messages or promises. Do not claim province-level board evidence is unavailable when it is shown here.\n\n"
            "# Current Belief Summary (Private Interpretation)\n"
            f"{built_context.get('belief_summary', '')}\n"
            "This is an interpretation, not an alternative source of truth. Authoritative board state and adjudicated resolutions override it. Revise stale beliefs when authoritative board facts contradict them, and preserve uncertainty when evidence remains incomplete.\n\n"
            "# Current Game Memory (Historical Evidence)\n"
            f"{built_context.get('in_game_memory', '')}\n\n"
            "# Previous Commitments\n"
            f"{json.dumps(built_context.get('previous_commitments', {}), sort_keys=True)}\n"
            "These are strategy commitments from the previous reflection. If you change direction, explain why.\n\n"
            "# Current Campaign Plan (Private, Game-Scoped)\n"
            f"{built_context.get('previous_commitments', {}).get('campaign_plan', '')}\n\n"
            "# Commitment Memory (Private, Game-Scoped)\n"
            f"{built_context.get('previous_commitments', {}).get('commitment_memory', '')}\n\n"
            "# Latest Winter Assessment (Private, Game-Scoped)\n"
            f"{built_context.get('previous_commitments', {}).get('winter_assessment', '')}\n\n"
            f"{legal_order_context_section}"
            "# Visible Communications\n"
            f"{json.dumps(visible_messages, sort_keys=True)}\n\n"
        )
        if free_text_reflection:
            common += "# Latest Private Prose Memory\n" + str(built_context.get("free_text_reflection", "")) + "\n\n"
        if method == "reflect" and free_text_reflection:
            return (
                common
                + "# Private Reflection\n"
                "Write a plain text reflection in your own words about the current phase and what you learned. "
                "It is private memory, not an order or message. No schema, JSON, required headings, or strategic claims are needed. "
                "Distinguish observations from interpretations and preserve uncertainty. Keep the output within the provider's configured token limit.\n"
            )
        return (
            common
            + "# Required JSON Schema\n"
            "Output requirements:\n"
            "- Return exactly one JSON object.\n"
            "- Do not include markdown, code fences, commentary, or backticks.\n"
            "- Use double quotes for all keys and strings.\n"
            "- Include all required fields for this method schema.\n"
            f"Method: {method}\n"
            f"Required schema:\n{json.dumps(schema, sort_keys=True)}\n"
            f"Valid example:\n{json.dumps(example, sort_keys=True)}\n"
        )

    def build_repair_prompt(
        self,
        *,
        method: str,
        built_context: dict[str, Any],
        prior_output: str,
        issue: str,
    ) -> str:
        schema = self._method_schema(method)
        example = self._method_example(method)
        repair_context = self._repair_context(method=method, built_context=built_context)
        return (
            f"Your previous response was invalid for method {method}.\n"
            f"Issue: {issue}\n"
            "Rewrite the response as exactly one valid JSON object and nothing else.\n"
            "Do not include markdown or prose.\n"
            f"Required schema:\n{json.dumps(schema, sort_keys=True)}\n"
            f"Valid example:\n{json.dumps(example, sort_keys=True)}\n"
            f"Previous invalid output:\n{prior_output}\n"
            f"Context:\n{json.dumps(repair_context, sort_keys=True)}\n"
        )

    def _read_strategic_imperatives(self) -> str:
        return read_prompt_asset(
            self.strategic_imperatives_path,
            fallback=DEFAULT_STRATEGIC_IMPERATIVES,
        )

    def _read_diplomacy_primer(self) -> str:
        return read_prompt_asset(
            self.diplomacy_primer_path,
            fallback=DEFAULT_DIPLOMACY_PRIMER,
        )

    def _read_victory_campaign_doctrine(self) -> str:
        return read_prompt_asset(
            self.victory_campaign_doctrine_path,
            fallback=DEFAULT_VICTORY_CAMPAIGN_DOCTRINE,
        )

    def _phase_rules_block(self, *, built_context: dict[str, Any], method: str) -> str:
        board = built_context.get("board_summary", {})
        season = board.get("season")
        phase = board.get("phase")
        year = board.get("year")
        base = (
            f"Game: {built_context.get('game_id')} | Year: {year} | Season: {season} | Phase: {phase}\n"
            f"Invocation method: {method}\n"
            "Use only visible information. Prefer actions that advance concrete strategic objectives."
        )
        if method == "reflect":
            visible_messages = built_context.get("visible_messages", {})
            private_msgs = visible_messages.get("private", []) if isinstance(visible_messages, dict) else []
            global_msgs = visible_messages.get("global", []) if isinstance(visible_messages, dict) else []
            communication_review = ""
            if not private_msgs and not global_msgs:
                communication_review = (
                    "\nCommunication review prompt:\n"
                    "- Did insufficient communication contribute to your current position?\n"
                )
            return (
                base
                + self._outcome_review_target_block(built_context)
                + "\nReflection requirements:\n"
                "- Do not write a diary entry. Keep output compact, concrete, and strategic.\n"
                "- Include what changed, why it matters, and what to do next.\n"
                "- Do not praise stable communication unless it created strategic value.\n"
                "- For each important relationship, explain objective value and limits.\n"
                "- Track up to six other powers in relationship_updates (maximum 6 entries).\n"
                "- If a relationship produced only reassurance, label it as low-value or stagnant.\n"
                "- Do not invent commitments.\n"
                "- Do not claim trust/cooperation without message/order evidence.\n"
                "- Treat silence as uncertainty, not cooperation.\n"
                "- Write belief_summary every reflection, even when no dramatic event occurred. It is your current interpretation, not a list of events.\n"
                "- Base it on what powers said, what they did, the current board, and contradictions or gaps between words and actions.\n"
                "- Distinguish what you know, infer, and do not know. Treat silence or cordial language as uncertainty, not cooperation.\n"
                "- Cover only beliefs that could affect diplomacy, orders, threats, opportunities, expansion, or survival. Let your personality decide how broadly to attend.\n"
                "- State what future evidence could change uncertain beliefs. Do not list every message or invent commitments.\n"
                "- When relevant, include the current campaign hypothesis: likely expansion route or bottleneck, what border arrangements enable, and evidence that would revise the route.\n"
                "- outcome_review is required, target 100–150 words, and must not exceed 200 words.\n"
                "- Maintain a concise campaign plan for your own position. State your present route toward eventual victory or a favorable draw, the near-term gains or defensive requirements that matter most, the relationships or tactical conditions on which it depends, and what evidence would cause you to revise it. Treat it as a working plan, not a promise or a fixed doctrine. Current authoritative board state and public adjudication override earlier assumptions.\n"
                "- Apply the initiative test to campaign_plan: a defended position or current alliance alone is incomplete. Explain a plausible route to relative growth, or a reasoned temporary posture that advances your campaign and the evidence that would trigger revision. If neither exists, identify a self-interested initiative that could change the opportunity set, with its possible gain, risk, incentive dependencies, and evidence for pursuing, deferring, revising, or abandoning it. This does not require an attack, betrayal, deception, alliance, message, or disclosure of intended orders.\n"
                "- Maintain a concise record of decision-relevant commitments and reliability evidence. Record what was promised, requested, claimed, or observed; preserve uncertainty when a claim has not been verified; and revise the record when public orders, outcomes, or later messages confirm or contradict it. Do not reduce relationships to labels or scores.\n"
                + ("- In Winter, reassess the campaign before treating adjustments as routine. Explain what the year’s center changes and adjustments reveal about the board, whether your current plan remains viable, and what opportunity, danger, or leverage should shape the next Spring.\n" if "_winter_" in str(built_context.get("phase_id", "")) else "")
                + f"- {OUTCOME_REVIEW_INSTRUCTION}\n"
                "- Target about 350 words and do not exceed 600 words.\n"
                "- relationship_updates.status is a short descriptive label, such as tentative_ally, conditional_partner, or stagnant_risk; examples are not exhaustive.\n"
                "- Prefer short statements (concise strings; avoid long prose)."
                + communication_review
            )
        if method == "negotiate_tick":
            return (
                base
                + "\nNegotiation requirements:\n"
                "- Return only valid JSON. Do not include markdown or prose outside the JSON object.\n"
                "- private_messages: maximum 4; global_messages: maximum 1; relationship_updates: maximum 6; withheld_information: maximum 2.\n"
                "- Private message bodies: maximum 90 words. Global message bodies: maximum 75 words.\n"
                "- relationship_updates.next_use and each withheld_information summary/reason: maximum 40 words.\n"
                "- relationship_updates.status and every message purpose are short descriptive labels (maximum 8 words). Examples: tentative_ally, high_value_pending, claim_test, border_probe; examples are not an exhaustive vocabulary.\n"
                "- Prefer a few high-value messages over broad diplomatic coverage. You do not need to contact every agent.\n"
                "- Prioritize recipients: immediate tactical threats; cooperation affecting this order phase; claims needing tests; concrete support, DMZ, or information opportunities; then strategic relationship repair.\n"
                "- Every message must contain a concrete request, offer, test, warning, boundary, information exchange, or relationship repair.\n"
                "- Do not send generic reassurance unless it serves a concrete strategic purpose. Keep analysis implicit and concise.\n"
                "- Use messages to test or advance concrete campaign opportunities when they exist; do not invent broad diplomacy or support offers.\n"
                "- Use each message for a distinct strategic purpose: obtaining or testing information, clarifying or revising a commitment, coordinating a legal action, changing another power’s incentives, or preserving a relationship that matters to your plan. Do not send messages merely to repeat yourself or to be agreeable without strategic purpose.\n"
                "- Preserve useful specificity: provinces, unit movements, support requests, DMZ terms, red lines, conditional bargains, clarification requests, claim tests, information trades, and threat warnings.\n"
                "- Prioritize message output over explanation. If no message is strategically valuable, return empty message lists rather than inventing diplomacy."
            )
        if method == "submit_orders":
            legal_context = built_context.get("legal_order_context", {})
            phase = str((built_context.get("board_summary") or {}).get("phase", ""))
            adjustment = legal_context.get("adjustment", {}) if isinstance(legal_context, dict) else {}
            adjustment_note = ""
            if phase == "adjustment":
                obligation = adjustment.get("obligation")
                if obligation == "none":
                    adjustment_note = (
                        "\nAdjustment-specific requirement:\n"
                        "- No build/disband required: return orders as an empty list.\n"
                        "- Do not output HOLD orders in winter adjustment.\n"
                    )
                else:
                    adjustment_note = (
                        "\nAdjustment-specific requirement:\n"
                        "- Do not output HOLD orders in winter adjustment.\n"
                        "- Use only listed legal build/disband options.\n"
                    )
            return (
                base
                + "\nOrder requirements:\n"
                "- Include order_rationale with goal_supported and why_this_advances_strategy.\n"
                "- Connect the rationale to the current campaign hypothesis, or explain how the order preserves or revises it.\n"
                "- For movement phases, use previous commitments as default direction unless changed with reason.\n"
                "- If all submitted orders are HOLD in a movement phase, include passivity_review with justified and reason."
                + adjustment_note
            )
        if method == "game_end_cleanup":
            return (
                base
                + "\nCleanup requirements:\n"
                "- Persistent memory should prioritize strategic lessons, not social summaries.\n"
                "- Prefer concise sections such as 'Strategic Lessons' and 'Agent-Specific Tendencies'.\n"
                "- For each important tendency, include strategic use and strategic risk."
            )
        return base

    @staticmethod
    def _outcome_review_target_block(built_context: dict[str, Any]) -> str:
        resolution = built_context.get("authoritative_board_update", {})
        if not isinstance(resolution, dict) or not resolution.get("available"):
            return ""
        phase_id = resolution.get("phase_id")
        if not isinstance(phase_id, str) or not phase_id.strip():
            return (
                "\nOutcome-review target: a prior public adjudication is available, but its canonical phase_id is unavailable in this legacy context. "
                "Review the shown adjudication and public final orders; do not invent a phase identity.\n"
            )
        human_phase = PromptContextBuilder._human_phase_name(phase_id)
        return (
            f"\nOutcome-review target: the just-adjudicated {human_phase} (`{phase_id}`). "
            "Review this adjudication and its public final orders, not an earlier phase.\n"
        )

    @staticmethod
    def _human_phase_name(phase_id: str) -> str:
        parts = phase_id.split("_")
        if len(parts) == 3 and parts[0].isdigit():
            year, season, phase = parts
            return f"{season.title()} {year} {phase.title()} phase"
        return "resolved phase"

    @staticmethod
    def _phase_order(phase_id: str) -> tuple[int, int, int] | None:
        match = re.fullmatch(r"(\d{4})_(spring|fall|winter)_(negotiation|movement|retreat|adjustment)", phase_id)
        if not match:
            return None
        year, season, stage = match.groups()
        season_order = {"spring": 0, "fall": 1, "winter": 2}[season]
        stage_order = {"negotiation": 0, "movement": 1, "retreat": 2, "adjustment": 3}[stage]
        return int(year), season_order, stage_order

    def _method_schema(self, method: str) -> dict[str, Any]:
        by_method: dict[str, dict[str, Any]] = {
            "negotiate_tick": {
                "type": "object",
                "required": [
                    "private_messages",
                    "global_messages",
                    "relationship_updates",
                    "withheld_information",
                ],
                "properties": {
                    "private_messages": {
                        "type": "array",
                        "description": "Each purpose is a short descriptive label; examples are non-exhaustive.",
                        "items": {
                            "type": "object",
                            "required": ["recipient", "body", "purpose"],
                        },
                    },
                    "global_messages": {
                        "type": "array",
                        "description": "Each purpose is a short descriptive label; examples are non-exhaustive.",
                        "items": {
                            "type": "object",
                            "required": ["body", "purpose"],
                        },
                    },
                    "relationship_updates": {
                        "type": "array",
                        "maxItems": 6,
                        "description": "Each status is a short descriptive label; examples are non-exhaustive.",
                        "items": {
                            "type": "object",
                            "required": ["agent_id", "status", "next_use"],
                        },
                    },
                    "withheld_information": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["summary", "reason"],
                        },
                    },
                },
            },
            "submit_orders": {
                "type": "object",
                "required": ["orders", "order_rationale"],
                "properties": {
                    "orders": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["unit_ref", "type", "payload"],
                        },
                    },
                    "order_rationale": {
                        "type": "object",
                        "required": ["goal_supported", "why_this_advances_strategy"],
                    },
                    "passivity_review": {
                        "type": "object",
                        "required": ["justified", "reason"],
                    },
                    "explanation": {"type": "object"},
                },
            },
            "reflect": {
                "type": "object",
                "required": [
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
                ],
                "properties": {
                    "schema_version": {"type": "string"},
                    "phase_summary": {"type": "string"},
                    "strategic_position": {"type": "string"},
                    "objective_update": {
                        "type": "object",
                        "required": ["current_objective", "why_it_matters"],
                    },
                    "relationship_updates": {
                        "type": "array",
                        "maxItems": 6,
                        "description": "Each status is a short descriptive label; examples are non-exhaustive.",
                        "items": {
                            "type": "object",
                            "required": [
                                "agent_id",
                                "status",
                                "evidence",
                                "value_to_my_objectives",
                                "risk_or_limit",
                                "next_use",
                            ],
                        },
                    },
                    "action_lessons": {"type": "array", "items": {"type": "string"}},
                    "memory_candidates": {"type": "array", "items": {"type": "string"}},
                    "belief_summary": {
                        "type": "string",
                        "description": "Current free-form interpretation of powers, board position, agreements, contradictions, uncertainty, and future evidence to watch; target about 350 words, max 600 words.",
                    },
                    "outcome_review": {
                        "type": "string",
                        "description": "Required evidence-grounded review of the immediately preceding adjudication; target 100–150 words, maximum 200 words.",
                    },
                    "campaign_plan": {"type": "string", "description": "Required free-form, revisable game-scoped plan; max 600 words."},
                    "commitment_memory": {"type": "string", "description": "Required free-form game-scoped record of concrete commitments and evidence; max 450 words."},
                    "winter_assessment": {"type": ["string", "null"], "description": "Required non-empty in Winter only; otherwise null; max 350 words."},
                    "belief_updates": {
                        "type": "array",
                        "description": "Optional legacy compatibility field. New reflections should use belief_summary instead.",
                    },
                    "explanation": {"type": "object"},
                },
            },
            "game_end_cleanup": {
                "type": "object",
                "required": ["persistent_memory_update", "public_profile_update"],
                "properties": {
                    "persistent_memory_update": {"type": "string"},
                    "public_profile_update": {"type": ["object", "null"]},
                    "explanation": {"type": "object"},
                },
            },
        }
        return by_method.get(method, {"type": "object"})

    def _method_example(self, method: str) -> dict[str, Any]:
        by_method: dict[str, dict[str, Any]] = {
            "negotiate_tick": {
                "private_messages": [
                    {
                        "recipient": "england",
                        "body": "Will you keep the Channel clear this spring if I leave Picardy? Confirm before orders.",
                        "purpose": "border_probe",
                    }
                ],
                "global_messages": [],
                "relationship_updates": [
                    {
                        "agent_id": "england",
                        "status": "high_value_pending",
                        "next_use": "Test whether England will make a Channel commitment before committing Picardy.",
                    }
                ],
                "withheld_information": [],
            },
            "submit_orders": {
                "orders": [{"unit_ref": "u_001", "type": "HOLD", "payload": {"at": "par"}}],
                "order_rationale": {
                    "goal_supported": "Avoid immediate tactical exposure.",
                    "why_this_advances_strategy": "Preserves position while awaiting coordination evidence.",
                    "why_not_more_aggressive": "No confirmed support commitments this phase.",
                },
                "passivity_review": {"justified": True, "reason": "No safe expansion line was available."},
                "explanation": {"summary": "Hold baseline."},
            },
            "reflect": {
                "schema_version": "reflection_compact_v3_campaign_memory",
                "phase_summary": "No concrete coordination changed this phase.",
                "strategic_position": "Position is stable but strategic conversion remains weak.",
                "objective_update": {
                    "current_objective": "Obtain one concrete support or border commitment next phase.",
                    "why_it_matters": "Converts communication into defensive leverage or expansion options.",
                },
                "relationship_updates": [
                    {
                        "agent_id": "italy",
                        "status": "stagnant_relationship",
                        "evidence": "Messages remained cordial but non-specific.",
                        "value_to_my_objectives": "Limited defensive reassurance only.",
                        "risk_or_limit": "No concrete support commitments were secured.",
                        "next_use": "Ask for province-specific commitment.",
                    }
                ],
                "action_lessons": ["Repeated reassurance without concrete support is low-value."],
                "memory_candidates": [
                    "Treat vague reassurance as low-value unless it produces concrete tactical commitments."
                ],
                "belief_summary": "Italy's cordial but non-specific messages remain weak evidence of cooperation. I should seek a province-specific commitment before treating Italy as a useful partner. Other relationships remain uncertain until orders or adjudicated outcomes provide stronger evidence.",
                "outcome_review": "The immediately preceding adjudication matched the expected holds, so no promised tactical effect was disproved. The public orders confirm no unexpected support or movement. This is demonstrated by the published order record and outcome, not an inference about motives. My next action is to test a concrete province-specific commitment rather than treating cordial messages as cooperation.",
                "campaign_plan": "Maintain a conditional expansion route while preserving a defensible position; revise after public adjudication changes the route or its prerequisites.",
                "commitment_memory": "No decision-relevant commitments are currently verified beyond the public record.",
                "winter_assessment": None,
                "explanation": {"summary": "Converted observations into actionable next steps."},
            },
            "game_end_cleanup": {
                "persistent_memory_update": "Early communication volume correlated with better coordination.",
                "public_profile_update": None,
                "explanation": {"summary": "Stored cross-game learning."},
            },
        }
        return by_method.get(method, {})

    def _read_folio(self, agent_id: str, game_id: str | None) -> dict[str, str]:
        root = self.folio_root / agent_id / "folio"
        root.mkdir(parents=True, exist_ok=True)
        soul_path = root / "soul.md"
        persistent_path = root / "persistent_memory.md"
        public_profile_path = root / "public_profile.md"
        game_memory_dir = root / "game_memory"
        game_memory_dir.mkdir(parents=True, exist_ok=True)
        game_memory_path = game_memory_dir / f"{game_id or 'unknown_game'}.md"
        game_memory_json_path = game_memory_dir / f"{game_id or 'unknown_game'}.json"

        if not soul_path.exists():
            soul_path.write_text("Baseline LLM agent soul.", encoding="utf-8")
        if not persistent_path.exists():
            persistent_path.write_text("", encoding="utf-8")
        if not public_profile_path.exists():
            public_profile_path.write_text("", encoding="utf-8")
        if not game_memory_path.exists():
            game_memory_path.write_text("", encoding="utf-8")
        if not game_memory_json_path.exists():
            game_memory_json_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "objective_facts": [],
                        "belief_state": [],
                        "belief_summary": "",
                        "belief_summary_history": [],
                        "belief_summary_phase_id": "",
                        "strategic_intent": {},
                        "memory_log": [],
                    },
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
        structured_memory = {}
        try:
            structured_memory = json.loads(game_memory_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            structured_memory = {}

        return {
            "soul": soul_path.read_text(encoding="utf-8"),
            "persistent_memory": persistent_path.read_text(encoding="utf-8"),
            "public_profile": public_profile_path.read_text(encoding="utf-8"),
            "game_memory": json.dumps(structured_memory, sort_keys=True),
            "game_memory_structured": structured_memory,
        }

    def _trim_structured_memory(
        self,
        memory: dict[str, Any],
        warnings: list[str],
    ) -> dict[str, Any]:
        if not isinstance(memory, dict):
            return {}
        trimmed = dict(memory)
        objective_facts = trimmed.get("objective_facts", [])
        if isinstance(objective_facts, list) and len(objective_facts) > 40:
            trimmed["objective_facts"] = objective_facts[-40:]
            warnings.append("objective_facts_truncated_to_last_40")
        belief_state = trimmed.get("belief_state", [])
        if isinstance(belief_state, list) and len(belief_state) > 30:
            trimmed["belief_state"] = belief_state[-30:]
            warnings.append("belief_state_truncated_to_last_30")
        belief_history = trimmed.get("belief_summary_history", [])
        if isinstance(belief_history, list) and len(belief_history) > 12:
            trimmed["belief_summary_history"] = belief_history[-12:]
            warnings.append("belief_summary_history_truncated_to_last_12")
        memory_log = trimmed.get("memory_log", [])
        if isinstance(memory_log, list) and len(memory_log) > 20:
            trimmed["memory_log"] = memory_log[-20:]
            warnings.append("memory_log_truncated_to_last_20")
        return trimmed

    def _trim_belief_summary(self, value: str, warnings: list[str]) -> str:
        text = value.strip()
        words = text.split()
        if len(words) <= 600:
            return text
        segments = [
            segment.strip()
            for segment in re.split(r"(?<=[.!?])\s+|\n+", text)
            if segment.strip()
        ]
        kept: list[str] = []
        words_used = 0
        for segment in segments:
            segment_words = len(segment.split())
            if segment_words > 600:
                continue
            if words_used + segment_words > 600:
                break
            kept.append(segment)
            words_used += segment_words
        warnings.append("belief_summary_truncated_to_600_words")
        if kept:
            return " ".join(kept)
        return "Belief summary exceeded the supported length; use authoritative board state and historical evidence."

    def _recent_belief_updates(self, memory: dict[str, Any]) -> list[dict[str, str]]:
        rows = memory.get("belief_state", []) if isinstance(memory, dict) else []
        if not isinstance(rows, list):
            return []
        fields = (
            "phase_id",
            "subject",
            "prior_belief",
            "new_belief",
            "evidence",
            "evidence_class",
            "strategic_consequence",
            "next_test_or_action",
        )
        updates: list[dict[str, str]] = []
        for row in rows[-3:]:
            if not isinstance(row, dict):
                continue
            new_belief = row.get("new_belief", row.get("belief", ""))
            evidence = row.get("evidence", row.get("basis", ""))
            if not isinstance(new_belief, str) or not new_belief.strip():
                continue
            compact = {
                field: self._clip_text(str(row.get(field, "")).strip(), max_chars=280, warning_key="recent_belief_update_field_truncated", warnings=[])
                for field in fields
                if str(row.get(field, "")).strip()
            }
            compact["new_belief"] = self._clip_text(
                new_belief.strip(),
                max_chars=280,
                warning_key="recent_belief_update_field_truncated",
                warnings=[],
            )
            if isinstance(evidence, str) and evidence.strip():
                compact["evidence"] = self._clip_text(
                    evidence.strip(),
                    max_chars=280,
                    warning_key="recent_belief_update_field_truncated",
                    warnings=[],
                )
            updates.append(compact)
        return updates

    def _clip_text(
        self,
        text: str,
        *,
        max_chars: int,
        warning_key: str,
        warnings: list[str],
    ) -> str:
        if len(text) <= max_chars:
            return text
        warnings.append(warning_key)
        return text[: max_chars - 3] + "..."

    def _repair_context(self, *, method: str, built_context: dict[str, Any]) -> dict[str, Any]:
        if method == "reflect":
            return {
                "phase_id": built_context.get("phase_id"),
                "board_summary": built_context.get("board_summary", {}),
                "belief_summary": built_context.get("belief_summary", ""),
                "previous_commitments": built_context.get("previous_commitments", {}),
                "visible_messages_counts": {
                    "private": len((built_context.get("visible_messages", {}) or {}).get("private", []))
                    if isinstance(built_context.get("visible_messages", {}), dict)
                    else 0,
                    "global": len((built_context.get("visible_messages", {}) or {}).get("global", []))
                    if isinstance(built_context.get("visible_messages", {}), dict)
                    else 0,
                },
                "context_warnings": built_context.get("context_warnings", []),
            }
        if method == "submit_orders":
            return {
                "phase_id": built_context.get("phase_id"),
                "board_summary": built_context.get("board_summary", {}),
                "belief_summary": built_context.get("belief_summary", ""),
                "legal_order_context": built_context.get("legal_order_context", {}),
                "previous_commitments": built_context.get("previous_commitments", {}),
                "context_warnings": built_context.get("context_warnings", []),
            }
        return {
            "phase_id": built_context.get("phase_id"),
            "board_summary": built_context.get("board_summary", {}),
            "belief_summary": built_context.get("belief_summary", ""),
            "visible_messages": built_context.get("visible_messages", {}),
            "context_warnings": built_context.get("context_warnings", []),
        }

    def _board_summary(self, state: dict[str, Any], country: str | None) -> dict[str, Any]:
        units = [unit for unit in state.get("units", []) if isinstance(unit, dict)]
        own = [unit for unit in units if country is not None and unit.get("owner") == country]
        dislodged = [unit for unit in own if unit.get("status") == "dislodged" or unit.get("must_retreat")]
        units_by_power: dict[str, list[str]] = {}
        for unit in sorted(units, key=lambda row: (str(row.get("owner", "")), str(row.get("province", "")), str(row.get("unit_id", "")))):
            owner = str(unit.get("owner", "unknown"))
            location = str(unit.get("province", ""))
            unit_type = str(unit.get("type", ""))
            units_by_power.setdefault(owner, []).append(" ".join(part for part in (unit_type, location) if part))
        ownership = state.get("supply_ownership", {})
        centers_by_power: dict[str, list[str]] = {}
        if isinstance(ownership, dict):
            for province, owner in sorted(ownership.items(), key=lambda item: str(item[0])):
                if owner:
                    centers_by_power.setdefault(str(owner), []).append(str(province))
        return {
            "total_units": len(units),
            "own_units": len(own),
            "own_dislodged": len(dislodged),
            "season": state.get("season"),
            "phase": state.get("phase"),
            "year": state.get("year"),
            "all_visible_unit_positions_by_power": units_by_power,
            "supply_center_ownership_by_power": centers_by_power,
            "current_agent_units": [
                {key: unit.get(key) for key in ("unit_id", "type", "province", "coast", "status", "must_retreat")}
                for unit in own
            ],
            "current_agent_centers": centers_by_power.get(str(country), []) if country else [],
        }

    def _authoritative_board_update(self, state: dict[str, Any]) -> dict[str, Any]:
        """Expose public outcomes and final adjudicated orders, never current-phase orders."""
        resolution = state.get("latest_public_resolution", state.get("latest_resolution", {}))
        if not isinstance(resolution, dict) or not resolution:
            return {"available": False, "summary": "No adjudicated public resolution is available yet."}
        moves = [row for row in resolution.get("moves", resolution.get("movement_outcomes", [])) if isinstance(row, dict)]
        retreats = [row for row in resolution.get("retreats", resolution.get("retreat_outcomes", [])) if isinstance(row, dict)]
        changes = [row for row in resolution.get("center_changes", []) if isinstance(row, dict)]
        moved = [row for row in moves if str(row.get("result", "")) == "moved"]
        held = [row for row in moves if str(row.get("result", "")) in {"held", "hold"}]
        failed = [row for row in moves if str(row.get("result", "")) in {"failed", "bounced", "illegal"}]
        dislodged = [row for row in [*moves, *retreats] if str(row.get("result", "")) == "dislodged"]
        return {
            "available": True,
            "phase_id": resolution.get("phase_id"),
            "phase": resolution.get("phase"),
            "units_that_moved": moved,
            "units_that_held": held,
            "failed_or_bounced_orders": failed,
            "dislodgements_and_retreats": dislodged + retreats,
            "supply_center_changes": changes,
            "standoff_provinces": resolution.get("standoff_provinces", []),
            "public_final_orders": [
                row for row in resolution.get("public_final_orders", []) if isinstance(row, dict)
            ],
            "summary": "Public adjudication outcomes and final orders are available after resolution; current-phase orders remain secret before adjudication.",
        }

    def _legal_order_context(self, *, state: dict[str, Any], country: str | None) -> dict[str, Any]:
        phase = str(state.get("phase", "movement"))
        country_value = str(country or "")
        own_units = [
            unit
            for unit in state.get("units", [])
            if isinstance(unit, dict)
            and str(unit.get("owner", "")) == country_value
            and (
                (phase == "retreat" and (bool(unit.get("must_retreat")) or str(unit.get("status", "")) == "dislodged"))
                or (phase != "retreat" and str(unit.get("status", "active")) == "active")
            )
        ]
        own_units = sorted(own_units, key=lambda row: str(row.get("unit_id", "")))

        if phase == "movement":
            types = ["HOLD", "MOVE", "SUPPORT_HOLD", "SUPPORT_MOVE", "CONVOY"]
        elif phase == "retreat":
            types = ["RETREAT"]
        elif phase == "adjustment":
            types = ["BUILD", "DISBAND"]
        else:
            types = []

        your_units: list[dict[str, Any]] = []
        signatures_by_unit: dict[str, list[str]] = {}
        for unit in own_units:
            unit_ref = str(unit.get("unit_id", ""))
            if not unit_ref:
                continue
            legal_orders = self._legal_orders_for_unit(state=state, unit=unit, phase=phase)
            signatures_by_unit[unit_ref] = [
                self._order_signature(str(order.get("type", "")), order.get("payload", {}))
                for order in legal_orders
            ]
            your_units.append(
                {
                    "unit_ref": unit_ref,
                    "type": str(unit.get("type", "")),
                    "province": str(unit.get("province", "")),
                    "coast": unit.get("coast"),
                    "legal_orders": legal_orders,
                }
            )

        adjustment = self._adjustment_context(state=state, country=country_value)
        legal_context = {
            "phase": phase,
            "phase_legal_order_types": types,
            "your_units": your_units,
            "legal_order_signatures_by_unit": signatures_by_unit,
            "adjustment": adjustment,
        }
        if phase == "movement":
            route_options = self._convoy_route_options(state=state, country=country_value)
            legal_context["catalogue_notes"] = [
                "SUPPORT entries are engine-derived actions for current public unit positions.",
                "CONVOY entries and route options are derived from public board geometry; they still require matching simultaneous orders.",
            ]
            legal_context["convoy_route_options"] = route_options[:24]
            legal_context["convoy_route_options_truncated"] = len(route_options) > 24
        return legal_context

    def _legal_orders_for_unit(
        self,
        *,
        state: dict[str, Any],
        unit: dict[str, Any],
        phase: str,
    ) -> list[dict[str, Any]]:
        unit_ref = str(unit.get("unit_id", ""))
        unit_type = str(unit.get("type", ""))
        province = str(unit.get("province", ""))
        coast = unit.get("coast") if isinstance(unit.get("coast"), str) else None
        if not unit_ref or not province:
            return []

        if phase == "movement":
            orders: list[dict[str, Any]] = [{"type": "HOLD", "payload": {"at": province}}]
            for destination in self._legal_move_destinations(unit_type=unit_type, province=province, coast=coast):
                orders.append({"type": "MOVE", "payload": {"to": destination}})
            orders.extend(self._legal_support_orders(state=state, supporter=unit))
            orders.extend(self._legal_convoy_actions(state=state, unit=unit))
            return orders

        if phase == "retreat":
            occupied = {
                str(row.get("province", ""))
                for row in state.get("units", [])
                if isinstance(row, dict) and str(row.get("status", "active")) == "active"
            }
            standoff = {str(row) for row in state.get("standoff_provinces", []) if isinstance(row, str)}
            dislodged_from = str(unit.get("dislodged_from", "")) if unit.get("dislodged_from") else ""
            orders = []
            for destination in self._legal_move_destinations(unit_type=unit_type, province=province, coast=coast):
                destination_province = self._node_to_province(destination)
                if destination_province in occupied:
                    continue
                if destination_province in standoff:
                    continue
                if dislodged_from and destination_province == dislodged_from:
                    continue
                orders.append({"type": "RETREAT", "payload": {"to": destination}})
            return orders

        return []

    def _legal_support_orders(self, *, state: dict[str, Any], supporter: dict[str, Any]) -> list[dict[str, Any]]:
        supporter_type = str(supporter.get("type", ""))
        supporter_province = str(supporter.get("province", ""))
        supporter_coast = supporter.get("coast") if isinstance(supporter.get("coast"), str) else None
        support_destinations = set(
            self._legal_move_destinations(
                unit_type=supporter_type,
                province=supporter_province,
                coast=supporter_coast,
            )
        )
        if not support_destinations:
            return []
        support_provinces = {self._node_to_province(destination) for destination in support_destinations}
        active_units = sorted(
            [
                row
                for row in state.get("units", [])
                if isinstance(row, dict)
                and str(row.get("status", "active")) == "active"
                and isinstance(row.get("province"), str)
            ],
            key=lambda row: (str(row.get("province", "")), str(row.get("unit_id", ""))),
        )
        orders: list[dict[str, Any]] = []
        for target_unit in active_units:
            if str(target_unit.get("unit_id", "")) == str(supporter.get("unit_id", "")):
                continue
            target_province = str(target_unit.get("province", ""))
            if target_province in support_provinces:
                orders.append({"type": "SUPPORT_HOLD", "payload": {"target": target_province}})
            target_type = str(target_unit.get("type", ""))
            target_coast = target_unit.get("coast") if isinstance(target_unit.get("coast"), str) else None
            for destination in self._legal_move_destinations(
                unit_type=target_type,
                province=target_province,
                coast=target_coast,
            ):
                if destination in support_destinations:
                    orders.append(
                        {
                            "type": "SUPPORT_MOVE",
                            "payload": {"supported_from": target_province, "supported_to": destination},
                        }
                    )
        return orders

    def _legal_convoy_actions(self, *, state: dict[str, Any], unit: dict[str, Any]) -> list[dict[str, Any]]:
        unit_type = str(unit.get("type", ""))
        province = str(unit.get("province", ""))
        if unit_type == "A":
            return [
                {"type": "MOVE", "payload": {"to": destination, "via_convoy": True}}
                for destination in self._convoy_destinations_for_army(state=state, army_province=province)
            ]
        if unit_type != "F" or province not in self.convoy_passable_seas:
            return []

        actions: list[dict[str, Any]] = []
        for army in self._active_units(state=state, unit_type="A"):
            army_from = str(army.get("province", ""))
            for army_to in self._convoy_destinations_for_army(state=state, army_province=army_from):
                participating_fleets = self._convoy_participating_fleets(
                    army_from=army_from,
                    army_to=army_to,
                    fleet_provinces=set(self._convoy_fleet_positions(state=state)),
                )
                if province in participating_fleets:
                    actions.append(
                        {
                            "type": "CONVOY",
                            "payload": {"from": province, "army_from": army_from, "army_to": army_to},
                        }
                    )
        return actions

    def _convoy_destinations_for_army(self, *, state: dict[str, Any], army_province: str) -> list[str]:
        source = self.province_by_id.get(army_province)
        if source is None or source.province_type != "coastal":
            return []
        fleet_positions = set(self._convoy_fleet_positions(state=state))
        destinations: list[str] = []
        for destination, target in sorted(self.province_by_id.items()):
            if destination == army_province or target.province_type != "coastal":
                continue
            if self._find_convoy_route(
                army_from=army_province,
                army_to=destination,
                fleet_provinces=fleet_positions,
            ) is not None:
                destinations.append(destination)
        return destinations

    def _convoy_route_options(self, *, state: dict[str, Any], country: str) -> list[dict[str, Any]]:
        fleet_positions = self._convoy_fleet_positions(state=state)
        fleet_provinces = set(fleet_positions)
        options: list[dict[str, Any]] = []
        for army in self._active_units(state=state, unit_type="A"):
            army_from = str(army.get("province", ""))
            for army_to in self._convoy_destinations_for_army(state=state, army_province=army_from):
                route = self._find_convoy_route(
                    army_from=army_from,
                    army_to=army_to,
                    fleet_provinces=fleet_provinces,
                )
                if route is None:
                    continue
                route_fleets = [fleet_positions[province] for province in route]
                if str(army.get("owner", "")) != country and not any(
                    str(fleet.get("owner", "")) == country for fleet in route_fleets
                ):
                    continue
                options.append(
                    {
                        "army_unit_id": str(army.get("unit_id", "")),
                        "army_from": army_from,
                        "army_to": army_to,
                        "fleet_unit_ids": [str(fleet.get("unit_id", "")) for fleet in route_fleets],
                        "fleet_provinces": route,
                        "requires_other_powers": any(str(fleet.get("owner", "")) != country for fleet in route_fleets),
                    }
                )
        return options

    def _convoy_fleet_positions(self, *, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(fleet.get("province", "")): fleet
            for fleet in self._active_units(state=state, unit_type="F")
            if str(fleet.get("province", "")) in self.convoy_passable_seas
        }

    def _convoy_participating_fleets(
        self,
        *,
        army_from: str,
        army_to: str,
        fleet_provinces: set[str],
    ) -> set[str]:
        starts = {fleet for fleet in fleet_provinces if self._fleet_touches_province(fleet, army_from)}
        goals = {fleet for fleet in fleet_provinces if self._fleet_touches_province(fleet, army_to)}
        if not starts or not goals:
            return set()
        return self._reachable_convoy_fleets(starts=starts, fleet_provinces=fleet_provinces) & self._reachable_convoy_fleets(
            starts=goals,
            fleet_provinces=fleet_provinces,
        )

    def _find_convoy_route(
        self,
        *,
        army_from: str,
        army_to: str,
        fleet_provinces: set[str],
    ) -> list[str] | None:
        starts = sorted(fleet for fleet in fleet_provinces if self._fleet_touches_province(fleet, army_from))
        goals = {fleet for fleet in fleet_provinces if self._fleet_touches_province(fleet, army_to)}
        if not starts or not goals:
            return None
        queue: list[tuple[str, list[str]]] = [(start, [start]) for start in starts]
        visited = set(starts)
        while queue:
            node, path = queue.pop(0)
            if node in goals:
                return path
            for neighbor in sorted(self._fleet_adjacency(node) & fleet_provinces):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, [*path, neighbor]))
        return None

    def _reachable_convoy_fleets(self, *, starts: set[str], fleet_provinces: set[str]) -> set[str]:
        reachable = set(starts)
        queue = sorted(starts)
        while queue:
            node = queue.pop(0)
            for neighbor in sorted(self._fleet_adjacency(node) & fleet_provinces):
                if neighbor not in reachable:
                    reachable.add(neighbor)
                    queue.append(neighbor)
        return reachable

    @staticmethod
    def _active_units(*, state: dict[str, Any], unit_type: str) -> list[dict[str, Any]]:
        return sorted(
            [
                row
                for row in state.get("units", [])
                if isinstance(row, dict)
                and str(row.get("status", "active")) == "active"
                and str(row.get("type", "")) == unit_type
                and isinstance(row.get("province"), str)
            ],
            key=lambda row: (str(row.get("province", "")), str(row.get("unit_id", ""))),
        )

    def _adjustment_context(self, *, state: dict[str, Any], country: str) -> dict[str, Any]:
        if str(state.get("phase", "")) != "adjustment" or not country:
            return {"obligation": "not_adjustment", "delta": 0, "required_count": 0, "legal_options": []}

        active_units = [
            row
            for row in state.get("units", [])
            if isinstance(row, dict)
            and str(row.get("owner", "")) == country
            and str(row.get("status", "active")) == "active"
        ]
        owned_centers = [
            province_id
            for province_id, owner in (state.get("supply_ownership", {}) or {}).items()
            if owner == country
        ]
        delta = len(owned_centers) - len(active_units)
        if delta == 0:
            return {"obligation": "none", "delta": 0, "required_count": 0, "legal_options": []}
        if delta < 0:
            options = [
                {"type": "DISBAND", "payload": {"unit_ref": str(unit.get("unit_id", ""))}}
                for unit in sorted(active_units, key=lambda row: str(row.get("unit_id", "")))
                if isinstance(unit.get("unit_id"), str)
            ]
            return {
                "obligation": "disband",
                "delta": delta,
                "required_count": abs(delta),
                "legal_options": options,
            }

        occupied = {str(row.get("province", "")) for row in active_units}
        home_centers = sorted(
            [
                province.id
                for province in self.province_by_id.values()
                if province.home_center_owner == country
            ]
        )
        options: list[dict[str, Any]] = []
        for province_id in sorted(home_centers):
            if province_id not in owned_centers:
                continue
            if province_id in occupied:
                continue
            province = self.province_by_id.get(province_id)
            if province is None:
                continue
            if "A" in province.occupiable_by:
                options.append(
                    {"type": "BUILD", "payload": {"unit_type": "A", "province": province_id, "coast": None}}
                )
            if "F" in province.occupiable_by:
                if province.coasts:
                    for coast_id in sorted(province.coasts):
                        options.append(
                            {
                                "type": "BUILD",
                                "payload": {"unit_type": "F", "province": province_id, "coast": coast_id},
                            }
                        )
                else:
                    options.append(
                        {"type": "BUILD", "payload": {"unit_type": "F", "province": province_id, "coast": None}}
                    )
        return {"obligation": "build", "delta": delta, "required_count": delta, "legal_options": options}

    def _legal_move_destinations(self, *, unit_type: str, province: str, coast: str | None) -> list[str]:
        if province not in self.province_by_id:
            return []
        if unit_type == "A":
            return sorted(set(self.province_by_id[province].army_adjacency))
        origin_node = coast if coast and coast in self.coast_by_id else province
        adjacency = self._fleet_adjacency(origin_node)
        return sorted(adjacency)

    def _fleet_adjacency(self, node_id: str) -> set[str]:
        if node_id in self.coast_by_id:
            return set(self.coast_by_id[node_id].fleet_adjacency)
        province = self.province_by_id.get(node_id)
        if province is None:
            return set()
        return set(province.fleet_adjacency)

    def _fleet_touches_province(self, fleet_node: str, province_id: str) -> bool:
        adjacency = self._fleet_adjacency(fleet_node)
        if province_id in adjacency:
            return True
        province = self.province_by_id.get(province_id)
        return province is not None and any(coast_id in adjacency for coast_id in province.coasts)

    def _node_to_province(self, node_id: str) -> str:
        if node_id in self.coast_by_id:
            return self.coast_by_id[node_id].province_id
        return node_id

    def _order_signature(self, order_type: str, payload: dict[str, Any]) -> str:
        payload_obj = payload if isinstance(payload, dict) else {}
        return f"{order_type.upper()}::{json.dumps(payload_obj, sort_keys=True)}"
