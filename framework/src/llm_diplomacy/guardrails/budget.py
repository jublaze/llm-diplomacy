from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BudgetConfig:
    max_provider_calls: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_estimated_cost_usd: float | None = None
    safe_reserve_usd_per_call: float | None = None
    stop_policy: str = "stop_experiment"

    def __post_init__(self) -> None:
        for name in ("max_estimated_cost_usd", "safe_reserve_usd_per_call"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0
                                      or (name == "safe_reserve_usd_per_call" and value == 0)):
                raise ValueError(f"{name} must be a finite positive number when provided.")
        for name in ("max_provider_calls", "max_input_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer.")


@dataclass(frozen=True)
class BudgetDecision:
    allow_provider_call: bool
    reason: str | None = None
    stop_policy: str | None = None


class BudgetController:
    """Tracks optional experiment-level provider budgets and emits append-only events."""

    def __init__(
        self,
        *,
        schema_version: str,
        experiment_id: str,
        artifact_root: str | Path,
        config: BudgetConfig | None = None,
    ) -> None:
        self.schema_version = schema_version
        self.experiment_id = experiment_id
        self.config = config
        self.root = Path(artifact_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "budget_events.jsonl"
        self.state_path = self.root / "budget_state.json"
        self.known_input_tokens = 0
        self.known_output_tokens = 0
        self.known_estimated_cost_usd = 0.0
        self.total_provider_calls = 0
        self.total_input_tokens: int | None = 0
        self.total_output_tokens: int | None = 0
        self.total_estimated_cost_usd: float | None = 0.0
        self.stop_experiment = False
        self.stop_game_after_phase = False
        self.stop_reason: str | None = None
        self.stop_game_id: str | None = None
        self.force_fallback = False
        self.pending_reservations: dict[str, dict[str, Any]] = {}
        self.pending_input_tokens = 0
        self.pending_output_tokens = 0
        self.pending_cost_usd = 0.0
        self._config_fingerprint = json.dumps({
            "max_provider_calls": config.max_provider_calls if config else None,
            "max_input_tokens": config.max_input_tokens if config else None,
            "max_output_tokens": config.max_output_tokens if config else None,
            "max_estimated_cost_usd": config.max_estimated_cost_usd if config else None,
            "safe_reserve_usd_per_call": config.safe_reserve_usd_per_call if config else None,
            "stop_policy": config.stop_policy if config else None,
        }, sort_keys=True)
        self._restore_state()
        self._flush_state()

    @classmethod
    def from_raw(
        cls,
        *,
        schema_version: str,
        experiment_id: str,
        artifact_root: str | Path,
        raw: Any,
    ) -> BudgetController | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("'budget' must be an object when provided.")
        integer_limits = ("max_provider_calls", "max_input_tokens", "max_output_tokens")
        numeric_limits = ("max_estimated_cost_usd", "safe_reserve_usd_per_call")
        for name in integer_limits:
            value = raw.get(name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"budget.{name} must be a non-negative integer.")
        for name in numeric_limits:
            value = raw.get(name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ValueError(f"budget.{name} must be a finite number.")
        if not isinstance(raw.get("stop_policy", "stop_experiment"), str):
            raise ValueError("budget.stop_policy must be a string.")
        cfg = BudgetConfig(
            max_provider_calls=raw.get("max_provider_calls"),
            max_input_tokens=raw.get("max_input_tokens"),
            max_output_tokens=raw.get("max_output_tokens"),
            max_estimated_cost_usd=raw.get("max_estimated_cost_usd"),
            safe_reserve_usd_per_call=raw.get("safe_reserve_usd_per_call"),
            stop_policy=raw.get("stop_policy", "stop_experiment").strip().lower(),
        )
        if cfg.stop_policy not in {"stop_experiment", "stop_game_after_phase", "switch_to_fallback"}:
            raise ValueError(f"Unsupported budget stop_policy: {cfg.stop_policy}")
        return cls(
            schema_version=schema_version,
            experiment_id=experiment_id,
            artifact_root=artifact_root,
            config=cfg,
        )

    def check_before_call(self, *, game_id: str, phase_id: str, agent_id: str, method: str) -> BudgetDecision:
        if self.config is None:
            return BudgetDecision(allow_provider_call=True)
        reason = self._exceeded_reason()
        if reason is None:
            self._append_event(
                "budget_check",
                {
                    "game_id": game_id,
                    "phase_id": phase_id,
                    "agent_id": agent_id,
                    "method": method,
                    "allow_provider_call": True,
                },
            )
            return BudgetDecision(allow_provider_call=True)
        self._apply_stop_policy(reason=reason, game_id=game_id)
        self._append_event(
            "budget_exceeded",
            {
                "game_id": game_id,
                "phase_id": phase_id,
                "agent_id": agent_id,
                "method": method,
                "reason": reason,
                "stop_policy": self.config.stop_policy,
            },
        )
        self._flush_state()
        return BudgetDecision(allow_provider_call=False, reason=reason, stop_policy=self.config.stop_policy)

    def reserve_before_call(self, *, reservation_id: str, game_id: str, phase_id: str, agent_id: str,
                            method: str, input_tokens: int, output_tokens: int, cost_usd: float | None) -> BudgetDecision:
        if not reservation_id or input_tokens < 0 or output_tokens < 0:
            raise ValueError("Provider reservation bounds must be non-negative and identified.")
        if cost_usd is not None and (not math.isfinite(float(cost_usd)) or cost_usd < 0):
            raise ValueError("Provider reservation cost must be finite and non-negative.")
        if reservation_id in self.pending_reservations:
            raise ValueError("Duplicate provider reservation id.")
        if self.config is None:
            self._reserve(reservation_id, game_id, phase_id, agent_id, method, input_tokens, output_tokens, cost_usd)
            return BudgetDecision(allow_provider_call=True)
        reason = self._exceeded_reason(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd)
        if reason:
            self._apply_stop_policy(reason=reason, game_id=game_id)
            self._append_event("budget_exceeded", {"game_id": game_id, "phase_id": phase_id, "agent_id": agent_id,
                "method": method, "reason": reason, "stop_policy": self.config.stop_policy})
            self._flush_state()
            return BudgetDecision(False, reason, self.config.stop_policy)
        self._reserve(reservation_id, game_id, phase_id, agent_id, method, input_tokens, output_tokens, cost_usd)
        return BudgetDecision(True)

    def settle_reservation(self, *, reservation_id: str, usage: dict[str, Any] | None, uncertain: bool = False) -> None:
        reservation = self.pending_reservations.get(reservation_id)
        if reservation is None:
            return
        if uncertain:
            reservation["status"] = "uncertain"
            self.total_input_tokens = None
            self.total_output_tokens = None
            self.total_estimated_cost_usd = None
            self._append_event("provider_reservation_uncertain", {"reservation_id": reservation_id})
        else:
            cfg = self.config
            usage_row, malformed = self._validated_usage(usage)
            unresolved = malformed or (cfg is not None and (
                (cfg.max_input_tokens is not None and not self._valid_int(usage_row.get("prompt_tokens")))
                or (cfg.max_output_tokens is not None and not self._valid_int(usage_row.get("completion_tokens")))
                or (cfg.max_estimated_cost_usd is not None and not self._valid_number(usage_row.get("estimated_cost_usd")))
            ))
            if unresolved:
                self.record_after_call(game_id=reservation["game_id"], phase_id=reservation["phase_id"],
                    agent_id=reservation["agent_id"], method=reservation["method"], usage=usage_row, count_call=False)
                reservation["status"] = "uncertain"
                self._flush_state()
                return
            self.pending_reservations.pop(reservation_id)
            self.pending_input_tokens -= int(reservation["input_tokens"])
            self.pending_output_tokens -= int(reservation["output_tokens"])
            self.pending_cost_usd -= float(reservation["cost_usd"] or 0.0)
            self.record_after_call(game_id=reservation["game_id"], phase_id=reservation["phase_id"],
                agent_id=reservation["agent_id"], method=reservation["method"], usage=usage, count_call=False)
            return
        self._flush_state()

    def record_after_call(
        self,
        *,
        game_id: str,
        phase_id: str,
        agent_id: str,
        method: str,
        usage: dict[str, Any] | None,
        count_call: bool = True,
    ) -> None:
        usage, malformed = self._validated_usage(usage)
        if count_call:
            self.total_provider_calls += 1
        if malformed:
            self.total_input_tokens = None
            self.total_output_tokens = None
            self.total_estimated_cost_usd = None
            usage = {}
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        estimated_cost = usage.get("estimated_cost_usd") if isinstance(usage, dict) else None
        if self._valid_int(prompt_tokens):
            self.known_input_tokens += prompt_tokens
            if self.total_input_tokens is not None:
                self.total_input_tokens += prompt_tokens
        else:
            self.total_input_tokens = None
        if self._valid_int(completion_tokens):
            self.known_output_tokens += completion_tokens
            if self.total_output_tokens is not None:
                self.total_output_tokens += completion_tokens
        else:
            self.total_output_tokens = None
        if self._valid_number(estimated_cost):
            self.known_estimated_cost_usd += float(estimated_cost)
            if self.total_estimated_cost_usd is not None:
                self.total_estimated_cost_usd += float(estimated_cost)
        else:
            self.total_estimated_cost_usd = None
        self._append_event(
            "budget_usage_update",
            {
                "game_id": game_id,
                "phase_id": phase_id,
                "agent_id": agent_id,
                "method": method,
                "usage": usage if isinstance(usage, dict) else {},
                "totals": self.status(),
            },
        )
        self._flush_state()

    def record_provider_error(
        self,
        *,
        game_id: str,
        phase_id: str,
        agent_id: str,
        method: str,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.total_provider_calls += 1
        self.total_input_tokens = None
        self.total_output_tokens = None
        self.total_estimated_cost_usd = None
        self._append_event(
            "provider_error",
            {
                "game_id": game_id,
                "phase_id": phase_id,
                "agent_id": agent_id,
                "method": method,
                "error": error,
                "provider_error": metadata if isinstance(metadata, dict) else {},
            },
        )
        self._flush_state()

    def consume_stop_game_after_phase(self, *, game_id: str) -> bool:
        if self.stop_game_after_phase and self.stop_game_id == game_id:
            self.stop_game_after_phase = False
            self._flush_state()
            return True
        return False

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "max_provider_calls": self.config.max_provider_calls if self.config else None,
            "max_input_tokens": self.config.max_input_tokens if self.config else None,
            "max_output_tokens": self.config.max_output_tokens if self.config else None,
            "max_estimated_cost_usd": self.config.max_estimated_cost_usd if self.config else None,
            "stop_policy": self.config.stop_policy if self.config else None,
            "known_input_tokens": self.known_input_tokens,
            "known_output_tokens": self.known_output_tokens,
            "known_estimated_cost_usd": self.known_estimated_cost_usd,
            "total_provider_calls": self.total_provider_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_estimated_cost_usd": self.total_estimated_cost_usd,
            "stop_experiment": self.stop_experiment,
            "stop_game_after_phase": self.stop_game_after_phase,
            "stop_reason": self.stop_reason,
            "stop_game_id": self.stop_game_id,
            "force_fallback": self.force_fallback,
            "pending_reservations": self.pending_reservations,
            "pending_input_tokens": self.pending_input_tokens,
            "pending_output_tokens": self.pending_output_tokens,
            "pending_cost_usd": self.pending_cost_usd,
            "budget_admission_estimated_input_tokens": self.known_input_tokens + self.pending_input_tokens,
            "budget_admission_estimated_output_tokens": self.known_output_tokens + self.pending_output_tokens,
            "budget_admission_estimated_cost_usd": self.known_estimated_cost_usd + self.pending_cost_usd,
        }

    def _exceeded_reason(self, *, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float | None = 0.0) -> str | None:
        cfg = self.config
        if cfg is None:
            return None
        if cfg.max_provider_calls is not None and self.total_provider_calls >= cfg.max_provider_calls:
            return "max_provider_calls"
        if cfg.max_input_tokens is not None and (self.known_input_tokens + self.pending_input_tokens >= cfg.max_input_tokens or self.known_input_tokens + self.pending_input_tokens + input_tokens > cfg.max_input_tokens):
            return "max_input_tokens"
        if cfg.max_output_tokens is not None and (self.known_output_tokens + self.pending_output_tokens >= cfg.max_output_tokens or self.known_output_tokens + self.pending_output_tokens + output_tokens > cfg.max_output_tokens):
            return "max_output_tokens"
        if cfg.max_estimated_cost_usd is not None and (
            cost_usd is None
            or (self.total_estimated_cost_usd is None and not self.pending_reservations)
            or self.known_estimated_cost_usd + self.pending_cost_usd + cost_usd > cfg.max_estimated_cost_usd
        ):
            return "max_estimated_cost_usd"
        return None

    def _reserve(self, reservation_id: str, game_id: str, phase_id: str, agent_id: str, method: str,
                 input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
        row = {"reservation_id": reservation_id, "game_id": game_id, "phase_id": phase_id,
               "agent_id": agent_id, "method": method, "input_tokens": input_tokens,
               "output_tokens": output_tokens, "cost_usd": cost_usd, "status": "pending"}
        self.pending_reservations[reservation_id] = row
        self.total_provider_calls += 1
        self.pending_input_tokens += input_tokens
        self.pending_output_tokens += output_tokens
        if cost_usd is not None:
            self.pending_cost_usd += cost_usd
        self._append_event("provider_reservation", row)
        self._flush_state()

    def _restore_state(self) -> None:
        if not self.state_path.exists():
            return
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("experiment_id") != self.experiment_id or state.get("schema_version") != self.schema_version:
            raise ValueError("Existing budget state belongs to a different experiment or schema.")
        if state.get("config_fingerprint") != self._config_fingerprint:
            raise ValueError("Existing budget state has a different budget configuration.")
        required_state_keys = {
            "max_provider_calls", "max_input_tokens", "max_output_tokens", "max_estimated_cost_usd", "stop_policy",
            "known_input_tokens", "known_output_tokens", "known_estimated_cost_usd", "total_provider_calls",
            "total_input_tokens", "total_output_tokens", "total_estimated_cost_usd", "stop_experiment",
            "stop_game_after_phase", "stop_reason", "stop_game_id", "force_fallback", "pending_reservations",
            "pending_input_tokens", "pending_output_tokens", "pending_cost_usd",
            "budget_admission_estimated_input_tokens", "budget_admission_estimated_output_tokens",
            "budget_admission_estimated_cost_usd",
        }
        if not required_state_keys <= state.keys():
            raise ValueError("Existing budget state is incomplete.")
        expected_limits = {
            "max_provider_calls": self.config.max_provider_calls if self.config else None,
            "max_input_tokens": self.config.max_input_tokens if self.config else None,
            "max_output_tokens": self.config.max_output_tokens if self.config else None,
            "max_estimated_cost_usd": self.config.max_estimated_cost_usd if self.config else None,
            "stop_policy": self.config.stop_policy if self.config else None,
        }
        if any(state.get(key) != value for key, value in expected_limits.items()):
            raise ValueError("Existing budget state limits do not match its configuration.")
        if (any(state[key] is not None and not self._valid_int(state[key])
                for key in ("max_provider_calls", "max_input_tokens", "max_output_tokens"))
                or (state["max_estimated_cost_usd"] is not None and not self._valid_number(state["max_estimated_cost_usd"]))
                or (state["stop_policy"] is not None and not isinstance(state["stop_policy"], str))):
            raise ValueError("Existing budget state has invalid configured limits.")
        required_ints = ("known_input_tokens", "known_output_tokens", "total_provider_calls", "pending_input_tokens", "pending_output_tokens")
        required_numbers = ("known_estimated_cost_usd", "pending_cost_usd")
        required_nullable = ("total_input_tokens", "total_output_tokens", "total_estimated_cost_usd")
        required_bools = ("stop_experiment", "stop_game_after_phase", "force_fallback")
        if any(not self._valid_int(state.get(key)) for key in required_ints):
            raise ValueError("Existing budget state has missing or invalid integer accounting fields.")
        if any(not self._valid_number(state.get(key)) for key in required_numbers):
            raise ValueError("Existing budget state has missing or invalid numeric accounting fields.")
        if (any(state.get(key) is not None and not self._valid_int(state[key]) for key in ("total_input_tokens", "total_output_tokens"))
                or (state.get("total_estimated_cost_usd") is not None and not self._valid_number(state["total_estimated_cost_usd"]))):
            raise ValueError("Existing budget state has invalid total fields.")
        if any(not isinstance(state.get(key), bool) for key in required_bools):
            raise ValueError("Existing budget state has missing or invalid policy flags.")
        if not isinstance(state.get("pending_reservations"), dict):
            raise ValueError("Existing budget state has invalid reservations.")
        pending_input = pending_output = 0
        pending_cost = 0.0
        for reservation_id, row in state["pending_reservations"].items():
            if not isinstance(reservation_id, str) or not reservation_id or not isinstance(row, dict):
                raise ValueError("Existing budget state has an invalid reservation row.")
            if row.get("reservation_id") != reservation_id or row.get("status") not in {"pending", "uncertain"}:
                raise ValueError("Existing budget state has an inconsistent reservation identity or status.")
            if any(not isinstance(row.get(key), str) or not row[key] for key in ("game_id", "phase_id", "agent_id", "method")):
                raise ValueError("Existing budget state has invalid reservation context.")
            if not self._valid_int(row.get("input_tokens")) or not self._valid_int(row.get("output_tokens")):
                raise ValueError("Existing budget state has invalid reservation token bounds.")
            row_cost = row.get("cost_usd")
            if row_cost is not None and not self._valid_number(row_cost):
                raise ValueError("Existing budget state has invalid reservation cost.")
            pending_input += row["input_tokens"]
            pending_output += row["output_tokens"]
            pending_cost += float(row_cost or 0.0)
        if (pending_input != state["pending_input_tokens"] or pending_output != state["pending_output_tokens"]
                or not math.isclose(pending_cost, float(state["pending_cost_usd"]), rel_tol=1e-12, abs_tol=1e-12)
                or len(state["pending_reservations"]) > state["total_provider_calls"]):
            raise ValueError("Existing budget state has inconsistent reservation aggregates.")
        expected_admission = {
            "budget_admission_estimated_input_tokens": state["known_input_tokens"] + pending_input,
            "budget_admission_estimated_output_tokens": state["known_output_tokens"] + pending_output,
            "budget_admission_estimated_cost_usd": float(state["known_estimated_cost_usd"]) + pending_cost,
        }
        if (state.get("budget_admission_estimated_input_tokens") != expected_admission["budget_admission_estimated_input_tokens"]
                or state.get("budget_admission_estimated_output_tokens") != expected_admission["budget_admission_estimated_output_tokens"]
                or not self._valid_number(state.get("budget_admission_estimated_cost_usd"))
                or not math.isclose(float(state["budget_admission_estimated_cost_usd"]), expected_admission["budget_admission_estimated_cost_usd"], rel_tol=1e-12, abs_tol=1e-12)):
            raise ValueError("Existing budget state has inconsistent admission totals.")
        if (not self._valid_int(state["budget_admission_estimated_input_tokens"])
                or not self._valid_int(state["budget_admission_estimated_output_tokens"])):
            raise ValueError("Existing budget state has invalid token admission totals.")
        for total_key, known in (("total_input_tokens", state["known_input_tokens"]),
                                 ("total_output_tokens", state["known_output_tokens"]),
                                 ("total_estimated_cost_usd", state["known_estimated_cost_usd"])):
            total = state[total_key]
            if total is not None and total < known:
                raise ValueError("Existing budget state totals are below known usage.")
        if state.get("stop_reason") is not None and not isinstance(state.get("stop_reason"), str):
            raise ValueError("Existing budget state has an invalid stop reason.")
        if state.get("stop_game_id") is not None and not isinstance(state.get("stop_game_id"), str):
            raise ValueError("Existing budget state has an invalid stop game id.")
        self.known_input_tokens = state["known_input_tokens"]
        self.known_output_tokens = state["known_output_tokens"]
        self.total_provider_calls = state["total_provider_calls"]
        self.pending_input_tokens = state["pending_input_tokens"]
        self.pending_output_tokens = state["pending_output_tokens"]
        self.known_estimated_cost_usd = float(state["known_estimated_cost_usd"])
        self.pending_cost_usd = float(state["pending_cost_usd"])
        self.total_input_tokens, self.total_output_tokens, self.total_estimated_cost_usd = (state[key] for key in required_nullable)
        self.stop_experiment = state["stop_experiment"]
        self.stop_game_after_phase = state["stop_game_after_phase"]
        self.stop_reason = state.get("stop_reason")
        self.stop_game_id = state.get("stop_game_id")
        self.force_fallback = state["force_fallback"]
        self.pending_reservations = state["pending_reservations"]

    @staticmethod
    def _valid_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _valid_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    @classmethod
    def _validated_usage(cls, usage: Any) -> tuple[dict[str, Any], bool]:
        if not isinstance(usage, dict):
            return {}, usage is not None
        clean = dict(usage)
        malformed = False
        integer_fields = ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens",
                          "total_tokens", "cached_input_tokens", "reasoning_tokens")
        for key in integer_fields:
            if key in clean and clean[key] is not None and not cls._valid_int(clean[key]):
                clean[key] = None
                malformed = True
        for left, right in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            if cls._valid_int(clean.get(left)) and cls._valid_int(clean.get(right)) and clean[left] != clean[right]:
                clean[left] = clean[right] = None
                malformed = True
            elif not cls._valid_int(clean.get(left)) and cls._valid_int(clean.get(right)):
                clean[left] = clean[right]
            elif cls._valid_int(clean.get(left)) and not cls._valid_int(clean.get(right)):
                clean[right] = clean[left]
        if "estimated_cost_usd" in clean and clean["estimated_cost_usd"] is not None and not cls._valid_number(clean["estimated_cost_usd"]):
            clean["estimated_cost_usd"] = None
            malformed = True
        prompt, completion, total = clean.get("prompt_tokens"), clean.get("completion_tokens"), clean.get("total_tokens")
        if cls._valid_int(prompt) and cls._valid_int(completion) and cls._valid_int(total) and total != prompt + completion:
            clean["total_tokens"] = None
            malformed = True
        return clean, malformed

    def _apply_stop_policy(self, *, reason: str, game_id: str) -> None:
        assert self.config is not None
        self.stop_reason = reason
        policy = self.config.stop_policy
        if policy == "switch_to_fallback":
            self.force_fallback = True
            return
        if policy == "stop_game_after_phase":
            self.force_fallback = True
            self.stop_game_after_phase = True
            self.stop_game_id = game_id
            return
        self.force_fallback = True
        self.stop_experiment = True

    def _append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "schema_version": self.schema_version,
            "event_type": event_type,
            "experiment_id": self.experiment_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")

    def _flush_state(self) -> None:
        payload = self.status()
        payload["config_fingerprint"] = self._config_fingerprint
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)
