from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.runner import ScriptedExperimentRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Run capped mixed LLM + scripted game.")
    parser.add_argument("--config", required=True, help="Path to experiment config.")
    parser.add_argument("--output-root", default=".", help="Output root directory.")
    parser.add_argument("--max-years", type=int, default=1, help="Capped years to simulate.")
    args = parser.parse_args()

    cfg = load_experiment_config(args.config)
    summary = ScriptedExperimentRunner(
        config=cfg,
        base_dir=args.output_root,
        max_years=args.max_years,
        negotiation_ticks=int(cfg.negotiation.get("max_ticks", 1)) if isinstance(cfg.negotiation, dict) else 1,
    ).run()
    exp_root = Path(args.output_root) / "experiments" / cfg.experiment_id
    game_id = "game_001"
    payload = {
        "schema_version": cfg.schema_version,
        "experiment_path": str(exp_root),
        "game_id": game_id,
        "observatory_report_path": str(exp_root / "games" / game_id / "observatory" / "index.html"),
        "cost_summary_path": summary.cost_summary_path,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
