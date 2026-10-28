"""Offline preparation of the three human-authorized Italy cleanup attempts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, atomic_json, canonical_hash


def authorize(*, game: Path, evidence: Path, apply: bool = False, pass_number: int = 2):
    with RecoveryLock(game / "recovery/writer.lock"):
        cfg = load_population_experiment_config(game / "launch.yaml")
        identity = PopulationExperimentRunner(config=cfg, base_dir=ROOT,
                                              agents_root=game / "agents")._recovery_identity()
        recovery = ExperimentRecovery(game, identity=identity, resume=True)
        rows = recovery._journal_rows()
        approval = recovery._italy_cleanup_approval()
        if approval is None:
            if pass_number != 2 or len(rows) != 5739 or recovery.in_flight():
                raise ValueError("requires the exact first Italy cleanup halt")
            checkpoint = recovery._read_checkpoint()
            source_root = ROOT / "src/llm_diplomacy"
            target = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                      and "__pycache__" not in p.parts and p.suffix != ".pyc"}
            source = dict(target)
            source["runner/recovery.py"] = hashlib.sha256((evidence / "before-source/recovery.py").read_bytes()).hexdigest()
            runtime_root = recovery.working_root / "experiments/luna1912-1018-r2/games/game_001/agents"
            saved = json.loads((runtime_root / "major/runtime/phase_inputs/game_end_cleanup_game_end_cleanup.json").read_text())
            context = AgentContext(**saved["context"])
            runtime = BaselineLLMAgentRuntime(schema_version=context.schema_version, runtime_root=runtime_root,
                                             provider=None, halt_on_issue=True)
            prompt = runtime.context_builder.build_prompt(method="game_end_cleanup",
                built_context=runtime.context_builder.build(context), free_text_reflection=True)
            ledger = (game / "metrics/provider_attempts.jsonl").read_bytes()
            approval = dict(kind="italy_cleanup_authorization", from_identity=checkpoint["identity"],
                to_identity=identity, source_code=source, target_code=target,
                source_checkpoint=checkpoint, source_checkpoint_sha256=canonical_hash(checkpoint),
                source_journal_count=len(rows), source_journal_head=rows[-1]["sha256"],
                source_ledger_bytes=len(ledger), source_ledger_sha256=hashlib.sha256(ledger).hexdigest(),
                decision_id="game_001/game_end_cleanup/once/italy/major/game_end_cleanup",
                parent_attempt_id="f3e3f62d-e0a7-4564-a629-261c08195a0e", max_attempts=4,
                base_prompt_hash="196806778fcf017e723390fbb36d30b22c06578dc01c20b417b7cef850d5e937",
                base_prompt=prompt,
                authorization="Human: Give Italyup to  3 more attempts")
            recovery._validate_journal_semantics(rows)
            recovery._validate_snapshot()
            recovery._validate_italy_cleanup(approval, checkpoint)
            atomic_json(evidence / "authorization-plan.json", approval)
            if apply:
                recovery._append(approval)
        else:
            recovery._validate_italy_cleanup(approval, recovery._read_checkpoint())
        intent = None
        if apply:
            intent = recovery.prepare_italy_cleanup_feedback(pass_number)
            recovery.prepare()
            recovery.status()
            atomic_json(evidence / f"pass-{pass_number}-receipt.json",
                        dict(applied=True, intent=intent, provider_calls_executed=0))
        return dict(applied=apply, pass_number=pass_number, provider_calls_executed=0,
                    prompt_hash=intent["prompt_hash"] if intent else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--pass-number", type=int, choices=(2, 3, 4), default=2)
    parser.add_argument("--game-root", type=Path, default=ROOT / "experiments/luna1912-1018-r2")
    parser.add_argument("--evidence", type=Path, default=ROOT / "experiments/luna1912-1018-r2/italy-cleanup-feedback-20261028")
    args = parser.parse_args()
    print(json.dumps(authorize(game=args.game_root.resolve(), evidence=args.evidence.resolve(),
                               apply=args.apply, pass_number=args.pass_number)))
