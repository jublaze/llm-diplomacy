"""One-run, offline migration for the explicitly approved input-cap doubling."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, atomic_json
from llm_diplomacy.guardrails.budget import BudgetController


def migrate(root: Path, *, apply: bool = False) -> dict:
    game = root / "experiments/luna1912-1018-r2"
    evidence = game / "input-budget-double-20261026"
    evidence.mkdir(exist_ok=True)
    plan_path = evidence / "plan.json"
    config_path = game / "launch.yaml"
    state_path = game / "metrics/budget/budget_state.json"
    with RecoveryLock(game / "recovery/writer.lock"):
        if plan_path.exists():
            plan = json.loads(plan_path.read_text())
        else:
            config_text = config_path.read_text()
            if config_text.count("max_input_tokens: 19919602") != 1:
                raise ValueError("expected exact original input cap")
            target_text = config_text.replace("max_input_tokens: 19919602", "max_input_tokens: 39839204")
            target_path = evidence / "target-launch.yaml"
            target_path.write_text(target_text)
            old_cfg = load_population_experiment_config(config_path)
            new_cfg = load_population_experiment_config(target_path)
            make_runner = lambda cfg: PopulationExperimentRunner(config=cfg, base_dir=root,
                                                       agents_root=game / "agents")
            new_identity = make_runner(new_cfg)._recovery_identity()
            checkpoint = json.loads((game / "recovery/checkpoint.json").read_text())
            old_identity = checkpoint["identity"]
            code_root = ROOT / "src/llm_diplomacy"
            new_code = {p.relative_to(code_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(code_root.rglob("*")) if p.is_file() and not p.is_symlink()
                        and "__pycache__" not in p.parts and p.suffix != ".pyc"}
            old_code = dict(new_code)
            original = game / "human-restart-20261025T225249Z/recovery-before-budget.py"
            old_code["runner/recovery.py"] = hashlib.sha256(original.read_text().encode()).hexdigest()
            rows = [json.loads(x) for x in (game / "recovery/journal.jsonl").read_text().splitlines()]
            ledger = (game / "metrics/provider_attempts.jsonl").read_bytes()
            approval = dict(kind="input_budget_upgrade", from_identity=old_identity,
                to_identity=new_identity, source_config=old_cfg.raw, target_config=new_cfg.raw,
                source_code=old_code, target_code=new_code, source_checkpoint=checkpoint,
                source_journal_count=len(rows), source_journal_head=rows[-1]["sha256"],
                source_ledger_bytes=len(ledger), source_ledger_sha256=hashlib.sha256(ledger).hexdigest(),
                authorization="Human: Yes raise it.. double it; input cap 19919602 -> 39839204; spending cap unchanged")
            recovery = ExperimentRecovery(game, identity=new_identity, resume=True)
            recovery._validate_journal_semantics(rows)
            recovery._validate_snapshot()
            if recovery.in_flight():
                raise ValueError("unresolved original request blocks migration")
            recovery._validate_input_budget_upgrade(approval, checkpoint)
            before = json.loads(state_path.read_text())
            if before["stop_reason"] != "max_input_tokens" or before["max_input_tokens"] != 19919602:
                raise ValueError("expected input budget halt")
            after = copy.deepcopy(before)
            fingerprint = json.loads(before["config_fingerprint"])
            fingerprint["max_input_tokens"] = 39839204
            after.update(max_input_tokens=39839204, config_fingerprint=json.dumps(fingerprint, sort_keys=True),
                         stop_experiment=False, force_fallback=False, stop_reason=None)
            # Validate the entire migrated accounting state through the normal budget loader on a copy.
            validation = evidence / "budget-validation"
            atomic_json(validation / "metrics/budget/budget_state.json", after)
            BudgetController.from_raw(schema_version="1.0", experiment_id="luna1912-1018-r2",
                                      artifact_root=validation, raw=new_cfg.raw["budget"])
            plan = dict(approval=approval, config_before=config_text, config_after=target_text,
                        budget_before=before, budget_after=after)
            backup = evidence / "before"
            for name in ["launch.yaml", "recovery/checkpoint.json", "recovery/journal.jsonl",
                         "recovery/journal_head.json", "metrics/provider_attempts.jsonl",
                         "metrics/budget/budget_state.json"]:
                dest = backup / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(game / name, dest)
            atomic_json(plan_path, plan)
        approval = plan["approval"]
        recovery = ExperimentRecovery(game, identity=approval["to_identity"], resume=True)
        recovery._validate_input_budget_upgrade(approval, recovery._read_checkpoint())
        if config_path.read_text() not in (plan["config_before"], plan["config_after"]):
            raise ValueError("configuration changed after preflight")
        if json.loads(state_path.read_text()) not in (plan["budget_before"], plan["budget_after"]):
            raise ValueError("accounting changed after preflight")
        if apply:
            config_path.write_text(plan["config_after"])
            atomic_json(state_path, plan["budget_after"])
            if not any(x["kind"] == "input_budget_upgrade" for x in recovery._journal_rows()):
                recovery._append(approval)
            blocked = recovery._journal_rows()[5181]
            if not any(x.get("kind") == "response_skipped" and x.get("attempt_id") == blocked["attempt_id"]
                       for x in recovery._journal_rows()):
                recovery._append(dict(kind="response_skipped", decision_id=blocked["decision_id"],
                    prompt_hash=blocked["prompt_hash"], attempt_id=blocked["attempt_id"],
                    choice="human_approved_input_budget_doubling"))
            recovery.prepare()
            atomic_json(evidence / "receipt.json", dict(applied=True, provider_calls_executed=0,
                        old_input_cap=19919602, new_input_cap=39839204, spending_cap_usd=74.75))
        return dict(applied=apply, old_input_cap=19919602, new_input_cap=39839204,
                    spending_cap_usd=74.75, provider_calls_executed=0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.root.resolve(), apply=args.apply)))
