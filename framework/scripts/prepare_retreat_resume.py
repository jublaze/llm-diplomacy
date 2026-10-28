from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner.recovery import ExperimentRecovery, RecoveryLock, canonical_hash


ALLOWED_CODE_CHANGES = {"runner/scripted_experiment_runner.py", "runner/recovery.py"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_manifest(package_root: Path) -> dict[str, str]:
    return {path.relative_to(package_root).as_posix(): sha256_file(path)
            for path in sorted(package_root.rglob("*"))
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
            and path.suffix != ".pyc"}


def tree_manifest(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()}


def validate_package_change(baseline_manifest: dict[str, str], current_manifest: dict[str, str],
                           expected_current_code_hash: str) -> list[str]:
    if set(baseline_manifest) != set(current_manifest):
        raise ValueError("package file set changed from the captured source baseline")
    changed = {name for name in baseline_manifest if baseline_manifest[name] != current_manifest[name]}
    if not changed or not changed <= ALLOWED_CODE_CHANGES:
        raise ValueError(f"package changes exceed the reviewed allowlist: {sorted(changed)}")
    if canonical_hash(current_manifest) != expected_current_code_hash:
        raise ValueError("current package manifest differs from the runner recovery identity")
    return sorted(changed)


def prepare(*, config_path: Path, output_root: Path, agents_root: Path,
            baseline_package: Path, expected_before: str, expected_after: str,
            binding_path: Path, apply: bool) -> dict:
    config = load_population_experiment_config(str(config_path))
    runner = PopulationExperimentRunner(config=config, base_dir=output_root, agents_root=agents_root)
    if config.experiment_id != "luna1912-1018-r2" or config.game_count != 1:
        raise ValueError("retreat compatibility only applies to the reviewed one-game run")
    if not config.raw.get("provider_failure_policy", {}).get("strict_recovery"):
        raise ValueError("strict recovery must remain enabled")
    package_root = Path(__file__).resolve().parents[1] / "src" / "llm_diplomacy"
    baseline_manifest = package_manifest(baseline_package)
    current_manifest = package_manifest(package_root)
    changed_paths = validate_package_change(baseline_manifest, current_manifest,
                                            runner._recovery_identity().get("code_sha256"))

    experiment_root = output_root / "experiments" / config.experiment_id
    try:
        agents_root.resolve().relative_to(experiment_root.resolve())
    except ValueError as exc:
        raise ValueError("strict recovery agents root must stay inside the experiment") from exc
    checkpoint_path = experiment_root / "recovery" / "checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("a saved recovery checkpoint is required")
    checkpoint_bytes = checkpoint_path.read_bytes()
    checkpoint = json.loads(checkpoint_bytes.decode("utf-8"))
    identity = runner._recovery_identity()
    recovery = ExperimentRecovery(experiment_root, identity=identity, resume=True)
    with RecoveryLock(recovery.path / "writer.lock"):
        checkpoint_validated = recovery._read_checkpoint()
        if checkpoint_validated is None or checkpoint_validated.get("identity") != checkpoint.get("identity"):
            raise ValueError("saved checkpoint failed recovery integrity validation")
        rows = recovery._journal_rows()
        recovery._validate_journal_semantics(rows)
        recovery._validate_snapshot()
        relationship = recovery._relationship_upgrade()
        if relationship is None or relationship.get("to_identity") != checkpoint.get("identity"):
            raise ValueError("the six-relationship compatibility approval must remain the direct predecessor")
        if canonical_hash(checkpoint.get("identity")) != expected_before:
            raise ValueError("saved checkpoint identity differs from the explicit predecessor hash")
        if canonical_hash(identity) != expected_after:
            raise ValueError("current run identity differs from the explicit reviewed target hash")
        if canonical_hash(baseline_manifest) != checkpoint.get("identity", {}).get("code_sha256"):
            raise ValueError("captured package baseline differs from the halted checkpoint code identity")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        if binding.get("checkpoint_identity") != checkpoint.get("identity"):
            raise ValueError("saved checkpoint differs from the captured retreat binding")
        if recovery.in_flight() or (recovery.path / "commit_manifest.json").exists():
            raise ValueError("pending provider outcomes or tree publication need separate review")
        if (not checkpoint.get("halt_required")
                or checkpoint.get("halt_reason") != "orders_rejected_or_substituted"
                or checkpoint.get("cursor") != {"unit": "phase_started", "game_id": "game_001",
                                                "phase_id": "1902_spring_retreat"}):
            raise ValueError("the run is not halted at the exact reviewed retreat phase")
        source_rows = [row for row in rows if row.get("kind") == "attempt"
                       and row.get("outcome") == "received"
                       and row.get("decision_id", "").startswith("game_001/1902_spring_retreat/")]
        if len(source_rows) != 7:
            raise ValueError("expected exactly seven durable retreat responses")
        retreat_attempts = {}
        for row in source_rows:
            try:
                payload = json.loads(row["response"]["content"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("a retreat response is not valid saved JSON") from exc
            if not isinstance(payload, dict) or payload.get("orders") != []:
                raise ValueError("the reviewed compatibility only covers seven empty order lists")
            if recovery.is_abandoned(row["decision_id"]):
                raise ValueError("a retreat decision was explicitly abandoned")
            retreat_attempts[row["decision_id"]] = {
                "attempt_id": row["attempt_id"], "prompt_hash": row["prompt_hash"],
                "response_hash": canonical_hash(row.get("response")),
            }
        bound_attempts = {
            row["decision_id"]: {"attempt_id": row["attempt_id"], "prompt_hash": row["prompt_hash"],
                                 "response_hash": row["response_hash"]}
            for row in binding.get("received_retreats", [])
        }
        if retreat_attempts != bound_attempts:
            raise ValueError("saved retreat prompts or responses differ from the captured binding")
        rejected = [row for row in rows if row.get("kind") == "response_validation"
                    and row.get("valid") is False and row.get("decision_id") in retreat_attempts]
        rejected_ids = {row.get("attempt_id") for row in rejected}
        expected_ids = {row["attempt_id"] for row in retreat_attempts.values()}
        if rejected_ids != expected_ids:
            raise ValueError("all seven old global response rejection rows are required for this receipt")
        if any(row.get("kind") == "retreat_missing_order_upgrade" for row in rows):
            recovery._validate_retreat_missing_order_upgrade(checkpoint.get("identity"))
            return {"prepared": False, "already_prepared": True,
                    "from_identity_sha256": expected_before, "to_identity_sha256": expected_after,
                    "retreat_decisions": sorted(retreat_attempts), "provider_calls": 0}
        if not apply:
            return {"prepared": False, "from_identity_sha256": expected_before,
                    "to_identity_sha256": expected_after, "retreat_decisions": sorted(retreat_attempts),
                    "changed_package_paths": changed_paths, "provider_calls": 0}
        metrics_root = experiment_root / "metrics"
        metrics_before = tree_manifest(metrics_root)
        inventory_before = sha256_file(recovery.path / "snapshot_inventory.json")
        recovery.approve_retreat_missing_order_upgrade(
            expected_from=expected_before, expected_to=expected_after,
            source_code_manifest=baseline_manifest, target_code_manifest=current_manifest)
        if sha256_file(checkpoint_path) != hashlib.sha256(checkpoint_bytes).hexdigest():
            raise RuntimeError("checkpoint changed during compatibility receipt append")
        if tree_manifest(metrics_root) != metrics_before:
            raise RuntimeError("provider usage or budget evidence changed during compatibility preparation")
        if sha256_file(recovery.path / "snapshot_inventory.json") != inventory_before:
            raise RuntimeError("initial snapshot inventory changed during compatibility preparation")
        return {"prepared": True, "from_identity_sha256": expected_before,
                "to_identity_sha256": expected_after, "retreat_decisions": sorted(retreat_attempts),
                "changed_package_paths": changed_paths, "provider_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the reviewed forced-retreat compatibility receipt offline.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("."))
    parser.add_argument("--agents-root", required=True, type=Path)
    parser.add_argument("--baseline-package", required=True, type=Path,
                        help="exact-byte llm_diplomacy package captured before the fix")
    parser.add_argument("--binding-file", required=True, type=Path,
                        help="coordinator-captured identities and seven response hashes")
    parser.add_argument("--expected-before", required=True)
    parser.add_argument("--expected-after", required=True)
    parser.add_argument("--prepare", action="store_true", help="append the approved receipt after review")
    args = parser.parse_args()
    report = prepare(config_path=args.config, output_root=args.output_root, agents_root=args.agents_root,
                     baseline_package=args.baseline_package, expected_before=args.expected_before,
                     expected_after=args.expected_after, binding_path=args.binding_file, apply=args.prepare)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
