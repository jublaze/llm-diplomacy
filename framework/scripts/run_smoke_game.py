from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_diplomacy.compliance.artifacts import ArtifactValidator
from llm_diplomacy.config.loader import load_experiment_config
from llm_diplomacy.runner.experiment_runner import ExperimentRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end smoke experiment.")
    parser.add_argument("--config", required=True, help="Path to experiment config JSON/YAML")
    parser.add_argument("--output-root", required=True, help="Root output directory")
    args = parser.parse_args()

    config = load_experiment_config(args.config)
    runner = ExperimentRunner(config=config, base_dir=args.output_root)
    summary = runner.run()

    experiment_root = Path(args.output_root) / "experiments" / config.experiment_id
    report = ArtifactValidator(schema_version=config.schema_version).validate_experiment_dir(experiment_root)
    payload = {
        "schema_version": config.schema_version,
        "summary": {
            "experiment_id": summary.experiment_id,
            "games_completed": summary.games_completed,
            "files_checked": report.files_checked,
            "validation_ok": report.ok,
            "errors": report.errors,
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
