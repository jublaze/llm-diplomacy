"""Audit that fixture evidence still resolves to the source artifacts it names."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

parser = argparse.ArgumentParser(description="Audit fixture evidence against a completed experiment root.")
parser.add_argument("--source-root", required=True, help="Path to the completed experiment root.")
parser.add_argument("--fixtures-root", default=str(ROOT / "fixtures"), help="Fixture directory (default: ./fixtures).")
args = parser.parse_args()
experiment = Path(args.source_root).resolve()
if not (experiment / "manifest.json").is_file(): parser.error("--source-root must be a completed experiment root containing manifest.json")
for fixture in Path(args.fixtures_root).glob("game_*/report.json"):
    bundle = json.loads(fixture.read_text(encoding="utf-8"))
    sources = bundle["evidence_index"]
    for source_id, source in sources.items():
        path = experiment / source["artifact_path"]
        assert path.is_file(), f"{fixture}: {source_id} missing artifact {path}"
        assert sha256(path) == source["sha256"], f"{fixture}: {source_id} hash mismatch"
    for year in bundle["years"]:
        for phase in year["phases"]:
            for source_id in phase["narrative"]["source_refs"]:
                assert source_id in sources, f"{fixture}: {phase['phase_id']} unresolved narrative source {source_id}"
    print(f"{fixture}: {len(sources)} evidence artifacts resolve and match their hashes")
