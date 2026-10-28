"""Emit Python builder outcome normalization for the shared policy-case table."""
from __future__ import annotations
import json
from pathlib import Path
from build_fixtures import normalized_outcome

cases = json.loads((Path(__file__).parents[1] / "tests" / "outcome-policy-cases.json").read_text(encoding="utf-8"))
print(json.dumps([{"name": case["name"], "outcome": normalized_outcome(case["raw"], case["final_year"], case["final_year"] if case["expected_kind"] == "scheduled_max_year" else None)} for case in cases], sort_keys=True))
