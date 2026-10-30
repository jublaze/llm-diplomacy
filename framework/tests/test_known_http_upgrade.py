import copy
import hashlib
import json
from pathlib import Path
import pytest
import llm_diplomacy.runner.recovery as module
from llm_diplomacy.runner.recovery import ExperimentRecovery, atomic_json, canonical_hash


def fixture(tmp_path):
    root = tmp_path / "game"
    (root / "recovery").mkdir(parents=True)
    (root / "metrics/budget").mkdir(parents=True)
    pkg = Path(module.__file__).parents[1]
    target = {p.relative_to(pkg).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(pkg.rglob("*")) if p.is_file() and not p.is_symlink()
              and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    source = dict(target)
    source.pop("runner/known_http_reservations.py")
    source["runner/recovery.py"] = "old-recovery"
    source["runner/build_rule_accounting.py"] = "old-accounting"
    before = {"experiment_id":"game", "code_sha256":canonical_hash(source), "config":"same"}
    after = {**before, "code_sha256":canonical_hash(target)}
    failure = {"kind":"attempt", "attempt_id":"attempt", "decision_id":"g/p/once/c/a/reflect",
               "prompt_hash":"prompt", "outcome":"known_failed", "error_category":"provider_transient_http",
               "response":None, "usage":None, "sequence":1, "sha256":"failure"}
    rows = [{"kind":"created", "sequence":0, "sha256":"head0"}, failure,
            {"kind":"halt", "sequence":2, "reason":"provider_transient_http", "decision_id":failure["decision_id"], "sha256":"head2"}]
    cp = {"version":1, "identity":before, "halt_required":True, "halt_reason":"provider_transient_http",
          "decision_id":failure["decision_id"], "journal_anchor":{"count":2,"head":"failure"}}
    atomic_json(root / "recovery/known-http-source-checkpoint.json", {**cp,"sha256":canonical_hash(cp)})
    (root / "recovery/known-http-source-budget.json").write_text('{}',encoding='utf8')
    (root / "metrics/provider_attempts.jsonl").write_text('ledger\n',encoding='utf8')
    (root / "metrics/budget/budget_events.jsonl").write_text('events\n',encoding='utf8')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    transition = {"sha256":"transition", "to_identity":before, "target_code_manifest":source}
    upgrade = {"kind":"known_http_accounting_upgrade_v1", "sequence":3, "previous_hash":"head2",
               "source_journal_count":3, "source_journal_head":"head2", "transition_sha256":"transition",
               "from_identity":before, "to_identity":after, "source_code_manifest":source,
               "target_code_manifest":target, "failure":failure,
               "source_checkpoint_sha256":sha(root / "recovery/known-http-source-checkpoint.json"),
               "source_budget_sha256":sha(root / "recovery/known-http-source-budget.json"),
               "source_ledger_bytes":(root / "metrics/provider_attempts.jsonl").stat().st_size, "source_ledger_sha256":sha(root / "metrics/provider_attempts.jsonl"),
               "source_budget_events_bytes":(root / "metrics/budget/budget_events.jsonl").stat().st_size, "source_budget_events_sha256":sha(root / "metrics/budget/budget_events.jsonl")}
    return ExperimentRecovery(root, identity=after, resume=True), transition, rows + [upgrade]


def test_upgrade_binds_checkpoint_source_and_package(tmp_path):
    r, t, rows = fixture(tmp_path)
    r._validate_known_http_upgrade(t, rows)


@pytest.mark.parametrize("mutation", ["head", "config", "manifest", "failure", "checkpoint", "ledger", "events", "budget", "duplicate"])
def test_upgrade_rejects_tampering(tmp_path, mutation):
    r, t, rows = fixture(tmp_path)
    rows = copy.deepcopy(rows)
    u = rows[-1]
    if mutation == "head": u["source_journal_head"] = "wrong"
    if mutation == "config": u["to_identity"]["config"] = "changed"
    if mutation == "manifest": u["target_code_manifest"]["runner/recovery.py"] = "wrong"
    if mutation == "failure": u["failure"]["outcome"] = "unknown"
    if mutation == "checkpoint": u["source_checkpoint_sha256"] = "wrong"
    if mutation == "ledger": u["source_ledger_sha256"] = "wrong"
    if mutation == "events": u["source_budget_events_sha256"] = "wrong"
    if mutation == "budget": u["source_budget_sha256"] = "wrong"
    if mutation == "duplicate": rows.append(copy.deepcopy(u))
    with pytest.raises(ValueError): r._validate_known_http_upgrade(t, rows)
