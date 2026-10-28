"""Source-configured portability gate; intentionally excluded from normal npm test."""
from __future__ import annotations
import argparse, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Verify portable fixture build and audit commands against raw artifacts.")
parser.add_argument("--source-root", required=True, help="Path to a completed experiment root.")
args = parser.parse_args()
source = Path(args.source_root).resolve()
if not (source / "manifest.json").is_file(): parser.error("--source-root must be a completed experiment root containing manifest.json")

def run(command): subprocess.run(command, cwd=ROOT, check=True)
for command in ([sys.executable, "scripts/build_fixtures.py"], [sys.executable, "scripts/audit_fixture_integrity.py"]):
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert failed.returncode != 0 and "--source-root" in failed.stderr, f"missing source-root did not fail clearly: {command}"
with tempfile.TemporaryDirectory(prefix="diplomacy-viewer-fixtures-") as temp:
    output = Path(temp) / "fixtures"
    run([sys.executable, "scripts/build_fixtures.py", "--source-root", str(source), "--output-root", str(output)])
    reports = [str(output / game / "report.json") for game in ("game_001", "game_002")]
    run(["node", "--experimental-strip-types", "scripts/validate_schema.ts", *reports])
    run(["node", "--experimental-strip-types", "scripts/validate_bundle.ts", *reports])
    run([sys.executable, "scripts/audit_fixture_integrity.py", "--source-root", str(source), "--fixtures-root", str(output)])
print("portable raw-fixture toolchain: valid")
