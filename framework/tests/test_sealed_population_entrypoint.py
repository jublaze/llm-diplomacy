import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("BUILD_RULE_CANDIDATE_ROOT", Path(__file__).parents[1]))


def command(code_hash, entry_hash):
    return [sys.executable, "-B", str(ROOT / "scripts/run_sealed_population.py"),
            "--expected-code-sha256", code_hash, "--expected-entrypoint-sha256", entry_hash, "--verify-only"]


def test_sealed_entrypoint_verifies_exact_import_origin_and_hashes():
    import llm_diplomacy
    from llm_diplomacy.runner.recovery import canonical_hash
    package = ROOT / "src/llm_diplomacy"
    assert Path(llm_diplomacy.__file__).resolve().parent == package.resolve()
    manifest = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(package.rglob('*')) if p.is_file() and not p.is_symlink()
                and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    code = canonical_hash(manifest)
    entry = hashlib.sha256((ROOT / "scripts/run_population_experiment.py").read_bytes()).hexdigest()
    result = subprocess.run(command(code, entry), capture_output=True, text=True)
    assert result.returncode == 0
    assert Path(json.loads(result.stdout)['runtime_package']) == package.resolve()
    wrong = subprocess.run(command('0' * 64, entry), capture_output=True, text=True)
    assert wrong.returncode != 0 and 'differs from reviewed hashes' in wrong.stderr
