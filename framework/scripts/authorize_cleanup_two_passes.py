"""Offline authorization and feedback preparation for Austria cleanup passes five and six."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, atomic_json, canonical_hash


def authorize(*, apply=False, pass_number=5):
    game = ROOT / "experiments/luna1912-1018-r2"
    evidence = game / "fifth-cleanup-feedback-20261026"
    with RecoveryLock(game / "recovery/writer.lock"):
        cfg = load_population_experiment_config(game / "launch.yaml")
        identity = PopulationExperimentRunner(config=cfg, base_dir=ROOT,
            agents_root=game / "agents")._recovery_identity()
        recovery = ExperimentRecovery(game, identity=identity, resume=True)
        rows = recovery._journal_rows()
        approval = recovery._cleanup_two_pass_approval()
        if approval is None:
            if pass_number != 5 or len(rows) != 5685 or recovery.in_flight():
                raise ValueError("authorization requires the exact fourth cleanup halt")
            checkpoint = recovery._read_checkpoint()
            source_root = ROOT / "src/llm_diplomacy"
            target = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                      and "__pycache__" not in p.parts and p.suffix != ".pyc"}
            source = dict(target)
            for relative in ("runner/recovery.py", "agents/baseline_llm_runtime.py"):
                source[relative] = hashlib.sha256((evidence / "before-source" / Path(relative).name).read_bytes()).hexdigest()
            runtime_root = recovery.working_root / "experiments/luna1912-1018-r2/games/game_001/agents"
            saved = json.loads((runtime_root / "birsan/runtime/phase_inputs/game_end_cleanup_game_end_cleanup.json").read_text())
            context = AgentContext(**saved["context"])
            runtime = BaselineLLMAgentRuntime(schema_version=context.schema_version, runtime_root=runtime_root,
                                             provider=None, halt_on_issue=True)
            # For cleanup this includes the original private-memory context while
            # retaining the JSON schema (only method="reflect" permits prose).
            prompt = runtime.context_builder.build_prompt(method="game_end_cleanup",
                        built_context=runtime.context_builder.build(context), free_text_reflection=True)
            ledger = (game / "metrics/provider_attempts.jsonl").read_bytes()
            approval = dict(kind="cleanup_two_pass_authorization", from_identity=checkpoint["identity"],
                to_identity=identity, source_code=source, target_code=target,
                source_checkpoint=checkpoint, source_checkpoint_sha256=canonical_hash(checkpoint),
                source_journal_count=len(rows), source_journal_head=rows[-1]["sha256"],
                source_ledger_bytes=len(ledger), source_ledger_sha256=hashlib.sha256(ledger).hexdigest(),
                decision_id="game_001/game_end_cleanup/once/austria/birsan/game_end_cleanup",
                parent_attempt_id="8bbe2ef9-b9d5-4097-9b86-4724d87bca60", max_attempts=6,
                base_prompt_hash="88c00803ccb7f545dac6e7cbc4524fc8025eaa85c28a97eded802d329b989ac7",
                base_prompt=prompt, authorization="Human: Yes I authorize a fifth and sixth paid pass for Austria")
            recovery._validate_journal_semantics(rows)
            recovery._validate_snapshot()
            recovery._validate_cleanup_two_pass(approval, checkpoint)
            for name in ("launch.yaml", "recovery/checkpoint.json", "recovery/journal.jsonl",
                         "recovery/journal_head.json", "metrics/provider_attempts.jsonl", "metrics/budget/budget_state.json"):
                dest = evidence / "before" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copyfile(game / name, dest)
            atomic_json(evidence / "authorization-plan.json", approval)
            if apply:
                recovery._append(approval)
        else:
            recovery._validate_cleanup_two_pass(approval, recovery._read_checkpoint())
        if apply:
            intent = recovery.prepare_cleanup_feedback(pass_number)
            recovery.prepare()
            recovery.status()
            atomic_json(evidence / f"pass-{pass_number}-receipt.json",
                        dict(applied=True, intent=intent, provider_calls_executed=0))
        return dict(applied=apply, pass_number=pass_number, provider_calls_executed=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--pass-number", type=int, choices=(5, 6), default=5)
    args = parser.parse_args()
    print(json.dumps(authorize(apply=args.apply, pass_number=args.pass_number)))
