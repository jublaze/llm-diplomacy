from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_diplomacy.factory import AgentFactory, load_blueprint


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an agent folio from a blueprint.")
    parser.add_argument("blueprint", help="Path to blueprint YAML/JSON.")
    parser.add_argument("--agents-root", default="agents", help="Output root for generated agents.")
    args = parser.parse_args()

    blueprint = load_blueprint(args.blueprint)
    factory = AgentFactory(schema_version=blueprint.schema_version)
    agent_path, report = factory.create_and_validate(blueprint, agents_root=args.agents_root)
    payload = {
        "schema_version": blueprint.schema_version,
        "agent_id": blueprint.agent_id,
        "agent_path": str(agent_path),
        "valid": report.ok,
        "errors": report.errors,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
