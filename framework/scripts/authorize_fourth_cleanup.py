"""Offline, one-run authorization for the human-approved fourth Austria cleanup."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, atomic_json, canonical_hash


def authorize(*, apply: bool = False) -> dict:
    game = ROOT / "experiments/luna1912-1018-r2"
    evidence = game / "fourth-cleanup-20261026"
    with RecoveryLock(game / "recovery/writer.lock"):
        cfg = load_population_experiment_config(game / "launch.yaml")
        identity = PopulationExperimentRunner(config=cfg, base_dir=ROOT,
            agents_root=game / "agents")._recovery_identity()
        recovery = ExperimentRecovery(game, identity=identity, resume=True)
        rows = recovery._journal_rows()
        existing = [r for r in rows if r["kind"] == "fourth_cleanup_authorization"]
        if existing:
            completions = [r for r in rows if r["kind"] == "fourth_cleanup_compatibility_completion"]
            if not completions and existing[0]["to_identity"] != identity:
                if len(rows) != 5675 or rows[-1] != existing[0] or recovery.in_flight():
                    raise ValueError("completion requires the unchanged prelaunch authorization")
                source_root = ROOT / "src/llm_diplomacy"
                code = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                        and "__pycache__" not in p.parts and p.suffix != ".pyc"}
                completion = dict(kind="fourth_cleanup_compatibility_completion",
                    approval_sha256=existing[0]["sha256"], from_identity=existing[0]["to_identity"],
                    to_identity=identity, target_code=code,
                    reason="Validate the prior checkpoint through the full fourth-cleanup approval chain; preserve checkpoint bytes")
                recovery._validate_fourth_cleanup(existing, recovery._read_checkpoint(), completion=completion)
                atomic_json(evidence / "compatibility-completion-plan.json", completion)
                if apply:
                    recovery._append(completion)
                    recovery.prepare()
                return {"completion_applied": apply, "provider_calls_executed": 0}
            recovery._validate_fourth_cleanup(existing, recovery._read_checkpoint())
            return {"already_applied": True, "provider_calls_executed": 0}
        recovery._validate_journal_semantics(rows)
        recovery._validate_snapshot()
        checkpoint = recovery._read_checkpoint()
        if recovery.in_flight():
            raise ValueError("unresolved provider request blocks authorization")
        code_root = ROOT / "src/llm_diplomacy"
        target = {p.relative_to(code_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(code_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        source = dict(target)
        for relative in ("runner/recovery.py", "agents/baseline_llm_runtime.py"):
            source[relative] = hashlib.sha256((evidence / "before-source" / Path(relative).name).read_bytes()).hexdigest()
        ledger = (game / "metrics/provider_attempts.jsonl").read_bytes()
        approval = dict(kind="fourth_cleanup_authorization", from_identity=checkpoint["identity"],
            to_identity=identity, source_code=source, target_code=target, source_checkpoint=checkpoint,
            source_checkpoint_sha256=canonical_hash(checkpoint),
            source_journal_count=len(rows), source_journal_head=rows[-1]["sha256"],
            source_ledger_bytes=len(ledger), source_ledger_sha256=hashlib.sha256(ledger).hexdigest(),
            decision_id="game_001/game_end_cleanup/once/austria/birsan/game_end_cleanup",
            prompt_hash="88c00803ccb7f545dac6e7cbc4524fc8025eaa85c28a97eded802d329b989ac7",
            parent_attempt_id="8baac9c8-dd74-4e7f-bfbf-fee2b45040db", max_attempts=4,
            authorization="Human explicitly approved: Yes-one fourth cleanup attempt; other limits unchanged")
        recovery._validate_fourth_cleanup([approval], checkpoint)
        for name in ("launch.yaml", "recovery/checkpoint.json", "recovery/journal.jsonl",
                     "recovery/journal_head.json", "metrics/provider_attempts.jsonl", "metrics/budget/budget_state.json"):
            dest = evidence / "before" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copyfile(game / name, dest)
        atomic_json(evidence / "plan.json", approval)
        if apply:
            recovery._append(approval)
            recovery.prepare()
            atomic_json(evidence / "receipt.json", {"applied": True, "provider_calls_executed": 0,
                        "max_attempts": 4, "decision_id": approval["decision_id"]})
        return {"applied": apply, "provider_calls_executed": 0, "max_attempts": 4}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(authorize(apply=parser.parse_args().apply)))
