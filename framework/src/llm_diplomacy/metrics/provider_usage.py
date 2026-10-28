from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


class ProviderUsageTracker:
    """One record per attempted request, separate from lifecycle and blocked calls."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        ledger = root / "provider_attempts.jsonl"
        self.rows: list[dict[str, Any]] = []
        if ledger.exists():
            with ledger.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Provider usage ledger is corrupt at line {line_number}.") from exc
                    if not isinstance(row, dict) or row.get("kind") not in {"request", "blocked", "lifecycle", "eliminated_skip"}:
                        raise ValueError(f"Provider usage ledger has an invalid row at line {line_number}.")
                    self.rows.append(row)
        self.eliminated: set[tuple[str, str]] = set()

    def append(self, row: dict[str, Any]) -> None:
        row = {"schema_version": "1.0", **row}
        lifecycle_id = row.get("lifecycle_id")
        if row.get("kind") == "lifecycle" and isinstance(lifecycle_id, str) and any(
            existing.get("kind") == "lifecycle" and existing.get("lifecycle_id") == lifecycle_id
            for existing in self.rows
        ):
            return
        row["post_elimination_cleanup"] = (
            row.get("method") == "game_end_cleanup"
            and (row.get("game_id"), row.get("agent_id")) in self.eliminated
        )
        self.rows.append(row)
        with (self.root / "provider_attempts.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def record(self, request: Any, *, response: Any = None, error: Exception | None = None,
               blocked: bool = False, provider: dict[str, Any] | None = None) -> None:
        raw = response.raw if response is not None and isinstance(response.raw, dict) else {}
        usage = raw.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        resolved_model = raw.get("resolved_model", response.model if response is not None else None)
        if not isinstance(resolved_model, str) or not resolved_model.strip():
            resolved_model = None
        self.append({
            "kind": "blocked" if blocked else "request",
            "game_id": request.context.get("game_id"), "phase_id": request.context.get("phase_id"),
            "agent_id": request.agent_id, "method": request.method,
            "attempt_index": request.context.get("attempt_index", 0),
            "error_type": type(error).__name__ if error else None,
            "provider": provider or {},
            "resolved_model": resolved_model if response is not None and not blocked else None,
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "cached_input_tokens": usage.get("cached_input_tokens"),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "estimated_cost_usd": usage.get("estimated_cost_usd"),
        })

    @staticmethod
    def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
        calls = [row for row in rows if row["kind"] == "request"]
        result = {
            "provider_calls": len(calls),
            "repair_calls": sum(row.get("attempt_index", 0) > 0 for row in calls),
            "provider_errors": sum(bool(row.get("error_type")) for row in calls),
            "budget_blocked_attempts": sum(row["kind"] == "blocked" for row in rows),
            "lifecycle_invocations": sum(row["kind"] == "lifecycle" for row in rows),
            "fallback_invocations": sum(row["kind"] == "lifecycle" and bool(row.get("fallback")) for row in rows),
            "skipped_eliminated_invocations": sum(row["kind"] == "eliminated_skip" for row in rows),
            "observed_resolved_models": sorted({row["resolved_model"] for row in calls if row.get("resolved_model")}),
        }
        for field in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "estimated_cost_usd"):
            known = [row[field] for row in calls if isinstance(row.get(field), (int, float))
                     and not isinstance(row[field], bool) and math.isfinite(row[field]) and row[field] >= 0]
            subtotal = round(sum(known), 10)
            result[field] = subtotal if len(known) == len(calls) else None
            result["known_" + field] = subtotal
            result["unknown_" + field + "_calls"] = len(calls) - len(known)
        return result

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "accounting_version": "provider_attempts_v1",
            "totals": self.totals(self.rows),
            "by_agent": {agent: self.totals([r for r in self.rows if r.get("agent_id") == agent])
                         for agent in sorted({r["agent_id"] for r in self.rows})},
            "by_method": {method: self.totals([r for r in self.rows if r.get("method") == method])
                          for method in sorted({r["method"] for r in self.rows})},
            "by_agent_method": {agent: {
                method: self.totals([r for r in self.rows if r.get("agent_id") == agent and r.get("method") == method])
                for method in sorted({r["method"] for r in self.rows if r["agent_id"] == agent})
            } for agent in sorted({r["agent_id"] for r in self.rows})},
            "repair_usage": self.totals([r for r in self.rows if r.get("attempt_index", 0) > 0]),
            "post_elimination_cleanup": self.totals([r for r in self.rows if r.get("post_elimination_cleanup")]),
        }

    def write_summary(self) -> dict[str, Any]:
        summary = self.summary()
        (self.root / "provider_usage.json").write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
        lines = ["# Provider usage", "", "Counts are actual requests, including repairs and errors. Blocked attempts are separate.",
                 "Unknown totals remain null; known subtotals are lower bounds, not savings estimates.", "",
                 "| Method | Requests | Repairs | Errors | Fallback invocations | Cost USD | Known cost USD |",
                 "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for method, row in summary["by_method"].items():
            cost = row["estimated_cost_usd"]
            lines.append(f"| {method} | {row['provider_calls']} | {row['repair_calls']} | {row['provider_errors']} | {row['fallback_invocations']} | {cost if cost is not None else 'unknown'} | {row['known_estimated_cost_usd']} |")
        lines += ["", "Post-elimination cleanup: " + json.dumps(summary["post_elimination_cleanup"], sort_keys=True),
                  "", "Skipped eliminated lifecycle invocations: " + str(summary["totals"]["skipped_eliminated_invocations"])]
        (self.root / "provider_usage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary
