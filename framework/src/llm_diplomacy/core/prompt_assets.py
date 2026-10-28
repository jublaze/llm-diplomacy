from __future__ import annotations

from pathlib import Path
import hashlib

DEFAULT_STRATEGIC_IMPERATIVES = (
    "strategic_imperatives_version: strategic_imperatives_v2_1_campaign_oriented\n"
    "Primary objective: solo victory at 18 centers. A draw is preferable to surviving without sharing in the draw; survival preserves agency but is not success by itself. "
    "Send messages when they advance a current campaign, test an important claim, clarify consequential uncertainty, or shape a strategically relevant relationship. "
    "Evaluate borders and relationships by the campaign options they enable; Souls define style, not objective."
)

DEFAULT_VICTORY_CAMPAIGN_DOCTRINE = (
    "victory_campaign_doctrine_version: victory_campaign_doctrine_v1_5_initiative_test\n"
    "A credible solo route needs additional centers. Maintain a compact, revisable campaign hypothesis: expansion route or bottleneck, objectives, enabling or obstructing powers, next phases, and revising evidence. "
    "A plan may be uncertain, conditional, or abandoned. Express the hypothesis in concise prose appropriate to the current position; do not invent certainty, detail, an alliance, or an attack plan unsupported by the board and available evidence. At each strategic reassessment, identify which powers are gaining, weakening, or becoming constrained most quickly. Decide whether those trajectories materially affect your own campaign; if they do, state the opportunity, risk, or possible counterweight you will consider. Do not assume that every imbalance requires the same response."
    "\n\nDo not mistake a quiet border, a surviving alliance, or the absence of immediate danger for strategic progress. Your campaign plan should explain how your current position can plausibly produce relative growth. If it cannot, identify a self-interested diplomatic, coalition, or tactical initiative that could change the opportunity set. State what it could gain, what it risks, which other powers’ incentives it depends on, and what new evidence would make you pursue, defer, revise, or abandon it."
    "\n\n## Campaign Execution Loop\n\n"
    "A campaign is not only a desired direction. When the board creates an imbalance, vulnerability, or opening, identify a plausible theater, the positional or center objective, and the power whose position must weaken for progress to occur.\n\n"
    "Use temporary cooperation when it creates a mutually intelligible gain: identify the shared problem, what each participant can gain or protect, the broad tactical effect sought, and the conditions under which the arrangement ends. You may communicate a capability or conditional offer without disclosing every final order.\n\n"
    "Convert an opening into durable position. Entering a province in Spring is not ownership; plan how to hold it through Fall, bank the center, and protect the resulting position.\n\n"
    "Treat adjudication as the test of a campaign and its partners. After a promised or expected action, distinguish what was said, what was ordered, and what happened. Continue, reprice, narrow, or abandon the arrangement based on the board result.\n\n"
    "When your campaign is failing or your position is deteriorating, re-evaluate the whole board—including which powers may gain from checking your opponents—and pursue a coherent revised plan for survival and renewed leverage rather than repeating a defeated line.\n\n"
    "Do not repeat a failed route by default. If a planned gain repeatedly fails, explain why the next attempt is materially different; otherwise choose another target, partner, or tactical approach."
)

DEFAULT_DIPLOMACY_PRIMER = (
    "rules_reference_version: diplomacy_rules_reference_v5_convoyed_army_support_public_orders\n"
    "Diplomacy uses secret simultaneous orders. The engine-generated legal actions and authoritative board state "
    "override this reference. A unit may support another power's or its own unit's legal hold or legal move "
    "when board geometry permits; valid support strength combines across powers and support affects adjudication "
    "subject to legality and support-cut rules. Powers may request, offer, or condition support during negotiation, "
    "but those proposals are non-binding communication and require matching legal orders. "
    "A support order is legal only when the supporting unit could itself legally move into the supported destination. For support-to-hold, that destination is the unit’s current province. For support-to-move, it is the moving unit’s destination. Armies may support only into adjacent land provinces; fleets may support only into adjacent sea or coastal provinces. A fleet cannot support into an inland province, even if it is adjacent to the moving unit’s origin. For example, `F Alb S A Bul–Ser` is illegal because Serbia is inland and a fleet in Albania cannot enter it. "
    "When supporting an army that moves by convoy, name the convoyed army’s original province and destination—not a convoying fleet’s province. For example, to support `A Yor–Bel` via `F Nth`, use `A Hol S A Yor–Bel`; `A Hol S F Nth–Bel` supports a different move and does not aid the convoy. "
    "Only armies may be convoyed, and retreats and adjustments use the engine-listed legal actions."
)

DIPLOMACY_RULES_REFERENCE_VERSION = "diplomacy_rules_reference_v5_convoyed_army_support_public_orders"

OUTCOME_REVIEW_VERSION = "outcome_review_v1_public_adjudication"
OUTCOME_REVIEW_INSTRUCTION = (
    "Review the immediately preceding adjudication. Compare your important expected effects and negotiated commitments with the now-public orders and outcomes. State what you expected, what happened, the best evidence-supported reason, and how this changes your next action. Explicitly flag any mismatch between your intended plan and your own submitted order. Distinguish a demonstrated cause from an inference or unresolved uncertainty. If the phase went as expected, say so concisely."
)
OUTCOME_REVIEW_SHA256 = f"sha256:{hashlib.sha256(OUTCOME_REVIEW_INSTRUCTION.encode('utf-8')).hexdigest()}"


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def canonical_strategic_imperatives_path() -> Path:
    return project_root() / "strategic_imperatives.md"


def canonical_victory_campaign_doctrine_path() -> Path:
    return project_root() / "victory_campaign_doctrine.md"


def canonical_diplomacy_primer_path() -> Path:
    return project_root() / "diplomacy_primer.md"


def read_prompt_asset(path: str | Path, *, fallback: str) -> str:
    asset_path = Path(path)
    if asset_path.exists():
        return asset_path.read_text(encoding="utf-8").strip()
    return fallback
