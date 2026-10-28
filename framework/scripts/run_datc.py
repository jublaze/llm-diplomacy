"""Validate pinned DATC cases against declared project-policy and adapter outcomes."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_diplomacy.compliance.datc import check_datc_coverage, ingest_datc_fixtures
from llm_diplomacy.rules.diplomacy import DiplomacyRuleEngine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path, default=ROOT / "tests/fixtures/datc/upstream")
    args = parser.parse_args()
    report = check_datc_coverage(DiplomacyRuleEngine(), ingest_datc_fixtures(args.fixtures_dir), args.fixtures_dir / "coverage.json")
    print(json.dumps(report, indent=2))
    return 1 if report["unexplained"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
