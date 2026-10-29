"""Replay selection primitive; installation requires a separately validated durable transition.

No production code installs this selector yet.  Source compatibility, receipt
validation, activation and crash recovery must be implemented before doing so.
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplaySelection:
    legacy_prompt: bool
    legacy_validation: bool
    amended_boundary_validation: bool = False
    boundary: bool = False


class BuildRuleReplaySelector:
    def __init__(self, *, historical_attempts: dict[str, tuple[tuple[str, str], ...]],
                 boundary_decision: str, boundary_attempt: str, boundary_prompt: str,
                 amend_boundary: bool, activation_sequence: int | None = None,
                 adoption_proof: dict[str, Any] | None = None) -> None:
        if not historical_attempts or boundary_decision not in historical_attempts:
            raise ValueError("boundary must belong to the historical journal")
        if (boundary_attempt, boundary_prompt) not in historical_attempts[boundary_decision]:
            raise ValueError("boundary attempt is absent from historical journal")
        if historical_attempts[boundary_decision][-1] != (boundary_attempt, boundary_prompt):
            raise ValueError("boundary must be the latest received response")
        if amend_boundary and not boundary_decision.endswith("/submit_orders"):
            raise ValueError("amended boundary requires an order decision")
        self._historical = {key: tuple(rows) for key, rows in historical_attempts.items()}
        self._boundary = (boundary_decision, boundary_attempt, boundary_prompt)
        self._amend_boundary = amend_boundary
        self._positions: dict[str, int] = {}
        self._activation_sequence = activation_sequence
        self._adoption_proof = adoption_proof
        self._boundary_crossed = False

    def select(self, decision_id: str) -> ReplaySelection:
        if self._boundary_crossed:
            if decision_id in self._historical:
                raise ValueError("historical decision cannot be regenerated after activation")
            return ReplaySelection(False, False)
        if decision_id in self._historical:
            return ReplaySelection(True, True)
        raise ValueError("new decision requires durable build rule activation")

    def cross_boundary(self, *, selection: ReplaySelection, decision_id: str, cached: dict[str, Any],
                       context_sha256: str, response_hash: str, valid: bool) -> None:
        proof = self._adoption_proof
        if (self._activation_sequence is None or not selection.boundary or not isinstance(proof, dict)
                or not valid or proof.get("decision_id") != decision_id
                or proof.get("attempt_id") != cached.get("attempt_id")
                or proof.get("prompt_hash") != cached.get("prompt_hash")
                or proof.get("context_sha256") != context_sha256 or proof.get("response_hash") != response_hash
                or proof.get("valid") is not True):
            raise RuntimeError("build rule boundary requires durable adoption and activation")
        self._boundary_crossed = True

    def cached_new_response(self, recovery: Any, *, decision_id: str, prompt_hash: str) -> dict[str, Any] | None:
        if not self._boundary_crossed or self._activation_sequence is None:
            raise ValueError("new decision requires crossed activation boundary")
        rows = recovery._journal_rows()
        skipped = {row.get("attempt_id") for row in rows if row.get("kind") == "response_skipped"
                   and row.get("sequence", -1) > self._activation_sequence}
        matches = [row for row in rows if row.get("kind") == "attempt" and row.get("outcome") in {"received", "blocked"}
                   and row.get("sequence", -1) > self._activation_sequence and row.get("decision_id") == decision_id
                   and row.get("prompt_hash") == prompt_hash and row.get("attempt_id") not in skipped]
        return matches[-1] if matches else None

    def cached_response(self, recovery: Any, *, decision_id: str, prompt_hash: str) -> dict[str, Any]:
        attempts = self._historical[decision_id]
        position = self._positions.get(decision_id, 0)
        if position >= len(attempts):
            raise ValueError("historical decision has no remaining recorded response")
        attempt_id, expected_prompt = attempts[position]
        if prompt_hash != expected_prompt:
            raise ValueError("historical prompt differs from next recorded attempt")
        matches = [row for row in recovery._journal_rows() if row.get("kind") == "attempt"
                   and row.get("decision_id") == decision_id and row.get("attempt_id") == attempt_id
                   and row.get("prompt_hash") == prompt_hash and row.get("outcome") == "received"]
        if len(matches) != 1:
            raise ValueError("expected historical response is missing or ambiguous")
        return matches[0]

    def select_attempt(self, *, decision_id: str, prompt_hash: str,
                       cached: dict[str, Any] | None) -> ReplaySelection:
        if cached is None or cached.get("outcome") != "received":
            raise ValueError("historical decision requires its received cached response")
        attempt = (cached.get("attempt_id"), prompt_hash)
        if cached.get("decision_id") != decision_id or cached.get("prompt_hash") != prompt_hash:
            raise ValueError("cached response identity differs from the historical prompt")
        position = self._positions.get(decision_id, 0)
        attempts = self._historical[decision_id]
        if position >= len(attempts) or attempt != attempts[position]:
            raise ValueError("cached response is not the next historical attempt")
        boundary = (decision_id, *attempt) == self._boundary
        amended = boundary and self._amend_boundary
        return ReplaySelection(True, not amended, amended, boundary)

    def advance_historical(self, *, decision_id: str, selection: ReplaySelection) -> None:
        if selection.boundary:
            raise ValueError("boundary requires durable adoption")
        self._positions[decision_id] = self._positions.get(decision_id, 0) + 1
