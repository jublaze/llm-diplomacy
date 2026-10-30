from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_diplomacy.population import (
    PopulationExperimentRunner,
    load_population_experiment_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a population experiment from blueprint configs.")
    parser.add_argument("--config", required=True, help="Path to population experiment config (yaml/json).")
    parser.add_argument("--output-root", default=".", help="Output root directory.")
    parser.add_argument("--agents-root", default="agents", help="Agent folio root directory.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not run games or call providers.")
    parser.add_argument("--resume", action="store_true", help="Explicitly resume the interrupted strict-recovery run.")
    parser.add_argument("--status", action="store_true", help="Show durable recovery status without changing the run.")
    parser.add_argument("--request-halt", action="store_true", help="Request a cooperative halt before the next provider dispatch or phase effect.")
    parser.add_argument("--halt-reason", default="operator_request", help="Reason recorded with --request-halt.")
    parser.add_argument("--decision-action", choices=("retry_unknown", "abandon_unknown", "retry_invalid"),
                        help="Reviewed decision for one pending or rejected logical request.")
    parser.add_argument("--decision-id", help="Logical decision ID shown by --status.")
    parser.add_argument("--attempt-id", help="Attempt ID shown by --status.")
    parser.add_argument("--prompt-hash", help="Required with a recovery decision; hash shown in status/journal.")
    args = parser.parse_args()

    cfg = load_population_experiment_config(args.config)
    runner = PopulationExperimentRunner(
        config=cfg,
        base_dir=args.output_root,
        agents_root=args.agents_root,
    )
    if args.status and args.request_halt:
        parser.error("--status and --request-halt cannot be combined")
    if args.request_halt:
        runner.request_safe_halt(args.halt_reason)
        print(json.dumps({"experiment_id": cfg.experiment_id, "safe_halt_requested": True,
                          "reason": args.halt_reason}, sort_keys=True))
        return 0
    if args.status:
        print(json.dumps(runner.recovery_status(), sort_keys=True))
        return 0
    result = runner.run(
        dry_run_override=args.dry_run,
        resume=args.resume,
        decision_action=args.decision_action,
        decision_id=args.decision_id,
        attempt_id=args.attempt_id,
        prompt_hash=args.prompt_hash,
    )
    payload = {
        "schema_version": cfg.schema_version,
        "experiment_id": cfg.experiment_id,
        "experiment_path": str(result.experiment_root),
        "population_manifest_path": str(result.manifest_path),
        "population_summary_json_path": str(result.summary_json_path),
        "population_summary_md_path": str(result.summary_md_path),
        "dry_run_report_path": str(result.dry_run_report_path) if result.dry_run_report_path else None,
        "pilot_report_json_path": str(result.pilot_report_json_path) if result.pilot_report_json_path else None,
        "pilot_report_md_path": str(result.pilot_report_md_path) if result.pilot_report_md_path else None,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
