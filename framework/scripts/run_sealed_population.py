"""Start the reviewed population entrypoint only from an exact sealed package."""
from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
package = root / "src" / "llm_diplomacy"
entrypoint = root / "scripts" / "run_population_experiment.py"
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--expected-code-sha256", required=True)
parser.add_argument("--expected-entrypoint-sha256", required=True)
parser.add_argument("--prompt-checkpoint-backup")
parser.add_argument("--prompt-checkpoint-sha256")
parser.add_argument("--verify-only", action="store_true")
args, remaining = parser.parse_known_args()
if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in package.rglob("*")):
    raise SystemExit("sealed runtime package cannot contain links")
manifest = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.rglob("*")) if p.is_file() and not p.is_symlink()
            and "__pycache__" not in p.parts and p.suffix != ".pyc"}
encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
code_sha = hashlib.sha256(encoded).hexdigest()
entry_sha = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
if code_sha != args.expected_code_sha256 or entry_sha != args.expected_entrypoint_sha256:
    raise SystemExit("sealed runtime package or entrypoint differs from reviewed hashes")
sys.path.insert(0, str(root / "src"))
import llm_diplomacy
if Path(llm_diplomacy.__file__).resolve().parent != package.resolve():
    raise SystemExit("population runner imported an unexpected package")
if args.verify_only:
    print(json.dumps({"runtime_package": str(package.resolve()), "runtime_code_sha256": code_sha,
                      "entrypoint": str(entrypoint.resolve()), "entrypoint_sha256": entry_sha}))
    raise SystemExit(0)
if not args.prompt_checkpoint_backup or not args.prompt_checkpoint_sha256:
    raise SystemExit("the reviewed original prompt checkpoint and hash are required")
from llm_diplomacy.core.prompt_assets import bind_checkpoint_prompt_assets
bind_checkpoint_prompt_assets(args.prompt_checkpoint_backup, expected_file_sha256=args.prompt_checkpoint_sha256)
sys.argv = [str(entrypoint), *remaining]
runpy.run_path(str(entrypoint), run_name="__main__")
