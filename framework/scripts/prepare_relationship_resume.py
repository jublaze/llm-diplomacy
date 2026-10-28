from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, atomic_json, canonical_hash


class NoInference:
    def generate(self, request):
        raise AssertionError("resume preparation must never make a provider request")


def revalidate(runner: PopulationExperimentRunner, recovery: ExperimentRecovery) -> dict:
    checkpoint = recovery._read_checkpoint()
    if checkpoint is None:
        raise ValueError("a halted checkpoint is required")
    rows = recovery._journal_rows()
    recovery._validate_journal_semantics(rows)
    recovery._validate_snapshot()
    if recovery.in_flight():
        raise ValueError("unknown request outcomes require separate review")
    stage = recovery.working_root / "experiments" / runner.config.experiment_id
    runtime = BaselineLLMAgentRuntime(schema_version=runner.config.schema_version,
                                     runtime_root=stage / "games/game_001/agents",
                                     provider=NoInference(), halt_on_issue=True)
    received = [row for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"]
    results = []
    for row in received:
        game, phase, occurrence, country, agent, method = row["decision_id"].split("/")
        input_path = stage / "games" / game / "agents" / agent / "runtime/phase_inputs" / f"{phase}_{method}.json"
        saved = json.loads(input_path.read_text(encoding="utf-8"))
        context = AgentContext(**saved["context"])
        payload = json.loads(row["response"]["content"])
        if not isinstance(payload, dict):
            raise ValueError(f"saved response is not an object: {row['decision_id']}")
        original_updates = payload.get("relationship_updates", [])
        if method == "reflect":
            issue = runtime._fresh_reflection_issue(payload, context=context) or runtime._reflection_normalization_issue(payload)
            if issue:
                raise ValueError(f"saved reflection is still invalid: {issue}")
            payload = runtime._normalize_reflection_candidate(payload)
        elif method == "negotiate_tick":
            payload, normalization = runtime._normalize_negotiate_candidate(payload)
            if normalization.get("schema_limit_trimmed"):
                raise ValueError("saved negotiation would still be trimmed")
        valid, issue = runtime._validate_payload(method=method, payload=payload, context=context,
                                               built_context=runtime.context_builder.build(context))
        if not valid:
            raise ValueError(f"saved response is still invalid: {row['decision_id']}: {issue}")
        if method == "reflect":
            semantic_issue = runtime._reflection_semantic_issue(payload=payload, context=context)
            if semantic_issue:
                raise ValueError(f"saved reflection would trigger fallback: {semantic_issue}")
            payload = runtime._coerce_reflection_payload(payload)
        if len(payload.get("relationship_updates", [])) != len(original_updates):
            raise ValueError("relationship updates would still be lost")
        results.append({"decision_id": row["decision_id"], "attempt_id": row["attempt_id"],
                        "response_sha256": canonical_hash(row["response"]),
                        "relationship_updates": len(original_updates), "valid": True})
    if not results:
        raise ValueError("no saved responses to revalidate")
    return {"validated_responses": results, "provider_calls_executed": 0,
            "from_identity_sha256": canonical_hash(checkpoint["identity"]),
            "to_identity_sha256": canonical_hash(recovery.identity)}



def ensure_backup(recovery: ExperimentRecovery, *, expected_before: str, expected_after: str) -> None:
    backup = recovery.path / "before_relationship_six"
    names = ("checkpoint.json", "journal.jsonl", "journal_head.json", "snapshot_inventory.json")
    manifest_path = backup / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {"from_identity": expected_before, "to_identity": expected_after}
        if any(manifest.get(key) != value for key, value in expected.items()) or set(manifest.get("files", {})) != set(names):
            raise ValueError("predecessor backup manifest does not match approval")
        for name in names:
            target = backup / name
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != manifest["files"][name]:
                raise ValueError("predecessor backup is incomplete or changed")
        return
    if recovery._relationship_upgrade() is not None:
        raise ValueError("existing approval has no verified predecessor backup")
    backup.mkdir(exist_ok=True)
    hashes = {}
    for name in names:
        data = (recovery.path / name).read_bytes()
        target = backup / name
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("partial predecessor backup differs from source evidence")
        else:
            fd, temporary = tempfile.mkstemp(dir=backup, prefix=".backup-")
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        hashes[name] = hashlib.sha256(data).hexdigest()
    atomic_json(manifest_path, {"from_identity": expected_before, "to_identity": expected_after, "files": hashes})


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline preparation for the reviewed six-relationship code upgrade; never launches play.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--agents-root", required=True)
    parser.add_argument("--prepare", action="store_true", help="Append an explicit compatibility approval after offline revalidation.")
    parser.add_argument("--expected-before", help="Required with --prepare: exact checkpoint identity hash.")
    parser.add_argument("--expected-after", help="Required with --prepare: exact current code/config identity hash.")
    args = parser.parse_args()
    if args.prepare and (not args.expected_before or not args.expected_after):
        parser.error("--prepare requires both explicitly approved identity hashes")
    cfg = load_population_experiment_config(args.config)
    runner = PopulationExperimentRunner(config=cfg, base_dir=args.output_root, agents_root=args.agents_root)
    root = Path(args.output_root) / "experiments" / cfg.experiment_id
    if cfg.game_count != 1 or not cfg.raw.get("provider_failure_policy", {}).get("strict_recovery"):
        raise ValueError("relationship recovery preparation requires one strict-recovery game")
    Path(args.agents_root).resolve().relative_to(root.resolve())
    if not (root / "recovery/checkpoint.json").is_file():
        raise ValueError("no halted experiment checkpoint exists")
    recovery = ExperimentRecovery(root, identity=runner._recovery_identity(), resume=True)
    with RecoveryLock(recovery.path / "writer.lock"):
        report = revalidate(runner, recovery)
        if args.prepare:
            if report["from_identity_sha256"] != args.expected_before or report["to_identity_sha256"] != args.expected_after:
                raise ValueError("explicit approval identity mismatch")
            ensure_backup(recovery, expected_before=args.expected_before, expected_after=args.expected_after)
            accounting = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (root / "metrics").rglob("*") if path.is_file()}
            recovery.approve_relationship_limit_six(expected_from=args.expected_before, expected_to=args.expected_after)
            assert accounting == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (root / "metrics").rglob("*") if path.is_file()}
            report["prepared"] = True
            atomic_json(recovery.path / "relationship_six_preparation.json", report)
        else:
            report["prepared"] = False
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
