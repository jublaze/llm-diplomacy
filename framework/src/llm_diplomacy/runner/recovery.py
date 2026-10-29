from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any


RECOVERY_VERSION = 1
_RUSSIA_ADJUSTMENT_DECISION = "game_001/1903_winter_adjustment/once/russia/birsan_ii/submit_orders"
JOURNAL_KINDS = {
    "build_rule_replay_transition_v1",
    "build_rule_replay_checkpoint_v1",
    "build_rule_boundary_adoption_v1",
    "build_rule_activation_v1",
    "build_rule_boundary_validation_v1",
    "repeat_correction_intent",
    "created", "resume_session", "commit_completed", "attempt", "dispatch", "dispatch_prepared",
    "response_validation", "response_skipped", "unknown_resolved", "operation_prepared",
    "operation_applied", "halt", "relationship_limit_upgrade", "retreat_missing_order_upgrade",
    "order_correction_intent", "order_correction_code_upgrade", "reflection_correction_code_upgrade",
    "negotiation_correction_code_upgrade",
    "free_text_reflection_migration",
    "adjustment_order_correction_code_upgrade", "input_budget_upgrade", "input_budget_manifest_completion",
    "fourth_cleanup_authorization",
    "fourth_cleanup_compatibility_completion",
    "cleanup_two_pass_authorization", "cleanup_feedback_intent",
    "russia_cleanup_authorization", "russia_cleanup_feedback_intent",
    "england_cleanup_authorization", "england_cleanup_feedback_intent",
    "italy_cleanup_authorization", "italy_cleanup_feedback_intent",
}


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"transaction paths must be regular files: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, destination)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


class RecoveryLock(AbstractContextManager["RecoveryLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self) -> RecoveryLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                if self.path.stat().st_size == 0:
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("experiment already has an active recovery writer") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


class ExperimentRecovery:
    """Durable recovery journal; records contain no provider credentials."""

    def __init__(self, root: str | Path, *, identity: dict[str, Any], resume: bool,
                 repeat_received_invalid: bool = False, max_wall_seconds: int | None = None,
                 deadline_utc: str | None = None) -> None:
        self.root = Path(root)
        self.path = self.root / "recovery"
        self.checkpoint_path = self.path / "checkpoint.json"
        self.journal_path = self.path / "journal.jsonl"
        self.safe_halt_path = self.path / "safe_halt.json"
        self.journal_head_path = self.path / "journal_head.json"
        self.identity = dict(identity)
        self.repeat_received_invalid = repeat_received_invalid
        if max_wall_seconds is not None and (not isinstance(max_wall_seconds, int)
                or isinstance(max_wall_seconds, bool) or max_wall_seconds <= 0):
            raise ValueError("max_wall_seconds must be a positive integer")
        self.max_wall_seconds = max_wall_seconds
        self.deadline = None
        if deadline_utc is not None:
            self.deadline = datetime.fromisoformat(deadline_utc)
            if self.deadline.tzinfo is None or self.deadline.utcoffset().total_seconds() != 0:
                raise ValueError("deadline_utc must be an absolute UTC timestamp")
            self.identity["deadline_utc"] = deadline_utc
        if max_wall_seconds is not None:
            self.identity["max_wall_seconds"] = max_wall_seconds
        if repeat_received_invalid:
            self.identity["received_invalid_policy"] = "repeat_with_feedback_v1"
        self.resume = resume
        self.needs_bootstrap = False

    @property
    def working_root(self) -> Path:
        return self.root.parent / f".{self.root.name}.recovery-work"

    def prepare_working_tree(self) -> None:
        if self.resume:
            # Validate every durable input before moving or replacing the current stage.
            self.prepare()
            snapshot = self.path / "initial_snapshot"
            if self.working_root.exists():
                partials = self.path / "partials"
                partials.mkdir(parents=True, exist_ok=True)
                os.replace(self.working_root, partials / f"stage-{uuid.uuid4().hex}")
            if snapshot.is_dir() and not self.needs_bootstrap:
                shutil.copytree(snapshot, self.working_root)
            elif self.attempt_count_total():
                raise ValueError("resume has provider attempts but no durable initial snapshot")
            else:
                if snapshot.exists() or (self.path / "snapshot_inventory.json").exists():
                    partials = self.path / "partials"
                    partials.mkdir(parents=True, exist_ok=True)
                    for source in (snapshot, self.path / "snapshot_inventory.json"):
                        if source.exists():
                            os.replace(source, partials / f"{source.name}-{uuid.uuid4().hex}")
                self.working_root.mkdir(parents=True, exist_ok=False)
            self._append({"kind": "resume_session", "session_id": str(uuid.uuid4())})
            checkpoint = self._read_checkpoint()
            if checkpoint is not None:
                self.checkpoint(cursor=checkpoint["cursor"], state={**checkpoint.get("state", {}),
                                                                     "halt_required": False},
                                applied=checkpoint.get("applied", []))
        else:
            self.working_root.mkdir(parents=True, exist_ok=False)

    def request_safe_halt(self, reason: str) -> None:
        checkpoint = self._read_checkpoint()
        if checkpoint is None:
            raise ValueError("cannot request a safe halt before recovery has been initialized")
        atomic_json(self.safe_halt_path, {"reason": str(reason).strip() or "operator_request",
                                         "requested_at": datetime.now(UTC).isoformat()})

    def clear_safe_halt(self) -> None:
        if self.safe_halt_path.exists():
            self.safe_halt_path.unlink()

    def check_safe_halt(self) -> None:
        if self.deadline is not None and datetime.now(UTC) >= self.deadline:
            self.halt(reason="arm_deadline_exhausted")
            raise RuntimeError("Recovery required: total arm wall-time exhausted")
        if self.max_wall_seconds is not None:
            rows = self._journal_rows()
            if not rows or not isinstance(rows[0].get("started_at"), str):
                raise ValueError("wall-time guard lacks durable experiment start")
            started = datetime.fromisoformat(rows[0]["started_at"])
            if (datetime.now(UTC) - started).total_seconds() >= self.max_wall_seconds:
                self.halt(reason="wall_time_exhausted")
                raise RuntimeError("Recovery required: total experiment wall-time exhausted")
        if not self.safe_halt_path.exists():
            return
        try:
            marker = json.loads(self.safe_halt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("safe-halt marker is unreadable; stop for operator review") from exc
        reason = marker.get("reason", "operator_request") if isinstance(marker, dict) else "operator_request"
        self.halt(reason=f"safe_halt:{reason}")
        raise RuntimeError(f"Safe halt requested: {reason}")

    def progress(self, *, cursor: dict[str, Any], state: dict[str, Any]) -> None:
        checkpoint = self._read_checkpoint()
        self.checkpoint(cursor=cursor, state=state,
                        applied=checkpoint.get("applied", []) if checkpoint else [])

    def attempt_count_total(self) -> int:
        rows = self._journal_rows()
        dispatch_ids = {row.get("attempt_id") for row in rows if row.get("kind") == "dispatch"}
        resolved = {row.get("attempt_id") for row in rows
                    if row.get("kind") == "attempt" and row.get("outcome") in {"blocked", "not_dispatched"}}
        return sum(1 for row in rows if row.get("kind") == "dispatch") + sum(
            1 for row in rows if row.get("kind") == "dispatch_prepared"
            and row.get("attempt_id") not in resolved and row.get("attempt_id") not in dispatch_ids
        )

    def save_initial_snapshot(self) -> None:
        if not self.working_root.is_dir():
            raise ValueError("working tree does not exist")
        snapshot = self.path / "initial_snapshot"
        if snapshot.exists():
            raise FileExistsError("initial snapshot already exists")
        staging = self.path / f".initial_snapshot-{uuid.uuid4().hex}.tmp"
        manifest_staging = self.path / f".snapshot_inventory-{uuid.uuid4().hex}.tmp"
        try:
            shutil.copytree(self.working_root, staging)
            inventory = self._tree_inventory(staging)
            atomic_json(manifest_staging, {"version": 1, "inventory": inventory,
                                           "sha256": canonical_hash(inventory)})
            os.replace(staging, snapshot)
            os.replace(manifest_staging, self.path / "snapshot_inventory.json")
        except BaseException:
            # Keep any incomplete staging tree for safe recovery/inspection.
            raise

    def _validate_input_budget_upgrade(self, approval: dict[str, Any], checkpoint: Any,
                                       *, expected_code: dict[str, str] | None = None) -> None:
        """Validate the human-approved doubling of this run's exhausted input cap."""
        rows = self._journal_rows()
        fourth = [row for row in rows if row.get("kind") == "fourth_cleanup_authorization"]
        if fourth and expected_code is None:
            self._validate_fourth_cleanup(fourth, checkpoint)
            return
        if self.root.name != "luna1912-1018-r2" or sum(
                row.get("kind") == "input_budget_upgrade" for row in rows) > 1:
            raise ValueError("input budget approval applies once to the saved run")
        before, after = approval["from_identity"], approval["to_identity"]
        completions = [row for row in rows if row.get("kind") == "input_budget_manifest_completion"]
        if len(completions) > 1:
            raise ValueError("duplicate input budget completion")
        completion = completions[0] if completions else None
        if completion is not None:
            original = next(row for row in rows if row.get("kind") == "input_budget_upgrade")
            if (completion.get("approval_sha256") != original["sha256"]
                    or completion.get("from_identity") != after
                    or {k: v for k, v in completion["to_identity"].items() if k != "code_sha256"}
                    != {k: v for k, v in after.items() if k != "code_sha256"}):
                raise ValueError("input budget completion binding mismatch")
            after = completion["to_identity"]
        if after != self.identity or checkpoint["identity"] not in (before, approval["to_identity"], after):
            raise ValueError("input budget approval identity mismatch")
        if {k: v for k, v in before.items() if k not in {"configuration_sha256", "code_sha256"}} != {
                k: v for k, v in after.items() if k not in {"configuration_sha256", "code_sha256"}}:
            raise ValueError("input budget approval changes unrelated identity")
        old_config, new_config = approval["source_config"], approval["target_config"]
        expected = json.loads(json.dumps(old_config))
        if expected["budget"]["max_input_tokens"] != 19919602:
            raise ValueError("unexpected original input cap")
        expected["budget"]["max_input_tokens"] *= 2
        if (new_config != expected or canonical_hash(old_config) != before["configuration_sha256"]
                or canonical_hash(new_config) != after["configuration_sha256"]):
            raise ValueError("only doubling the input cap is authorized")
        old_code, new_code = approval["source_code"], approval["target_code"]
        allowed_code_changes = {"runner/recovery.py"}
        if completion is not None:
            if canonical_hash(new_code) != approval["to_identity"]["code_sha256"]:
                raise ValueError("original input budget code binding mismatch")
            completed_code = completion["target_code"]
            if (set(completed_code) != set(new_code)
                    or {k for k in new_code if new_code[k] != completed_code[k]}
                    != {"runner/recovery.py", "population/runner.py"}):
                raise ValueError("input budget completion changes unrelated code")
            new_code = completed_code
            allowed_code_changes.add("population/runner.py")
        source_root = Path(__file__).parents[1]
        actual = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if (canonical_hash(old_code) != before["code_sha256"]
                or canonical_hash(new_code) != after["code_sha256"] or (expected_code if expected_code is not None else actual) != new_code
                or set(old_code) != set(new_code)
                or {k for k in old_code if old_code[k] != new_code[k]} != allowed_code_changes):
            raise ValueError("input budget recovery code binding mismatch")
        count = approval["source_journal_count"]
        if count != 5184 or len(rows) < count or rows[count - 1]["sha256"] != approval["source_journal_head"]:
            raise ValueError("input budget journal prefix mismatch")
        blocked = rows[count - 3]
        if (blocked.get("kind") != "attempt" or blocked.get("outcome") != "blocked"
                or blocked.get("attempt_id") != "5aee8ca5-7562-4684-95fb-4e869453bbbd"
                or rows[count - 1].get("reason") != "budget_blocked"
                or any(row.get("kind") == "dispatch" and row.get("attempt_id") == blocked["attempt_id"]
                       for row in rows[:count])):
            raise ValueError("input budget approval requires the nondispatched budget block")
        source_checkpoint = approval["source_checkpoint"]
        if source_checkpoint["identity"] != before or not source_checkpoint.get("halt_required"):
            raise ValueError("input budget approval lacks halted source checkpoint")
        predecessor = ExperimentRecovery(self.root, identity=before, resume=True)
        predecessor._validate_adjustment_order_correction_code_upgrade(
            source_checkpoint["identity"], expected_after=before)
        ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        if hashlib.sha256(ledger[:approval["source_ledger_bytes"]]).hexdigest() != approval["source_ledger_sha256"]:
            raise ValueError("input budget request ledger prefix mismatch")

    def migrate_input_budget_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        approval = next((row for row in self._journal_rows() if row.get("kind") == "input_budget_upgrade"), None)
        if approval is None:
            return manifest
        self._validate_input_budget_upgrade(approval, self._read_checkpoint())
        if manifest.get("budget") == approval["source_config"]["budget"]:
            manifest = json.loads(json.dumps(manifest))
            manifest["budget"] = approval["target_config"]["budget"]
        return manifest

    def prepare(self) -> dict[str, Any] | None:
        self.build_rule_replay_selector = None
        build_selector = None
        rows = self._journal_rows()
        self._validate_journal_semantics(rows)
        checkpoint = self._read_checkpoint()
        build_transition = next((row for row in rows
                                 if row.get("kind") == "build_rule_replay_transition_v1"), None)
        has_history = self.checkpoint_path.exists() or self.journal_path.exists()
        retreat_approval = next((row for row in rows
                                 if row.get("kind") == "retreat_missing_order_upgrade"), None)
        correction_upgrade = next((row for row in rows
                                   if row.get("kind") == "order_correction_code_upgrade"), None)
        reflection_upgrade = next((row for row in rows
                                   if row.get("kind") == "reflection_correction_code_upgrade"), None)
        negotiation_upgrade = next((row for row in rows
                                    if row.get("kind") == "negotiation_correction_code_upgrade"), None)
        prose_migration = next((row for row in rows
                                if row.get("kind") == "free_text_reflection_migration"), None)
        adjustment_upgrade = next((row for row in rows
                                   if row.get("kind") == "adjustment_order_correction_code_upgrade"), None)
        budget_upgrade = next((row for row in rows if row.get("kind") == "input_budget_upgrade"), None)
        if build_transition is not None:
            if any(item is not None for item in (retreat_approval, correction_upgrade, reflection_upgrade,
                                                  negotiation_upgrade, prose_migration, adjustment_upgrade, budget_upgrade)):
                raise ValueError("build replay transition cannot yet chain historical source upgrades")
            from .build_rule_replay_selector import BuildRuleReplaySelector
            history: dict[str, list[tuple[str, str]]] = {}
            for row in rows[:build_transition["sequence"]]:
                if row.get("kind") == "attempt" and row.get("outcome") == "received":
                    history.setdefault(row["decision_id"], []).append((row["attempt_id"], row["prompt_hash"]))
            boundary = build_transition["boundary"]
            activation = next((row for row in rows if row.get("kind") == "build_rule_activation_v1"), None)
            adoption = next((row for row in rows if row.get("kind") == "build_rule_boundary_adoption_v1"), None)
            build_selector = BuildRuleReplaySelector(
                historical_attempts={key: tuple(value) for key, value in history.items()},
                boundary_decision=boundary["decision_id"], boundary_attempt=boundary["attempt_id"],
                boundary_prompt=boundary["prompt_hash"], amend_boundary=boundary["amend"],
                activation_sequence=activation["sequence"] if activation else None,
                adoption_proof=adoption["proof"] if activation and adoption else None)
        if budget_upgrade is not None:
            self._validate_input_budget_upgrade(budget_upgrade, checkpoint)
        elif adjustment_upgrade is not None:
            self._validate_adjustment_order_correction_code_upgrade(
                checkpoint.get("identity") if checkpoint else None)
        elif prose_migration is not None:
            self._validate_free_text_reflection_migration(checkpoint.get("identity") if checkpoint else None)
        elif checkpoint is not None and negotiation_upgrade is not None:
            self._validate_negotiation_correction_code_upgrade(checkpoint.get("identity"))
        elif checkpoint is not None and reflection_upgrade is not None:
            self._validate_reflection_correction_code_upgrade(checkpoint.get("identity"))
        elif checkpoint is not None and correction_upgrade is not None:
            self._validate_order_correction_code_upgrade(checkpoint.get("identity"))
        elif checkpoint is not None and retreat_approval is not None:
            self._validate_retreat_missing_order_upgrade(checkpoint.get("identity"))
        if self.resume:
            if checkpoint is None:
                bootstrap = (len(rows) == 1 and rows[0].get("kind") == "created"
                             and rows[0].get("identity") == self.identity
                             and not any(row.get("kind") in {"dispatch", "dispatch_prepared", "attempt"}
                                         for row in rows))
                if not bootstrap:
                    raise ValueError("resume requires a valid recovery checkpoint")
                checkpoint = None
                self.needs_bootstrap = True
            elif checkpoint.get("identity") != self.identity and build_transition is None:
                if budget_upgrade is not None:
                    self._validate_input_budget_upgrade(budget_upgrade, checkpoint)
                elif adjustment_upgrade is not None:
                    self._validate_adjustment_order_correction_code_upgrade(checkpoint.get("identity"))
                elif prose_migration is not None:
                    self._validate_free_text_reflection_migration(checkpoint.get("identity"))
                elif negotiation_upgrade is not None:
                    self._validate_negotiation_correction_code_upgrade(checkpoint.get("identity"))
                elif reflection_upgrade is not None:
                    self._validate_reflection_correction_code_upgrade(checkpoint.get("identity"))
                elif correction_upgrade is not None:
                    self._validate_order_correction_code_upgrade(checkpoint.get("identity"))
                elif retreat_approval is not None:
                    self._validate_retreat_missing_order_upgrade(checkpoint.get("identity"))
                else:
                    upgrade = self._relationship_upgrade()
                    if upgrade is None or upgrade["from_identity"] != checkpoint.get("identity"):
                        raise ValueError("resume identity does not match the checkpoint")
                    if upgrade["to_identity"] != self.identity:
                        raise ValueError("relationship approval does not match the current code identity")
            if checkpoint is None:
                try:
                    self._validate_snapshot()
                except ValueError:
                    self.needs_bootstrap = True
                if rows:
                    atomic_json(self.journal_head_path,
                                {"sequence": len(rows) - 1, "sha256": rows[-1]["sha256"]})
                self.checkpoint(cursor={"game_index": 1, "unit": "game_start"}, state={}, applied=[])
                checkpoint = self._read_checkpoint()
                return checkpoint
            try:
                self._validate_snapshot()
            except ValueError:
                if self.attempt_count_total():
                    raise
                self.needs_bootstrap = True
            if not (self.path / "initial_snapshot").exists() and not self.attempt_count_total():
                self.needs_bootstrap = True
            if rows:
                atomic_json(self.journal_head_path,
                            {"sequence": len(rows) - 1, "sha256": rows[-1]["sha256"]})
            if (build_selector is not None and activation is not None and checkpoint.get("cursor", {}).get("unit")
                    in {"game_completed", "population_reports_completed"}):
                build_selector._boundary_crossed = True
            self.build_rule_replay_selector = build_selector
            return checkpoint
        if has_history:
            raise FileExistsError("experiment recovery evidence exists; use explicit resume")
        self.path.mkdir(parents=True, exist_ok=True)
        self._append({"kind": "created", "identity": self.identity, "session_id": str(uuid.uuid4()),
                      "started_at": datetime.now(UTC).isoformat()})
        self.checkpoint(cursor={"game_index": 1, "unit": "game_start"}, state={}, applied=[])
        return None

    def checkpoint(self, *, cursor: dict[str, Any], state: dict[str, Any], applied: list[str]) -> None:
        rows = self._journal_rows()
        if getattr(self, "build_rule_replay_selector", None) is not None:
            transitions = [row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"]
            if len(transitions) != 1:
                raise ValueError("replay checkpoint lacks its unique transition")
            payload = {"kind": "build_rule_replay_checkpoint_v1", "transition_sha256": transitions[0]["sha256"],
                       "identity": self.identity, "cursor": cursor, "state": state, "applied": applied}
            if cursor.get("unit") in {"game_completed", "population_reports_completed"}:
                if not getattr(self.build_rule_replay_selector, "_boundary_crossed", False):
                    raise ValueError("terminal checkpoint requires the adopted boundary to be crossed")
                manifest = self.validate_pending_commit(canonical_root=self.root)
                if manifest is None:
                    raise ValueError("terminal checkpoint requires a durable commit manifest")
                payload["terminal_manifest"] = manifest
            provisional = {**payload, "sequence": len(rows), "previous_hash": rows[-1]["sha256"]}
            provisional["sha256"] = canonical_hash(provisional)
            self._validate_build_rule_replay_checkpoint(provisional, transitions[0], rows)
            self._append(payload)
            rows = self._journal_rows()
        head = rows[-1].get("sha256") if rows else None
        body = {
            "version": RECOVERY_VERSION,
            "identity": self.identity,
            "cursor": cursor,
            "state": state,
            "applied": applied,
            "journal_anchor": {"count": len(rows), "head": head},
        }
        atomic_json(self.checkpoint_path, {**body, "sha256": canonical_hash(body)})

    def commit_tree(self, *, staged_root: Path, canonical_root: Path,
                    cursor: dict[str, Any], state: dict[str, Any], applied: list[str],
                    stop_after_files: int | None = None) -> None:
        self.check_safe_halt()
        staged_root = staged_root.resolve()
        canonical_root = canonical_root.resolve()
        if staged_root == canonical_root or staged_root in canonical_root.parents or canonical_root in staged_root.parents:
            raise ValueError("staged and canonical roots must be disjoint")
        entries: list[dict[str, Any]] = []
        staged_files = sorted(path for path in staged_root.rglob("*") if path.is_file())
        if any(path.is_symlink() for path in staged_root.rglob("*")):
            raise ValueError("staged transaction cannot contain symlinks")
        for source in staged_files:
            relative = source.relative_to(staged_root).as_posix()
            destination = canonical_root / Path(relative)
            entries.append({"path": relative, "before": _file_hash(destination),
                            "after": _file_hash(source), "delete": False})
        for destination in canonical_root.rglob("*") if canonical_root.exists() else ():
            if destination.is_symlink():
                raise ValueError("canonical transaction cannot contain symlinks")
        manifest_body = {"version": 1, "transaction_id": str(uuid.uuid4()),
                         "cursor": cursor, "state": state, "applied": applied,
                         "staged_root": str(staged_root), "canonical_root": str(canonical_root),
                         "entries": entries}
        manifest = {**manifest_body, "sha256": canonical_hash(manifest_body)}
        manifest_path = self.path / "commit_manifest.json"
        if manifest_path.exists():
            raise FileExistsError("a pending tree publication must be resumed before starting another")
        atomic_json(manifest_path, manifest)
        self.validate_pending_commit(canonical_root=canonical_root)
        self._apply_commit(manifest, canonical_root=canonical_root, stop_after_files=stop_after_files)
        if stop_after_files is None:
            self.finish_pending_commit(canonical_root=canonical_root)

    def finish_pending_commit(self, *, canonical_root: Path) -> dict[str, Any] | None:
        self.check_safe_halt()
        manifest_path = self.path / "commit_manifest.json"
        if not manifest_path.exists():
            return None
        manifest = self.validate_pending_commit(canonical_root=canonical_root)
        manifest.pop("sha256", None)
        self._apply_commit(manifest, canonical_root=canonical_root)
        self.checkpoint(cursor=manifest["cursor"], state=manifest["state"], applied=manifest["applied"])
        manifest_path.unlink()
        self._append({"kind": "commit_completed", "transaction_id": manifest["transaction_id"]})
        return manifest

    def validate_pending_commit(self, *, canonical_root: Path) -> dict[str, Any]:
        manifest_path = self.path / "commit_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("pending commit manifest is unreadable") from exc
        digest = manifest.pop("sha256", None) if isinstance(manifest, dict) else None
        if not isinstance(manifest, dict) or digest != canonical_hash(manifest):
            raise ValueError("pending commit manifest checksum is invalid")
        if Path(manifest.get("canonical_root", "")).resolve() != canonical_root.resolve():
            raise ValueError("pending commit targets a different canonical root")
        staged_root = Path(manifest.get("staged_root", "")).resolve()
        if staged_root != self.working_root.resolve() and self.working_root.resolve() not in staged_root.parents:
            raise ValueError("commit manifest staged root escapes recovery storage")
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ValueError("pending commit manifest entries are invalid")
        seen: set[str] = set()
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError("pending commit manifest contains an invalid entry")
            name = row["path"]
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or name in seen:
                raise ValueError("pending commit manifest contains an unsafe or duplicate path")
            seen.add(name)
            if row.get("delete") is not False:
                raise ValueError("pending commit manifest has an unsupported deletion")
            before, after = row.get("before"), row.get("after")
            if before is not None and not isinstance(before, str):
                raise ValueError("pending commit manifest has an invalid pre-hash")
            if not isinstance(after, str):
                raise ValueError("pending commit manifest has an invalid post-hash")
            destination = canonical_root / relative
            current = _file_hash(destination)
            if current not in {before, after}:
                raise ValueError(f"commit target changed outside the transaction: {name}")
            source = staged_root / relative
            if current != after and _file_hash(source) != after:
                raise ValueError(f"staged transaction file changed: {name}")
        return {**manifest, "sha256": digest}

    def _apply_commit(self, manifest: dict[str, Any], *, canonical_root: Path,
                      stop_after_files: int | None = None) -> None:
        canonical_root = canonical_root.resolve()
        staged_root = Path(manifest["staged_root"]).resolve()
        if staged_root != self.working_root.resolve() and self.working_root.resolve() not in staged_root.parents:
            raise ValueError("commit manifest staged root escapes recovery storage")
        for index, row in enumerate(manifest["entries"]):
            if stop_after_files is not None and index >= stop_after_files:
                return
            self.check_safe_halt()
            relative = Path(row["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("commit manifest contains an unsafe path")
            destination = canonical_root / relative
            current = _file_hash(destination)
            if current == row["after"]:
                continue
            if current != row["before"]:
                raise ValueError(f"commit target changed outside the transaction: {row['path']}")
            source = staged_root / relative
            if _file_hash(source) != row["after"]:
                raise ValueError(f"staged transaction file changed: {row['path']}")
            _atomic_copy(source, destination)

    def record_response(self, *, decision_id: str, prompt_hash: str, attempt_id: str,
                        provider: dict[str, Any], response: dict[str, Any] | None,
                        usage: dict[str, Any] | None, error_category: str | None,
                        outcome: str) -> None:
        self._append({
            "kind": "attempt", "decision_id": decision_id, "prompt_hash": prompt_hash,
            "attempt_id": attempt_id, "provider": provider, "response": response,
            "usage": usage, "error_category": error_category, "outcome": outcome,
        })

    def record_prepared(self, *, decision_id: str, prompt_hash: str, attempt_id: str,
                        provider: dict[str, Any]) -> None:
        self._append({"kind": "dispatch_prepared", "decision_id": decision_id,
                      "prompt_hash": prompt_hash, "attempt_id": attempt_id,
                      "provider": provider, "started_at": datetime.now(UTC).isoformat()})

    def assert_prompt_identity(self, decision_id: str, prompt_hash: str) -> None:
        if self.repeat_received_invalid:
            rows = self._journal_rows()
            intents = [r for r in rows if r.get("kind") == "repeat_correction_intent"
                       and r.get("decision_id") == decision_id]
            allowed = set()
            for intent in intents:
                self._validate_repeat_correction(intent, rows)
                allowed.update((intent["parent_prompt_hash"], intent["correction_prompt_hash"]))
            prior = [r for r in rows if r.get("decision_id") == decision_id
                     and r.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}]
            allowed.update(r["prompt_hash"] for r in prior[:1])
            if prior and (prompt_hash not in allowed or any(r["prompt_hash"] not in allowed for r in prior)):
                raise ValueError("repeat correction prompt identity changed")
            return
        italy = self._italy_cleanup_approval()
        if italy and decision_id == italy["decision_id"]:
            self._validate_italy_cleanup(italy, self._read_checkpoint())
            allowed = {italy["base_prompt_hash"]} | {
                r["prompt_hash"] for r in self._journal_rows()
                if r.get("kind") == "italy_cleanup_feedback_intent"}
            if prompt_hash not in allowed:
                raise ValueError("Italy cleanup prompt differs from authorized feedback")
            if any(r.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}
                   and r.get("decision_id") == decision_id and r.get("prompt_hash") not in allowed
                   for r in self._journal_rows()):
                raise ValueError("Italy cleanup history changed prompt identity")
            return
        england = self._england_cleanup_approval()
        if england and decision_id == england["decision_id"]:
            self._validate_england_cleanup(england, self._read_checkpoint())
            allowed = {england["base_prompt_hash"]} | {
                r["prompt_hash"] for r in self._journal_rows()
                if r.get("kind") == "england_cleanup_feedback_intent"}
            if prompt_hash not in allowed:
                raise ValueError("England cleanup prompt differs from authorized feedback")
            if any(r.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}
                   and r.get("decision_id") == decision_id and r.get("prompt_hash") not in allowed
                   for r in self._journal_rows()):
                raise ValueError("England cleanup history changed prompt identity")
            return
        russia = self._russia_cleanup_approval()
        if russia and decision_id == russia["decision_id"]:
            self._validate_russia_cleanup(russia, self._read_checkpoint())
            allowed = {russia["base_prompt_hash"]} | {
                r["prompt_hash"] for r in self._journal_rows()
                if r.get("kind") == "russia_cleanup_feedback_intent"}
            if prompt_hash not in allowed:
                raise ValueError("Russia cleanup prompt differs from authorized feedback")
            if any(r.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}
                   and r.get("decision_id") == decision_id and r.get("prompt_hash") not in allowed
                   for r in self._journal_rows()):
                raise ValueError("Russia cleanup history changed prompt identity")
            return
        intents = [row for row in self._journal_rows()
                   if row.get("kind") == "order_correction_intent"
                   and row.get("decision_id") == decision_id]
        allowed_hashes = {value for row in intents
                          for value in (row.get("base_prompt_hash"), row.get("correction_prompt_hash"))}
        cleanup = self._cleanup_two_pass_approval()
        if cleanup and decision_id == cleanup["decision_id"]:
            self._validate_cleanup_two_pass(cleanup, self._read_checkpoint())
            allowed_hashes.add(cleanup["base_prompt_hash"])
            allowed_hashes.update(r["prompt_hash"] for r in self._journal_rows()
                                  if r.get("kind") == "cleanup_feedback_intent")
            if prompt_hash not in allowed_hashes:
                raise ValueError("cleanup prompt differs from reviewed feedback")
        if intents and prompt_hash not in allowed_hashes:
            raise ValueError("recovery prompt identity differs from the bound correction")
        for row in self._journal_rows():
            if (row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}
                    and row.get("decision_id") == decision_id
                    and row.get("prompt_hash") != prompt_hash
                    and row.get("prompt_hash") not in allowed_hashes):
                raise ValueError("recovery prompt identity changed for an existing decision")

    def _validate_repeat_correction(self, intent: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        prior = [r for r in rows if r["sequence"] < intent.get("sequence", len(rows))
                 and r.get("decision_id") == intent["decision_id"]]
        attempts = [r for r in prior if r.get("kind") == "attempt"]
        validations = [r for r in prior if r.get("kind") == "response_validation"
                       and r.get("attempt_id") == intent.get("parent_attempt_id")]
        if not attempts or not validations:
            raise ValueError("repeat correction lacks its actual parent and validation")
        parent, validation = attempts[-1], validations[-1]
        feedback = intent.get("feedback", {})
        if (parent.get("outcome") != "received" or validation.get("valid") is not False
                or parent.get("attempt_id") != intent.get("parent_attempt_id")
                or parent.get("prompt_hash") != intent.get("parent_prompt_hash")
                or canonical_hash(parent.get("response")) != intent.get("parent_response_hash")
                or canonical_hash(validation) != intent.get("parent_validation_hash")
                or feedback.get("rejected_answer") != (parent.get("response") or {}).get("content")
                or feedback.get("validator_issue") != validation.get("issue")
                or canonical_hash(feedback) != intent.get("feedback_hash")
                or any(r.get("kind") == "repeat_correction_intent" and
                       (r.get("parent_attempt_id") == intent.get("parent_attempt_id") or
                        r.get("correction_prompt_hash") == intent.get("correction_prompt_hash")) for r in prior)
                or intent.get("correction_prompt_hash") == intent.get("parent_prompt_hash")):
            raise ValueError("repeat correction differs from actual received-invalid feedback")

    def prepare_repeat_correction(self, *, decision_id: str, parent_prompt_hash: str,
                                  feedback: dict[str, Any], correction_prompt_hash: str) -> dict[str, Any]:
        if not self.repeat_received_invalid:
            raise ValueError("repeat correction policy is not enabled")
        rows = self._journal_rows()
        parent = self.cached_response(decision_id, parent_prompt_hash)
        if parent is None or self.is_abandoned(decision_id):
            raise ValueError("repeat correction requires a settled cached parent")
        existing = [r for r in rows if r.get("kind") == "repeat_correction_intent"
                    and r.get("decision_id") == decision_id and r.get("parent_attempt_id") == parent["attempt_id"]]
        if existing:
            intent = existing[0]
            self._validate_repeat_correction(intent, rows)
            if intent["feedback"] != feedback or intent["correction_prompt_hash"] != correction_prompt_hash:
                raise ValueError("repeat correction intent changed")
            return intent
        if self.in_flight():
            raise ValueError("repeat correction cannot replace an unresolved original")
        validations = [r for r in rows if r.get("kind") == "response_validation"
                       and r.get("decision_id") == decision_id and r.get("attempt_id") == parent["attempt_id"]]
        if not validations:
            raise ValueError("repeat correction lacks validation")
        intent = dict(kind="repeat_correction_intent", decision_id=decision_id,
            parent_prompt_hash=parent_prompt_hash, parent_attempt_id=parent["attempt_id"],
            parent_response_hash=canonical_hash(parent.get("response")),
            parent_validation_hash=canonical_hash(validations[-1]), feedback=feedback,
            feedback_hash=canonical_hash(feedback), correction_prompt_hash=correction_prompt_hash)
        self._validate_repeat_correction(intent, rows)
        self._append(intent)
        return self._journal_rows()[-1]

    def prepare_order_correction(self, *, decision_id: str, base_prompt_hash: str,
                                 feedback: dict[str, Any], correction_prompt_hash: str,
                                 method: str = "submit_orders") -> dict[str, Any]:
        """Persist one exact, one-request correction bound to the received parent answer."""
        if self.repeat_received_invalid:
            validations = [r for r in self._journal_rows() if r.get("kind") == "response_validation"
                           and r.get("decision_id") == decision_id
                           and r.get("attempt_id") == feedback.get("parent_attempt_id") and r.get("valid") is False]
            if not validations:
                raise ValueError("repeat correction lacks rejection")
            return self.prepare_repeat_correction(decision_id=decision_id,
                parent_prompt_hash=validations[-1]["prompt_hash"], feedback=feedback,
                correction_prompt_hash=correction_prompt_hash)
        if method not in {"submit_orders", "reflect", "negotiate_tick"} or decision_id.rsplit("/", 1)[-1] != method:
            raise ValueError("correction method must match its supported logical decision")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "order_correction_intent"
                    and row.get("decision_id") == decision_id]
        parent = self.cached_response(decision_id, base_prompt_hash)
        if parent is None or parent.get("outcome") != "received":
            raise ValueError("order correction requires the exact cached parent response")
        if self.is_abandoned(decision_id):
            raise ValueError("order correction is unavailable for an abandoned decision")
        parent_validations = [row for row in rows if row.get("kind") == "response_validation"
                              and row.get("decision_id") == decision_id
                              and row.get("prompt_hash") == base_prompt_hash
                              and row.get("attempt_id") == parent["attempt_id"]
                              and row.get("valid") is False]
        if not parent_validations:
            raise ValueError("order correction requires a durable rejected-answer validation")
        parent_validation = parent_validations[-1]
        payload = {
            "kind": "order_correction_intent", "decision_id": decision_id,
            "method": method,
            "base_prompt_hash": base_prompt_hash, "parent_attempt_id": parent["attempt_id"],
            "parent_response_hash": canonical_hash(parent.get("response")),
            "parent_validation_hash": canonical_hash(parent_validation),
            "feedback": feedback, "feedback_hash": canonical_hash(feedback),
            "correction_prompt_hash": correction_prompt_hash,
        }
        if existing:
            if len(existing) != 1 or any(
                    existing[0].get(key, "submit_orders" if key == "method" else None) != value
                    for key, value in payload.items() if key != "kind"):
                raise ValueError("order correction intent changed or was already used")
            return existing[0]
        if self.attempt_count(decision_id) >= 3:
            raise ValueError("order correction would exceed the three-request decision limit")
        self._append(payload)
        return self._journal_rows()[-1]

    def order_correction_intent(self, decision_id: str) -> dict[str, Any] | None:
        rows = [row for row in self._journal_rows() if row.get("kind") == "order_correction_intent"
                and row.get("decision_id") == decision_id]
        if len(rows) > 1:
            raise ValueError("multiple order corrections are unsupported for one decision")
        return rows[0] if rows else None

    def _relationship_upgrade(self) -> dict[str, Any] | None:
        rows = self._journal_rows()
        upgrades = [row for row in rows if row.get("kind") == "relationship_limit_upgrade"]
        if not upgrades:
            return None
        if len(upgrades) != 1:
            raise ValueError("multiple relationship compatibility approvals are unsupported")
        upgrade = upgrades[0]
        before, after = upgrade.get("from_identity"), upgrade.get("to_identity")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("relationship compatibility approval has invalid identities")
        if ({k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in after.items() if k != "code_sha256"}):
            raise ValueError("relationship compatibility approval changed configuration or data")
        received = [row for row in rows[:upgrade["sequence"]]
                    if row.get("kind") == "attempt" and row.get("outcome") == "received"]
        expected = {row["decision_id"]: row["prompt_hash"] for row in received}
        if (upgrade.get("legacy_prompts") != expected
                or upgrade.get("source_journal_head") != upgrade["previous_hash"]
                or upgrade.get("response_hashes") != {row["attempt_id"]: canonical_hash(row["response"])
                                                       for row in received}):
            raise ValueError("relationship compatibility approval does not match saved responses")
        approved = upgrade.get("revalidated_attempts")
        if not isinstance(approved, list) or set(approved) != {row["attempt_id"] for row in received}:
            raise ValueError("relationship compatibility approval requires every saved response")
        return upgrade

    def _validate_retreat_missing_order_upgrade(self, checkpoint_identity: Any = None,
                                                expected_after: Any = None) -> dict[str, Any]:
        """Verify the exact, append-only code transition for the forced-retreat fix."""
        rows = self._journal_rows()
        approvals = [row for row in rows if row.get("kind") == "retreat_missing_order_upgrade"]
        relationship = self._relationship_upgrade()
        if len(approvals) != 1 or relationship is None:
            raise ValueError("resume identity does not match the checkpoint")
        approval = approvals[0]
        before, after = approval.get("from_identity"), approval.get("to_identity")
        relation_to = relationship.get("to_identity")
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before != relation_to or after != (self.identity if expected_after is None else expected_after)
                or checkpoint_identity is not None
                and checkpoint_identity != before and checkpoint_identity != after):
            raise ValueError("retreat compatibility approval is not a direct relationship-upgrade continuation")
        stable = {key: value for key, value in before.items() if key != "code_sha256"}
        if ({key: value for key, value in after.items() if key != "code_sha256"} != stable
                or {key: value for key, value in relationship["from_identity"].items()
                    if key != "code_sha256"} != stable):
            raise ValueError("retreat compatibility approval changed configuration or execution data")
        code_before, code_after = approval.get("source_code_manifest"), approval.get("target_code_manifest")
        if not isinstance(code_before, dict) or not isinstance(code_after, dict):
            raise ValueError("retreat compatibility approval lacks complete source manifests")
        allowed = {"runner/scripted_experiment_runner.py", "runner/recovery.py"}
        if (canonical_hash(code_before) != before.get("code_sha256")
                or canonical_hash(code_after) != after.get("code_sha256")
                or set(code_before) != set(code_after)):
            raise ValueError("retreat compatibility code manifests do not match the identities")
        changed = {name for name in code_before if code_before[name] != code_after[name]}
        if not changed or not changed <= allowed or approval.get("changed_code_paths") != sorted(changed):
            raise ValueError("retreat compatibility changed files outside the reviewed allowlist")
        sequence = approval.get("sequence")
        if not isinstance(sequence, int) or sequence >= len(rows):
            raise ValueError("retreat compatibility journal position is invalid")
        source_rows = rows[:sequence]
        if (approval.get("source_journal_head") != approval.get("previous_hash")
                or not source_rows or source_rows[-1].get("sha256") != approval.get("source_journal_head")
                or approval.get("source_journal_count") != len(source_rows)):
            raise ValueError("retreat compatibility journal anchor is invalid")
        source_checkpoint = approval.get("source_checkpoint")
        if (not isinstance(source_checkpoint, dict)
                or source_checkpoint.get("identity") != before
                or source_checkpoint.get("cursor") != {"unit": "phase_started", "game_id": "game_001",
                                                       "phase_id": "1902_spring_retreat"}
                or not source_checkpoint.get("halt_required")
                or source_checkpoint.get("halt_reason") != "orders_rejected_or_substituted"):
            raise ValueError("retreat compatibility is not bound to the reviewed halted phase")
        checkpoint_body = {key: value for key, value in source_checkpoint.items() if key != "sha256"}
        if source_checkpoint.get("sha256") != canonical_hash(checkpoint_body):
            raise ValueError("retreat compatibility source checkpoint checksum is invalid")
        anchor = source_checkpoint.get("journal_anchor")
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int)
                or anchor["count"] > len(source_rows)
                or (source_rows[anchor["count"] - 1]["sha256"] if anchor["count"] else None)
                != anchor.get("head")):
            raise ValueError("retreat compatibility source checkpoint anchor is invalid")
        if approval.get("source_snapshot_inventory_sha256") != _file_hash(self.path / "snapshot_inventory.json"):
            raise ValueError("retreat compatibility snapshot inventory changed")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("retreat compatibility applies only to the reviewed single-game run")
        retreat_attempts = approval.get("retreat_attempts")
        if not isinstance(retreat_attempts, dict) or len(retreat_attempts) != 7:
            raise ValueError("retreat compatibility does not bind all seven saved decisions")
        received = {
            row["decision_id"]: {"attempt_id": row["attempt_id"], "prompt_hash": row["prompt_hash"],
                                 "response_hash": canonical_hash(row.get("response"))}
            for row in source_rows if row.get("kind") == "attempt" and row.get("outcome") == "received"
            and row.get("decision_id", "").startswith("game_001/1902_spring_retreat/")
        }
        if received != retreat_attempts:
            raise ValueError("retreat compatibility saved responses differ from the reviewed source")
        response_hashes = {
            row["attempt_id"]: canonical_hash(row.get("response"))
            for row in source_rows if row.get("kind") == "attempt" and row.get("outcome") == "received"
        }
        if response_hashes != approval.get("source_response_hashes"):
            raise ValueError("retreat compatibility does not bind every received response")
        skipped = sorted(row["attempt_id"] for row in source_rows if row.get("kind") == "response_skipped")
        if skipped != approval.get("preserved_skipped_attempts"):
            raise ValueError("retreat compatibility skipped-response history changed")
        validation_hashes = approval.get("historical_validation_hashes")
        expected_validations = {
            row["attempt_id"]: canonical_hash(row)
            for row in source_rows if row.get("kind") == "response_validation"
            and row.get("valid") is False
            and row.get("decision_id") in retreat_attempts
        }
        if validation_hashes != expected_validations:
            raise ValueError("retreat compatibility does not preserve the existing retreat rejections")
        return approval

    def _validate_order_correction_code_upgrade(self, checkpoint_identity: Any = None) -> dict[str, Any]:
        return self._validate_order_correction_upgrade(checkpoint_identity=checkpoint_identity,
                                                        expected_after=self.identity)

    def _validate_order_correction_upgrade(self, *, checkpoint_identity: Any = None,
                                           expected_after: Any = None) -> dict[str, Any]:
        rows = self._journal_rows()
        approvals = [row for row in rows if row.get("kind") == "order_correction_code_upgrade"]
        if len(approvals) != 1:
            raise ValueError("order-correction compatibility approval is missing or duplicated")
        approval = approvals[0]
        before, after = approval.get("from_identity"), approval.get("to_identity")
        retreat = self._validate_retreat_missing_order_upgrade(
            checkpoint_identity=approval.get("source_checkpoint", {}).get("identity"),
            expected_after=before,
        )
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before != retreat.get("to_identity") or after != (self.identity if expected_after is None else expected_after)
                or checkpoint_identity is not None and checkpoint_identity != before and checkpoint_identity != after):
            raise ValueError("order-correction approval does not continue the reviewed retreat identity")
        stable = {key: value for key, value in before.items() if key != "code_sha256"}
        if {key: value for key, value in after.items() if key != "code_sha256"} != stable:
            raise ValueError("order-correction approval changed configuration or execution data")
        source_manifest, target_manifest = approval.get("source_code_manifest"), approval.get("target_code_manifest")
        if (not isinstance(source_manifest, dict) or not isinstance(target_manifest, dict)
                or source_manifest != retreat.get("target_code_manifest")
                or canonical_hash(source_manifest) != before.get("code_sha256")
                or canonical_hash(target_manifest) != after.get("code_sha256")
                or set(source_manifest) != set(target_manifest)):
            raise ValueError("order-correction package manifests do not match the chained identities")
        changed = {name for name in source_manifest if source_manifest[name] != target_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/scripted_experiment_runner.py", "runner/recovery.py"}
        if (not changed or not changed <= allowed or approval.get("changed_code_paths") != sorted(changed)):
            raise ValueError("order-correction code changed outside the reviewed allowlist")
        sequence = approval.get("sequence")
        if (not isinstance(sequence, int) or sequence >= len(rows) or sequence < 1
                or approval.get("source_journal_count") != sequence
                or approval.get("source_journal_head") != approval.get("previous_hash")
                or rows[sequence - 1].get("sha256") != approval.get("source_journal_head")):
            raise ValueError("order-correction journal anchor is invalid")
        source_rows = rows[:sequence]
        source_checkpoint = approval.get("source_checkpoint")
        if (not isinstance(source_checkpoint, dict) or source_checkpoint.get("identity") != before
                or source_checkpoint.get("sha256") != canonical_hash(
                    {key: value for key, value in source_checkpoint.items() if key != "sha256"})
                or approval.get("source_snapshot_inventory_sha256") != _file_hash(self.path / "snapshot_inventory.json")):
            raise ValueError("order-correction source checkpoint or snapshot binding is invalid")
        source_identity = source_checkpoint.get("identity")
        if source_identity != before:
            raise ValueError("order-correction source checkpoint identity changed")
        anchor = source_checkpoint.get("journal_anchor")
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int)
                or anchor["count"] > len(source_rows)
                or (source_rows[anchor["count"] - 1]["sha256"] if anchor["count"] else None)
                != anchor.get("head")):
            raise ValueError("order-correction source checkpoint journal anchor is invalid")
        response_hashes = {row["attempt_id"]: canonical_hash(row.get("response")) for row in source_rows
                           if row.get("kind") == "attempt" and row.get("outcome") == "received"}
        prompt_hashes = {row["attempt_id"]: row["prompt_hash"] for row in source_rows
                         if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}}
        skipped = sorted(row["attempt_id"] for row in source_rows if row.get("kind") == "response_skipped")
        validation_hashes = [canonical_hash(row) for row in source_rows if row.get("kind") == "response_validation"]
        if (approval.get("source_response_hashes") != response_hashes
                or approval.get("source_prompt_hashes") != prompt_hashes
                or approval.get("preserved_skipped_attempts") != skipped
                or approval.get("source_validation_hashes") != validation_hashes):
            raise ValueError("order-correction approval does not preserve the saved request history")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("order-correction approval applies only to the reviewed run")
        return approval

    def _validate_reflection_correction_code_upgrade(self, checkpoint_identity: Any = None,
                                                     expected_after: Any = None) -> dict[str, Any]:
        rows = self._journal_rows()
        approvals = [row for row in rows if row.get("kind") == "reflection_correction_code_upgrade"]
        if len(approvals) != 1:
            raise ValueError("reflection-correction compatibility approval is missing or duplicated")
        approval = approvals[0]
        before, after = approval.get("from_identity"), approval.get("to_identity")
        order = self._validate_order_correction_upgrade(
            checkpoint_identity=approval.get("source_checkpoint", {}).get("identity"), expected_after=before)
        current_target = self.identity if expected_after is None else expected_after
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before != order.get("to_identity") or after != current_target
                or checkpoint_identity is not None and checkpoint_identity != before and checkpoint_identity != after):
            raise ValueError("reflection-correction approval does not continue the order-correction identity")
        stable = {key: value for key, value in before.items() if key != "code_sha256"}
        if {key: value for key, value in after.items() if key != "code_sha256"} != stable:
            raise ValueError("reflection-correction approval changed configuration or execution data")
        source_manifest, target_manifest = approval.get("source_code_manifest"), approval.get("target_code_manifest")
        if (not isinstance(source_manifest, dict) or not isinstance(target_manifest, dict)
                or source_manifest != order.get("target_code_manifest")
                or canonical_hash(source_manifest) != before.get("code_sha256")
                or canonical_hash(target_manifest) != after.get("code_sha256")
                or set(source_manifest) != set(target_manifest)):
            raise ValueError("reflection-correction package manifests do not match the chained identities")
        changed = {name for name in source_manifest if source_manifest[name] != target_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/recovery.py"}
        if not changed or not changed <= allowed or approval.get("changed_code_paths") != sorted(changed):
            raise ValueError("reflection-correction code changed outside the reviewed allowlist")
        sequence = approval.get("sequence")
        if (not isinstance(sequence, int) or sequence >= len(rows) or sequence < 1
                or approval.get("source_journal_count") != sequence
                or approval.get("source_journal_head") != approval.get("previous_hash")
                or rows[sequence - 1].get("sha256") != approval.get("source_journal_head")):
            raise ValueError("reflection-correction journal anchor is invalid")
        source_rows = rows[:sequence]
        source_checkpoint = approval.get("source_checkpoint")
        if (not isinstance(source_checkpoint, dict) or source_checkpoint.get("identity") != before
                or source_checkpoint.get("sha256") != canonical_hash(
                    {key: value for key, value in source_checkpoint.items() if key != "sha256"})
                or approval.get("source_snapshot_inventory_sha256") != _file_hash(self.path / "snapshot_inventory.json")):
            raise ValueError("reflection-correction checkpoint or snapshot binding is invalid")
        anchor = source_checkpoint.get("journal_anchor")
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int)
                or anchor["count"] > len(source_rows)
                or (source_rows[anchor["count"] - 1]["sha256"] if anchor["count"] else None) != anchor.get("head")):
            raise ValueError("reflection-correction source journal anchor is invalid")
        response_hashes = {row["attempt_id"]: canonical_hash(row.get("response")) for row in source_rows
                           if row.get("kind") == "attempt" and row.get("outcome") == "received"}
        prompt_hashes = {row["attempt_id"]: row["prompt_hash"] for row in source_rows
                         if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}}
        skipped = sorted(row["attempt_id"] for row in source_rows if row.get("kind") == "response_skipped")
        validation_hashes = [canonical_hash(row) for row in source_rows if row.get("kind") == "response_validation"]
        if (approval.get("source_response_hashes") != response_hashes
                or approval.get("source_prompt_hashes") != prompt_hashes
                or approval.get("preserved_skipped_attempts") != skipped
                or approval.get("source_validation_hashes") != validation_hashes):
            raise ValueError("reflection-correction approval does not preserve the saved request history")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("reflection-correction approval applies only to the reviewed run")
        return approval

    def _validate_negotiation_correction_code_upgrade(self, checkpoint_identity: Any = None,
                                                      expected_after: Any = None) -> dict[str, Any]:
        rows = self._journal_rows()
        approvals = [row for row in rows if row.get("kind") == "negotiation_correction_code_upgrade"]
        if len(approvals) != 1:
            raise ValueError("negotiation-correction compatibility approval is missing or duplicated")
        approval = approvals[0]
        before, after = approval.get("from_identity"), approval.get("to_identity")
        reflection = self._validate_reflection_correction_code_upgrade(
            checkpoint_identity=approval.get("source_checkpoint", {}).get("identity"),
            expected_after=before)
        current_target = self.identity if expected_after is None else expected_after
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before != reflection.get("to_identity") or after != current_target
                or checkpoint_identity is not None and checkpoint_identity != before and checkpoint_identity != after):
            raise ValueError("negotiation-correction approval does not continue the reflection-correction identity")
        stable = {key: value for key, value in before.items() if key != "code_sha256"}
        if {key: value for key, value in after.items() if key != "code_sha256"} != stable:
            raise ValueError("negotiation-correction approval changed configuration or execution data")
        source_manifest, target_manifest = approval.get("source_code_manifest"), approval.get("target_code_manifest")
        if (not isinstance(source_manifest, dict) or not isinstance(target_manifest, dict)
                or source_manifest != reflection.get("target_code_manifest")
                or canonical_hash(source_manifest) != before.get("code_sha256")
                or canonical_hash(target_manifest) != after.get("code_sha256")
                or set(source_manifest) != set(target_manifest)):
            raise ValueError("negotiation-correction package manifests do not match the chained identities")
        changed = {name for name in source_manifest if source_manifest[name] != target_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/recovery.py"}
        if not changed or not changed <= allowed or approval.get("changed_code_paths") != sorted(changed):
            raise ValueError("negotiation-correction code changed outside the reviewed allowlist")
        sequence = approval.get("sequence")
        if (not isinstance(sequence, int) or sequence >= len(rows) or sequence < 1
                or approval.get("source_journal_count") != sequence
                or approval.get("source_journal_head") != approval.get("previous_hash")
                or rows[sequence - 1].get("sha256") != approval.get("source_journal_head")):
            raise ValueError("negotiation-correction journal anchor is invalid")
        source_rows = rows[:sequence]
        source_checkpoint = approval.get("source_checkpoint")
        if (not isinstance(source_checkpoint, dict) or source_checkpoint.get("identity") != before
                or source_checkpoint.get("sha256") != canonical_hash(
                    {key: value for key, value in source_checkpoint.items() if key != "sha256"})
                or approval.get("source_snapshot_inventory_sha256") != _file_hash(self.path / "snapshot_inventory.json")):
            raise ValueError("negotiation-correction checkpoint or snapshot binding is invalid")
        anchor = source_checkpoint.get("journal_anchor")
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int)
                or anchor["count"] > len(source_rows)
                or (source_rows[anchor["count"] - 1]["sha256"] if anchor["count"] else None) != anchor.get("head")):
            raise ValueError("negotiation-correction source journal anchor is invalid")
        response_hashes = {row["attempt_id"]: canonical_hash(row.get("response")) for row in source_rows
                           if row.get("kind") == "attempt" and row.get("outcome") == "received"}
        prompt_hashes = {row["attempt_id"]: row["prompt_hash"] for row in source_rows
                         if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}}
        skipped = sorted(row["attempt_id"] for row in source_rows if row.get("kind") == "response_skipped")
        validation_hashes = [canonical_hash(row) for row in source_rows if row.get("kind") == "response_validation"]
        if (approval.get("source_response_hashes") != response_hashes
                or approval.get("source_prompt_hashes") != prompt_hashes
                or approval.get("preserved_skipped_attempts") != skipped
                or approval.get("source_validation_hashes") != validation_hashes):
            raise ValueError("negotiation-correction approval does not preserve the saved request history")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("negotiation-correction approval applies only to the reviewed run")
        return approval

    def _validate_free_text_reflection_migration(self, checkpoint_identity: Any = None,
                                                 expected_after: Any = None) -> dict[str, Any]:
        rows = self._journal_rows()
        approvals = [row for row in rows if row.get("kind") == "free_text_reflection_migration"]
        if len(approvals) != 1:
            raise ValueError("free-text reflection migration approval is missing or duplicated")
        approval = approvals[0]
        before, after = approval.get("from_identity"), approval.get("to_identity")
        negotiation = self._validate_negotiation_correction_code_upgrade(
            checkpoint_identity=approval.get("source_checkpoint", {}).get("identity"),
            expected_after=before)
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before != negotiation.get("to_identity")
                or after != (self.identity if expected_after is None else expected_after)
                or checkpoint_identity is not None and checkpoint_identity not in
                (before, after, canonical_hash(before), canonical_hash(after))):
            raise ValueError("free-text migration does not continue the negotiation correction identity")
        if ({key: value for key, value in before.items() if key != "code_sha256"}
                != {key: value for key, value in after.items() if key != "code_sha256"}):
            raise ValueError("free-text migration changed configuration or execution data")
        source_manifest, target_manifest = approval.get("source_code_manifest"), approval.get("target_code_manifest")
        if (not isinstance(source_manifest, dict) or not isinstance(target_manifest, dict)
                or source_manifest != negotiation.get("target_code_manifest")
                or canonical_hash(source_manifest) != before.get("code_sha256")
                or canonical_hash(target_manifest) != after.get("code_sha256")
                or set(source_manifest) != set(target_manifest)):
            raise ValueError("free-text migration package manifests do not match the chained identities")
        changed = {name for name in source_manifest if source_manifest[name] != target_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "agents/llm_context.py", "population/runner.py",
                   "runner/scripted_experiment_runner.py", "runner/recovery.py"}
        if not changed or not changed <= allowed or approval.get("changed_code_paths") != sorted(changed):
            raise ValueError("free-text migration changed files outside the reviewed allowlist")
        sequence = approval.get("sequence")
        if (not isinstance(sequence, int) or sequence < 1 or sequence >= len(rows)
                or approval.get("source_journal_count") != sequence
                or approval.get("source_journal_head") != approval.get("previous_hash")
                or rows[sequence - 1].get("sha256") != approval.get("source_journal_head")):
            raise ValueError("free-text migration journal anchor is invalid")
        source_rows = rows[:sequence]
        checkpoint = approval.get("source_checkpoint")
        if (not isinstance(checkpoint, dict) or checkpoint.get("identity") != before
                or checkpoint.get("sha256") != canonical_hash(
                    {key: value for key, value in checkpoint.items() if key != "sha256"})
                or approval.get("source_snapshot_inventory_sha256") != _file_hash(self.path / "snapshot_inventory.json")):
            raise ValueError("free-text migration checkpoint or snapshot binding is invalid")
        anchor = checkpoint.get("journal_anchor")
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int)
                or anchor["count"] > len(source_rows)
                or (source_rows[anchor["count"] - 1]["sha256"] if anchor["count"] else None)
                != anchor.get("head")):
            raise ValueError("free-text migration checkpoint journal anchor is invalid")
        if (approval.get("source_response_hashes") != {
                row["attempt_id"]: canonical_hash(row.get("response")) for row in source_rows
                if row.get("kind") == "attempt" and row.get("outcome") == "received"}
                or approval.get("source_prompt_hashes") != {
                    row["attempt_id"]: row["prompt_hash"] for row in source_rows
                    if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}}
                or approval.get("preserved_skipped_attempts") != sorted(
                    row["attempt_id"] for row in source_rows if row.get("kind") == "response_skipped")
                or approval.get("source_validation_hashes") != [canonical_hash(row) for row in source_rows
                    if row.get("kind") == "response_validation"]):
            raise ValueError("free-text migration does not preserve saved request history")
        if approval.get("historical_prompt_hashes") != self._historical_prompt_hashes(source_rows):
            raise ValueError("free-text migration historical prompt map differs from saved requests")
        adoption = approval.get("adoption")
        expected_decision = "game_001/1903_fall_retreat/once/germany/shaver/reflect"
        if (not isinstance(adoption, dict) or adoption.get("decision_id") != expected_decision
                or adoption.get("protocol") != "free_text_reflection_v1"
                or adoption.get("attempt_id") != "db7b6fab-7972-4554-9768-5db6d5ebb035"
                or adoption.get("prompt_hash") != "b81704cd87c4973624edabf849d44db08e3bb2ea24448a0c906cabf60c897e9e"
                or adoption.get("content_sha256") != "a56d54f36e2905bf1482862488132651f005fb7b92fea084817213f8de755460"):
            raise ValueError("free-text migration adoption identity is invalid")
        attempt = next((row for row in source_rows if row.get("kind") == "attempt"
                        and row.get("attempt_id") == adoption.get("attempt_id")
                        and row.get("decision_id") == expected_decision
                        and row.get("outcome") == "received"), None)
        if (attempt is None or attempt.get("prompt_hash") != adoption.get("prompt_hash")
                or canonical_hash(attempt.get("response")) != adoption.get("response_hash")
                or not isinstance(attempt.get("response"), dict)
                or hashlib.sha256(str(attempt["response"].get("content", "")).encode("utf-8")).hexdigest()
                != adoption.get("content_sha256")):
            raise ValueError("free-text migration adoption does not match the exact saved provider response")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("free-text migration applies only to the reviewed run")
        return approval

    def _validate_adjustment_order_correction_code_upgrade(
            self, checkpoint_identity: Any = None, expected_after: Any = None) -> dict[str, Any]:
        rows = self._journal_rows()
        budget = next((row for row in rows if row.get("kind") == "input_budget_upgrade"), None)
        if budget is not None and expected_after is None:
            self._validate_input_budget_upgrade(budget, self._read_checkpoint())
            fourth = next((row for row in rows if row.get("kind") == "fourth_cleanup_authorization"), None)
            cleanup = self._cleanup_two_pass_approval()
            russia = self._russia_cleanup_approval()
            england = self._england_cleanup_approval()
            italy = self._italy_cleanup_approval()
            if checkpoint_identity is not None and checkpoint_identity not in (
                    budget["from_identity"], budget["to_identity"], self.identity,
                    fourth["from_identity"] if fourth else None,
                    cleanup["from_identity"] if cleanup else None,
                    russia["from_identity"] if russia else None,
                    england["from_identity"] if england else None,
                    italy["from_identity"] if italy else None):
                raise ValueError("budget checkpoint identity mismatch")
            checkpoint_identity = budget["from_identity"] if checkpoint_identity is not None else None
            expected_after = budget["from_identity"]
        approvals = [row for row in rows if row.get("kind") == "adjustment_order_correction_code_upgrade"]
        if len(approvals) != 1:
            raise ValueError("adjustment-correction approval is missing or duplicated")
        approval = approvals[0]
        before, after = approval.get("from_identity"), approval.get("to_identity")
        checkpoint = approval.get("source_checkpoint")
        if not isinstance(checkpoint, dict):
            raise ValueError("adjustment-correction approval lacks its source checkpoint")
        predecessor = self._validate_free_text_reflection_migration(
            checkpoint_identity=checkpoint.get("identity"), expected_after=before)
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before != predecessor.get("to_identity")
                or after != (self.identity if expected_after is None else expected_after)
                or checkpoint_identity is not None and checkpoint_identity not in
                (before, after, canonical_hash(before), canonical_hash(after))):
            raise ValueError("adjustment-correction approval does not continue the accepted prose migration")
        stable = {key: value for key, value in before.items() if key != "code_sha256"}
        if {key: value for key, value in after.items() if key != "code_sha256"} != stable:
            raise ValueError("adjustment-correction approval changed configuration or execution data")
        source_manifest, target_manifest = approval.get("source_code_manifest"), approval.get("target_code_manifest")
        if (not isinstance(source_manifest, dict) or not isinstance(target_manifest, dict)
                or source_manifest != predecessor.get("target_code_manifest")
                or canonical_hash(source_manifest) != before.get("code_sha256")
                or canonical_hash(target_manifest) != after.get("code_sha256")
                or set(source_manifest) != set(target_manifest)):
            raise ValueError("adjustment-correction package manifests do not match the chained identities")
        changed = {name for name in source_manifest if source_manifest[name] != target_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/recovery.py"}
        if not changed or not changed <= allowed or approval.get("changed_code_paths") != sorted(changed):
            raise ValueError("adjustment-correction changed files outside the reviewed allowlist")
        sequence = approval.get("sequence")
        if (not isinstance(sequence, int) or sequence < 1 or sequence >= len(rows)
                or approval.get("source_journal_count") != sequence
                or approval.get("source_journal_head") != approval.get("previous_hash")
                or rows[sequence - 1].get("sha256") != approval.get("source_journal_head")):
            raise ValueError("adjustment-correction journal anchor is invalid")
        source_rows = rows[:sequence]
        if (not isinstance(checkpoint, dict) or checkpoint.get("identity") != before
                or checkpoint.get("sha256") != canonical_hash(
                    {key: value for key, value in checkpoint.items() if key != "sha256"})
                or not checkpoint.get("halt_required")
                or checkpoint.get("decision_id") != _RUSSIA_ADJUSTMENT_DECISION
                or checkpoint.get("cursor") != {"game_id": "game_001", "phase_id": "1903_winter_adjustment",
                                                  "unit": "phase_started"}
                or approval.get("source_snapshot_inventory_sha256") !=
                _file_hash(self.path / "snapshot_inventory.json")):
            raise ValueError("adjustment-correction checkpoint or snapshot binding is invalid")
        anchor = checkpoint.get("journal_anchor")
        if (not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int)
                or anchor["count"] > len(source_rows)
                or (source_rows[anchor["count"] - 1]["sha256"] if anchor["count"] else None)
                != anchor.get("head")):
            raise ValueError("adjustment-correction checkpoint journal anchor is invalid")
        if (approval.get("source_response_hashes") != {
                row["attempt_id"]: canonical_hash(row.get("response")) for row in source_rows
                if row.get("kind") == "attempt" and row.get("outcome") == "received"}
                or approval.get("source_prompt_hashes") != {
                    row["attempt_id"]: row["prompt_hash"] for row in source_rows
                    if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}}
                or approval.get("preserved_skipped_attempts") != sorted(
                    row["attempt_id"] for row in source_rows if row.get("kind") == "response_skipped")
                or approval.get("source_validation_hashes") != [canonical_hash(row) for row in source_rows
                    if row.get("kind") == "response_validation"]):
            raise ValueError("adjustment-correction approval does not preserve saved request history")
        parent = approval.get("rejected_parent")
        if not isinstance(parent, dict) or any(parent.get(key) != value for key, value in {
                "decision_id": _RUSSIA_ADJUSTMENT_DECISION,
                "attempt_id": "1679f7e2-7a85-4d2e-9368-3994352d3340",
                "prompt_hash": "d2aa2f9a95315f755a1313bb224b4fbd1a05ef9a7d100f8c6c24ab89fd5bf3e6",
                "response_hash": "69eb6fd80292e0262cc32ba86a92325454227b475e8b0e8e92d415d8e8bb5fa4",
                "issue": "illegal adjustment order payload for DISBAND",
        }.items()):
            raise ValueError("adjustment-correction rejected parent binding is invalid")
        attempts = [row for row in source_rows if row.get("kind") == "attempt"
                    and row.get("attempt_id") == parent["attempt_id"]
                    and row.get("decision_id") == parent["decision_id"]
                    and row.get("outcome") == "received"]
        validations = [row for row in source_rows if row.get("kind") == "response_validation"
                       and row.get("attempt_id") == parent["attempt_id"]
                       and row.get("decision_id") == parent["decision_id"]
                       and row.get("prompt_hash") == parent["prompt_hash"]
                       and row.get("valid") is False
                       and row.get("issue") == parent["issue"]]
        source_dispatches = {row["attempt_id"] for row in source_rows
                             if row.get("kind") in {"dispatch_prepared", "dispatch"}}
        source_completed = {row["attempt_id"] for row in source_rows
                            if row.get("kind") == "attempt"
                            and row.get("outcome") in {"received", "known_failed", "blocked", "not_dispatched"}}
        source_resolved = {row["attempt_id"] for row in source_rows
                           if row.get("kind") == "unknown_resolved"}
        if (len(attempts) != 1 or attempts[0].get("prompt_hash") != parent["prompt_hash"]
                or canonical_hash(attempts[0].get("response")) != parent["response_hash"]
                or len(validations) != 1 or parent.get("validation_hash") != canonical_hash(validations[0])
                or any(row.get("kind") == "order_correction_intent"
                       and row.get("decision_id") == parent["decision_id"] for row in source_rows)
                or source_dispatches - source_completed - source_resolved):
            raise ValueError("adjustment-correction approval is not bound to the one saved rejected response")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("adjustment-correction approval applies only to the reviewed run")
        return approval

    def approve_adjustment_order_correction_code_upgrade(
            self, *, expected_from: str, expected_to: str,
            source_code_manifest: dict[str, str], target_code_manifest: dict[str, str]) -> None:
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly reviewed target")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "adjustment_order_correction_code_upgrade"]
        if existing:
            self._validate_adjustment_order_correction_code_upgrade()
            return
        checkpoint = self._read_checkpoint()
        if checkpoint is None or not checkpoint.get("halt_required"):
            raise ValueError("adjustment-correction approval requires a halted recovery checkpoint")
        prose = self._validate_free_text_reflection_migration(
            checkpoint_identity=checkpoint.get("identity"), expected_after=checkpoint.get("identity"))
        before = prose.get("to_identity")
        if checkpoint.get("identity") != before or canonical_hash(before) != expected_from:
            raise ValueError("adjustment-correction approval requires the accepted prose-migration checkpoint")
        if ({key: value for key, value in self.identity.items() if key != "code_sha256"}
                != {key: value for key, value in before.items() if key != "code_sha256"}):
            raise ValueError("adjustment-correction approval permits code changes only")
        if (source_code_manifest != prose.get("target_code_manifest")
                or canonical_hash(source_code_manifest) != before.get("code_sha256")
                or canonical_hash(target_code_manifest) != self.identity.get("code_sha256")
                or set(source_code_manifest) != set(target_code_manifest)):
            raise ValueError("adjustment-correction source or target package manifest is inconsistent")
        changed = {name for name in source_code_manifest if source_code_manifest[name] != target_code_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/recovery.py"}
        if not changed or not changed <= allowed:
            raise ValueError("adjustment-correction code changed outside the reviewed allowlist")
        self._validate_snapshot()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or publication before adjustment-correction migration")
        decision = "game_001/1903_winter_adjustment/once/russia/birsan_ii/submit_orders"
        attempts = [row for row in rows if row.get("kind") == "attempt"
                    and row.get("attempt_id") == "1679f7e2-7a85-4d2e-9368-3994352d3340"
                    and row.get("decision_id") == decision and row.get("outcome") == "received"]
        validations = [row for row in rows if row.get("kind") == "response_validation"
                       and row.get("attempt_id") == "1679f7e2-7a85-4d2e-9368-3994352d3340"
                       and row.get("decision_id") == decision
                       and row.get("prompt_hash") == "d2aa2f9a95315f755a1313bb224b4fbd1a05ef9a7d100f8c6c24ab89fd5bf3e6"
                       and row.get("valid") is False
                       and row.get("issue") == "illegal adjustment order payload for DISBAND"]
        if (len(attempts) != 1 or attempts[0].get("prompt_hash") !=
                "d2aa2f9a95315f755a1313bb224b4fbd1a05ef9a7d100f8c6c24ab89fd5bf3e6"
                or canonical_hash(attempts[0].get("response")) !=
                "69eb6fd80292e0262cc32ba86a92325454227b475e8b0e8e92d415d8e8bb5fa4"
                or len(validations) != 1 or self.order_correction_intent(decision) is not None):
            raise ValueError("saved Russia rejection differs from the exact reviewed correction target")
        parent = {"decision_id": decision, "attempt_id": attempts[0]["attempt_id"],
                  "prompt_hash": attempts[0]["prompt_hash"],
                  "response_hash": canonical_hash(attempts[0].get("response")),
                  "validation_hash": canonical_hash(validations[0]),
                  "issue": validations[0]["issue"]}
        self._append({"kind": "adjustment_order_correction_code_upgrade", "from_identity": before,
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "source_journal_count": len(rows),
                      "source_checkpoint": {**checkpoint, "sha256": canonical_hash(checkpoint)},
                      "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                      "source_code_manifest": source_code_manifest, "target_code_manifest": target_code_manifest,
                      "changed_code_paths": sorted(changed),
                      "source_response_hashes": {row["attempt_id"]: canonical_hash(row.get("response"))
                          for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"},
                      "source_prompt_hashes": {row["attempt_id"]: row["prompt_hash"] for row in rows
                          if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}},
                      "preserved_skipped_attempts": sorted(row["attempt_id"] for row in rows
                          if row.get("kind") == "response_skipped"),
                      "source_validation_hashes": [canonical_hash(row) for row in rows
                          if row.get("kind") == "response_validation"],
                      "rejected_parent": parent,
                      "reason": "operator approved one deterministic correction for the exact saved adjustment rejection"})

    def free_text_reflection_adoption(self, decision_id: str) -> dict[str, Any] | None:
        rows = self._journal_rows()
        if not any(row.get("kind") == "free_text_reflection_migration" for row in rows):
            return None
        adjustment = next((row for row in rows
                           if row.get("kind") == "adjustment_order_correction_code_upgrade"), None)
        if adjustment is not None:
            self._validate_adjustment_order_correction_code_upgrade()
        approval = self._validate_free_text_reflection_migration(
            expected_after=adjustment.get("from_identity") if adjustment else None)
        adoption = approval["adoption"]
        return adoption if adoption.get("decision_id") == decision_id else None

    @staticmethod
    def _historical_prompt_hashes(rows: list[dict[str, Any]]) -> dict[str, str]:
        prompts: dict[str, str] = {}
        for row in rows:
            if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}:
                prompts.setdefault(row["decision_id"], row["prompt_hash"])
            elif row.get("kind") == "order_correction_intent":
                prompts[row["decision_id"]] = row["base_prompt_hash"]
        return prompts

    def legacy_relationship_prompt(self, decision_id: str) -> bool:
        upgrade = self._relationship_upgrade()
        return bool(upgrade and decision_id in upgrade.get("legacy_prompts", {}))

    def assert_migration_replay_complete_before_dispatch(self) -> None:
        rows = self._journal_rows()
        approval = next((row for row in rows if row.get("kind") == "free_text_reflection_migration"), None)
        if approval is None:
            return
        adjustment = next((row for row in rows
                           if row.get("kind") == "adjustment_order_correction_code_upgrade"), None)
        if adjustment is not None:
            self._validate_adjustment_order_correction_code_upgrade()
        self._validate_free_text_reflection_migration(
            expected_after=adjustment.get("from_identity") if adjustment else None)
        adoption = approval["adoption"]
        accepted = any(row.get("kind") == "response_validation"
                       and row.get("decision_id") == adoption["decision_id"]
                       and row.get("attempt_id") == adoption["attempt_id"]
                       and row.get("prompt_hash") == adoption["prompt_hash"]
                       and row.get("valid") is True for row in rows[approval["sequence"] + 1:])
        if not accepted:
            raise ValueError("saved-game prose adoption must validate before any new provider dispatch")

    def approve_free_text_reflection_migration(self, *, expected_from: str, expected_to: str,
                                              source_code_manifest: dict[str, str],
                                              target_code_manifest: dict[str, str],
                                              adoption: dict[str, Any]) -> None:
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly reviewed target")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "free_text_reflection_migration"]
        if existing:
            self._validate_free_text_reflection_migration()
            return
        checkpoint = self._read_checkpoint()
        if checkpoint is None or not checkpoint.get("halt_required"):
            raise ValueError("free-text migration requires a halted recovery checkpoint")
        negotiation = self._validate_negotiation_correction_code_upgrade(
            checkpoint_identity=checkpoint.get("identity"), expected_after=checkpoint.get("identity"))
        before = negotiation.get("to_identity")
        if checkpoint.get("identity") != before or canonical_hash(before) != expected_from:
            raise ValueError("free-text migration requires the verified negotiation correction checkpoint")
        if ({key: value for key, value in self.identity.items() if key != "code_sha256"}
                != {key: value for key, value in before.items() if key != "code_sha256"}):
            raise ValueError("free-text migration permits code changes only")
        if (source_code_manifest != negotiation.get("target_code_manifest")
                or canonical_hash(source_code_manifest) != before.get("code_sha256")
                or canonical_hash(target_code_manifest) != self.identity.get("code_sha256")
                or set(source_code_manifest) != set(target_code_manifest)):
            raise ValueError("free-text migration source or target package manifest is inconsistent")
        changed = {name for name in source_code_manifest if source_code_manifest[name] != target_code_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "agents/llm_context.py", "population/runner.py",
                   "runner/scripted_experiment_runner.py", "runner/recovery.py"}
        if not changed or not changed <= allowed:
            raise ValueError("free-text migration code changed outside the reviewed allowlist")
        self._validate_snapshot()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or publication before free-text migration")
        expected_adoption = {
            "decision_id": "game_001/1903_fall_retreat/once/germany/shaver/reflect",
            "attempt_id": "db7b6fab-7972-4554-9768-5db6d5ebb035",
            "prompt_hash": "b81704cd87c4973624edabf849d44db08e3bb2ea24448a0c906cabf60c897e9e",
            "content_sha256": "a56d54f36e2905bf1482862488132651f005fb7b92fea084817213f8de755460",
            "protocol": "free_text_reflection_v1",
        }
        if not isinstance(adoption, dict) or any(adoption.get(key) != value
                                                 for key, value in expected_adoption.items()):
            raise ValueError("operator adoption differs from the exact reviewed Germany response")
        adopted_attempts = [row for row in rows if row.get("kind") == "attempt"
                            and row.get("decision_id") == expected_adoption["decision_id"]
                            and row.get("attempt_id") == expected_adoption["attempt_id"]
                            and row.get("outcome") == "received"]
        if (len(adopted_attempts) != 1
                or adopted_attempts[0].get("prompt_hash") != expected_adoption["prompt_hash"]
                or hashlib.sha256(str((adopted_attempts[0].get("response") or {}).get("content", ""))
                                  .encode("utf-8")).hexdigest() != expected_adoption["content_sha256"]
                or canonical_hash(adopted_attempts[0].get("response")) != adoption.get("response_hash")):
            raise ValueError("saved Germany response differs from the reviewed adoption binding")
        self._append({"kind": "free_text_reflection_migration", "from_identity": before,
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "source_journal_count": len(rows),
                      "source_checkpoint": {**checkpoint, "sha256": canonical_hash(checkpoint)},
                      "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                      "source_code_manifest": source_code_manifest, "target_code_manifest": target_code_manifest,
                      "changed_code_paths": sorted(changed),
                      "source_response_hashes": {row["attempt_id"]: canonical_hash(row.get("response"))
                          for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"},
                      "source_prompt_hashes": {row["attempt_id"]: row["prompt_hash"] for row in rows
                          if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}},
                      "preserved_skipped_attempts": sorted(row["attempt_id"] for row in rows
                          if row.get("kind") == "response_skipped"),
                      "source_validation_hashes": [canonical_hash(row) for row in rows
                          if row.get("kind") == "response_validation"],
                      "historical_prompt_hashes": self._historical_prompt_hashes(rows),
                      "adoption": adoption, "reason": "operator approved exact saved prose response under free-text policy"})

    def approve_negotiation_correction_code_upgrade(self, *, expected_from: str, expected_to: str,
                                                    source_code_manifest: dict[str, str],
                                                    target_code_manifest: dict[str, str]) -> None:
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly reviewed target")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "negotiation_correction_code_upgrade"]
        if existing:
            self._validate_negotiation_correction_code_upgrade()
            return
        checkpoint = self._read_checkpoint()
        if checkpoint is None or not checkpoint.get("halt_required"):
            raise ValueError("negotiation-correction approval requires a halted recovery checkpoint")
        reflection = self._validate_reflection_correction_code_upgrade(
            checkpoint_identity=checkpoint.get("identity"), expected_after=checkpoint.get("identity"))
        before = reflection.get("to_identity")
        if checkpoint.get("identity") != before or canonical_hash(before) != expected_from:
            raise ValueError("negotiation-correction approval requires the verified reflection-correction checkpoint")
        if ({key: value for key, value in self.identity.items() if key != "code_sha256"}
                != {key: value for key, value in before.items() if key != "code_sha256"}):
            raise ValueError("negotiation-correction approval permits code changes only")
        if (source_code_manifest != reflection.get("target_code_manifest")
                or canonical_hash(source_code_manifest) != before.get("code_sha256")
                or canonical_hash(target_code_manifest) != self.identity.get("code_sha256")
                or set(source_code_manifest) != set(target_code_manifest)):
            raise ValueError("negotiation-correction source or target package manifest is inconsistent")
        changed = {name for name in source_code_manifest if source_code_manifest[name] != target_code_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/recovery.py"}
        if not changed or not changed <= allowed:
            raise ValueError("negotiation-correction code changed outside the reviewed allowlist")
        self._validate_snapshot()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or pending publication before negotiation-correction approval")
        source_checkpoint = {**checkpoint, "sha256": canonical_hash(checkpoint)}
        self._append({"kind": "negotiation_correction_code_upgrade", "from_identity": before,
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "source_journal_count": len(rows), "source_checkpoint": source_checkpoint,
                      "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                      "source_code_manifest": source_code_manifest, "target_code_manifest": target_code_manifest,
                      "changed_code_paths": sorted(changed),
                      "source_response_hashes": {row["attempt_id"]: canonical_hash(row.get("response"))
                          for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"},
                      "source_prompt_hashes": {row["attempt_id"]: row["prompt_hash"] for row in rows
                          if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}},
                      "preserved_skipped_attempts": sorted(row["attempt_id"] for row in rows
                          if row.get("kind") == "response_skipped"),
                      "source_validation_hashes": [canonical_hash(row) for row in rows
                          if row.get("kind") == "response_validation"],
                      "reason": "reviewed one-shot strict negotiation schema correction"})

    def approve_reflection_correction_code_upgrade(self, *, expected_from: str, expected_to: str,
                                                   source_code_manifest: dict[str, str],
                                                   target_code_manifest: dict[str, str]) -> None:
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly reviewed target")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "reflection_correction_code_upgrade"]
        if existing:
            self._validate_reflection_correction_code_upgrade()
            return
        checkpoint = self._read_checkpoint()
        if checkpoint is None or not checkpoint.get("halt_required"):
            raise ValueError("reflection-correction approval requires a halted recovery checkpoint")
        order = self._validate_order_correction_upgrade(checkpoint_identity=checkpoint.get("identity"),
                                                         expected_after=checkpoint.get("identity"))
        before = order.get("to_identity")
        if checkpoint.get("identity") != before or canonical_hash(before) != expected_from:
            raise ValueError("reflection-correction approval requires the verified order-correction checkpoint")
        if ({key: value for key, value in self.identity.items() if key != "code_sha256"}
                != {key: value for key, value in before.items() if key != "code_sha256"}):
            raise ValueError("reflection-correction approval permits code changes only")
        if (source_code_manifest != order.get("target_code_manifest")
                or canonical_hash(source_code_manifest) != before.get("code_sha256")
                or canonical_hash(target_code_manifest) != self.identity.get("code_sha256")
                or set(source_code_manifest) != set(target_code_manifest)):
            raise ValueError("reflection-correction source or target package manifest is inconsistent")
        changed = {name for name in source_code_manifest if source_code_manifest[name] != target_code_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/recovery.py"}
        if not changed or not changed <= allowed:
            raise ValueError("reflection-correction code changed outside the reviewed allowlist")
        self._validate_snapshot()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or pending publication before reflection-correction approval")
        source_checkpoint = {**checkpoint, "sha256": canonical_hash(checkpoint)}
        self._append({"kind": "reflection_correction_code_upgrade", "from_identity": before,
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "source_journal_count": len(rows), "source_checkpoint": source_checkpoint,
                      "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                      "source_code_manifest": source_code_manifest, "target_code_manifest": target_code_manifest,
                      "changed_code_paths": sorted(changed),
                      "source_response_hashes": {row["attempt_id"]: canonical_hash(row.get("response"))
                          for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"},
                      "source_prompt_hashes": {row["attempt_id"]: row["prompt_hash"] for row in rows
                          if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}},
                      "preserved_skipped_attempts": sorted(row["attempt_id"] for row in rows
                          if row.get("kind") == "response_skipped"),
                      "source_validation_hashes": [canonical_hash(row) for row in rows
                          if row.get("kind") == "response_validation"],
                      "reason": "reviewed one-shot strict reflection schema correction"})

    def approve_order_correction_code_upgrade(self, *, expected_from: str, expected_to: str,
                                              source_code_manifest: dict[str, str],
                                              target_code_manifest: dict[str, str]) -> None:
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly reviewed target")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "order_correction_code_upgrade"]
        if existing:
            self._validate_order_correction_code_upgrade()
            return
        checkpoint = self._read_checkpoint()
        retreat = self._validate_retreat_missing_order_upgrade(
            checkpoint_identity=checkpoint.get("identity") if checkpoint else None,
            expected_after=checkpoint.get("identity") if checkpoint else None,
        )
        before = retreat.get("to_identity")
        if checkpoint is None or checkpoint.get("identity") != before:
            raise ValueError("order-correction approval requires the verified retreat checkpoint")
        if canonical_hash(before) != expected_from:
            raise ValueError("order-correction predecessor differs from explicit approval")
        if ({key: value for key, value in self.identity.items() if key != "code_sha256"}
                != {key: value for key, value in before.items() if key != "code_sha256"}):
            raise ValueError("order-correction approval permits code changes only")
        if (source_code_manifest != retreat.get("target_code_manifest")
                or canonical_hash(source_code_manifest) != before.get("code_sha256")
                or canonical_hash(target_code_manifest) != self.identity.get("code_sha256")
                or set(source_code_manifest) != set(target_code_manifest)):
            raise ValueError("order-correction source or target package manifest is inconsistent")
        changed = {name for name in source_code_manifest if source_code_manifest[name] != target_code_manifest[name]}
        allowed = {"agents/baseline_llm_runtime.py", "runner/scripted_experiment_runner.py", "runner/recovery.py"}
        if not changed or not changed <= allowed:
            raise ValueError("order-correction code changed outside the reviewed allowlist")
        self._validate_snapshot()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or pending publication before order-correction approval")
        if not checkpoint.get("halt_required"):
            raise ValueError("order-correction approval requires a halted recovery checkpoint")
        source_checkpoint = {**checkpoint, "sha256": canonical_hash(checkpoint)}
        self._append({"kind": "order_correction_code_upgrade", "from_identity": before,
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "source_journal_count": len(rows), "source_checkpoint": source_checkpoint,
                      "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                      "source_code_manifest": source_code_manifest, "target_code_manifest": target_code_manifest,
                      "changed_code_paths": sorted(changed),
                      "source_response_hashes": {row["attempt_id"]: canonical_hash(row.get("response"))
                          for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"},
                      "source_prompt_hashes": {row["attempt_id"]: row["prompt_hash"] for row in rows
                          if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}},
                      "preserved_skipped_attempts": sorted(row["attempt_id"] for row in rows
                          if row.get("kind") == "response_skipped"),
                      "source_validation_hashes": [canonical_hash(row) for row in rows
                          if row.get("kind") == "response_validation"],
                      "reason": "reviewed one-shot invalid-order corrective feedback"})

    def approve_retreat_missing_order_upgrade(self, *, expected_from: str, expected_to: str,
                                              source_code_manifest: dict[str, str],
                                              target_code_manifest: dict[str, str]) -> None:
        """Append a narrowly bound recovery receipt after offline replay validation."""
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly reviewed target")
        rows = self._journal_rows()
        existing = [row for row in rows if row.get("kind") == "retreat_missing_order_upgrade"]
        if existing:
            self._validate_retreat_missing_order_upgrade()
            return
        relationship = self._relationship_upgrade()
        checkpoint = self._read_checkpoint()
        if relationship is None or checkpoint is None or checkpoint.get("identity") != relationship["to_identity"]:
            raise ValueError("retreat upgrade requires the verified six-relationship identity")
        if canonical_hash(relationship["to_identity"]) != expected_from:
            raise ValueError("retreat upgrade predecessor differs from explicit approval")
        if self.identity != relationship["to_identity"] and (
                {key: value for key, value in self.identity.items() if key != "code_sha256"}
                != {key: value for key, value in relationship["to_identity"].items() if key != "code_sha256"}):
            raise ValueError("retreat upgrade permits code changes only")
        if (canonical_hash(source_code_manifest) != relationship["to_identity"].get("code_sha256")
                or canonical_hash(target_code_manifest) != self.identity.get("code_sha256")
                or set(source_code_manifest) != set(target_code_manifest)):
            raise ValueError("retreat upgrade source or target code manifest is inconsistent")
        changed = {name for name in source_code_manifest if source_code_manifest[name] != target_code_manifest[name]}
        allowed = {"runner/scripted_experiment_runner.py", "runner/recovery.py"}
        if not changed or not changed <= allowed:
            raise ValueError("retreat upgrade changed code outside the reviewed allowlist")
        self._validate_snapshot()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or publication before retreat approval")
        if (not checkpoint.get("halt_required")
                or checkpoint.get("halt_reason") != "orders_rejected_or_substituted"
                or checkpoint.get("cursor") != {"unit": "phase_started", "game_id": "game_001",
                                                "phase_id": "1902_spring_retreat"}):
            raise ValueError("retreat approval requires the exact halted retreat decision")
        retreat_attempts = {
            row["decision_id"]: {"attempt_id": row["attempt_id"], "prompt_hash": row["prompt_hash"],
                                 "response_hash": canonical_hash(row.get("response"))}
            for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"
            and row.get("decision_id", "").startswith("game_001/1902_spring_retreat/")
        }
        if len(retreat_attempts) != 7:
            raise ValueError("retreat approval requires all seven saved responses")
        false_validation_rows = [row for row in rows if row.get("kind") == "response_validation"
                                 and row.get("valid") is False and row.get("decision_id") in retreat_attempts]
        validation_by_attempt = {row["attempt_id"]: canonical_hash(row) for row in false_validation_rows}
        expected_ids = {item["attempt_id"] for item in retreat_attempts.values()}
        if set(validation_by_attempt) != expected_ids:
            raise ValueError("retreat approval requires exactly the seven existing global rejection records")
        if self.root.name != "luna1912-1018-r2":
            raise ValueError("retreat approval applies only to luna1912-1018-r2")
        source_checkpoint = {**checkpoint, "sha256": canonical_hash(checkpoint)}
        source_response_hashes = {
            row["attempt_id"]: canonical_hash(row.get("response"))
            for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"
        }
        preserved_skipped = sorted(row["attempt_id"] for row in rows if row.get("kind") == "response_skipped")
        self._append({"kind": "retreat_missing_order_upgrade", "from_identity": relationship["to_identity"],
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "source_journal_count": len(rows), "source_checkpoint": source_checkpoint,
                      "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                      "source_code_manifest": source_code_manifest,
                      "target_code_manifest": target_code_manifest,
                      "changed_code_paths": sorted(changed), "retreat_attempts": retreat_attempts,
                      "source_response_hashes": source_response_hashes,
                      "preserved_skipped_attempts": preserved_skipped,
                      "historical_validation_hashes": validation_by_attempt,
                      "reason": "reviewed strict-guard compatibility for forced retreat disbands"})

    def approve_relationship_limit_six(self, *, expected_from: str, expected_to: str) -> None:
        """Caller holds writer.lock and has revalidated every received response offline."""
        if canonical_hash(self.identity) != expected_to:
            raise ValueError("current identity differs from the explicitly approved target")
        if self._relationship_upgrade() is not None:
            return
        checkpoint = self._read_checkpoint()
        if checkpoint is None or canonical_hash(checkpoint["identity"]) != expected_from:
            raise ValueError("checkpoint differs from the explicitly approved predecessor")
        before = checkpoint["identity"]
        if ({k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in self.identity.items() if k != "code_sha256"}):
            raise ValueError("relationship upgrade permits code changes only")
        self._validate_snapshot()
        rows = self._journal_rows()
        self._validate_journal_semantics(rows)
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("resolve unknown requests or publication before compatibility approval")
        if not checkpoint.get("halt_required") or checkpoint.get("halt_reason") != "invalid_output":
            raise ValueError("relationship upgrade requires a halted invalid response")
        if any(row.get("kind") == "response_skipped" for row in rows):
            raise ValueError("relationship upgrade cannot restore explicitly skipped responses")
        received = [row for row in rows if row.get("kind") == "attempt" and row.get("outcome") == "received"]
        if not received or len({row["decision_id"] for row in received}) != len(received):
            raise ValueError("relationship upgrade requires one durable response per decision")
        self._append({"kind": "relationship_limit_upgrade", "from_identity": before,
                      "to_identity": self.identity, "source_journal_head": rows[-1]["sha256"],
                      "legacy_prompts": {row["decision_id"]: row["prompt_hash"] for row in received},
                      "response_hashes": {row["attempt_id"]: canonical_hash(row["response"]) for row in received},
                      "revalidated_attempts": [row["attempt_id"] for row in received],
                      "reason": "operator reviewed six-opponent relationship capacity"})

    def legacy_prompt_hash(self, decision_id: str) -> str | None:
        rows = self._journal_rows()
        prose_migration = next((row for row in rows
                                if row.get("kind") == "free_text_reflection_migration"), None)
        adjustment = next((row for row in rows
                           if row.get("kind") == "adjustment_order_correction_code_upgrade"), None)
        if adjustment is not None:
            self._validate_adjustment_order_correction_code_upgrade()
        elif prose_migration is not None:
            self._validate_free_text_reflection_migration()
        upgrade = self._relationship_upgrade()
        retreat = any(row.get("kind") == "retreat_missing_order_upgrade" for row in rows)
        correction_upgrade = any(row.get("kind") == "order_correction_code_upgrade"
                                 for row in self._journal_rows())
        reflection_upgrade = any(row.get("kind") == "reflection_correction_code_upgrade"
                                 for row in self._journal_rows())
        negotiation_upgrade = any(row.get("kind") == "negotiation_correction_code_upgrade"
                                  for row in self._journal_rows())
        if upgrade is not None and upgrade.get("to_identity") != self.identity and not prose_migration:
            if not retreat:
                raise ValueError("relationship compatibility approval does not match current code")
            if negotiation_upgrade:
                self._validate_negotiation_correction_code_upgrade()
            elif reflection_upgrade:
                self._validate_reflection_correction_code_upgrade()
            elif correction_upgrade:
                self._validate_order_correction_code_upgrade()
            else:
                self._validate_retreat_missing_order_upgrade()
        if upgrade is None:
            return None
        migration = prose_migration
        if migration is not None:
            self._validate_free_text_reflection_migration(
                expected_after=adjustment.get("from_identity") if adjustment else None)
            prompt_hash = migration["historical_prompt_hashes"].get(decision_id)
        else:
            prompt_hash = upgrade["legacy_prompts"].get(decision_id)
        if prompt_hash and self.cached_response(decision_id, prompt_hash) is None:
            raise ValueError("approved historical response is unavailable; refusing another request")
        return prompt_hash

    def _validate_build_rule_replay_checkpoint(self, entry: dict[str, Any], receipt: dict[str, Any],
                                               rows: list[dict[str, Any]]) -> None:
        if (entry.get("transition_sha256") != receipt.get("sha256")
                or entry.get("identity") != receipt.get("to_identity")
                or not isinstance(entry.get("state"), dict) or not isinstance(entry.get("applied"), list)):
            raise ValueError("build replay checkpoint receipt is invalid")
        cursor = entry.get("cursor")
        original_cursor = receipt["source_checkpoint"].get("cursor", {})
        if isinstance(cursor, dict) and cursor.get("unit") in {"game_completed", "population_reports_completed"}:
            activation = next((row for row in rows if row.get("kind") == "build_rule_activation_v1"
                               and row.get("sequence", -1) < entry.get("sequence", -1)), None)
            boundary_validated = any(row.get("kind") == "build_rule_boundary_validation_v1"
                                     and activation is not None
                                     and activation["sequence"] < row.get("sequence", -1) < entry.get("sequence", -1)
                                     for row in rows)
            manifest = entry.get("terminal_manifest")
            if (activation is None or not boundary_validated or cursor.get("game_index") != 1 or not isinstance(manifest, dict)
                    or manifest.get("sha256") != canonical_hash({k: v for k, v in manifest.items() if k != "sha256"})
                    or manifest.get("cursor") != cursor or manifest.get("state") != entry.get("state")
                    or manifest.get("applied") != entry.get("applied")
                    or Path(manifest.get("canonical_root", "")).resolve() != self.root.resolve()):
                raise ValueError("terminal replay checkpoint lacks an exact commit manifest")
            pending = self.path / "commit_manifest.json"
            completed = any(row.get("kind") == "commit_completed"
                            and row.get("transaction_id") == manifest.get("transaction_id")
                            and row.get("sequence", -1) > entry.get("sequence", -1) for row in rows)
            if not completed:
                try:
                    if json.loads(pending.read_bytes()) != manifest:
                        raise ValueError("terminal replay checkpoint commit manifest differs")
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError("terminal replay checkpoint lacks pending or completed commit") from exc
            return
        if not isinstance(cursor, dict) or cursor.get("game_id") != original_cursor.get("game_id"):
            raise ValueError("build replay checkpoint belongs to another game")
        phases = {original_cursor.get("phase_id")}
        for row in rows[:receipt["sequence"]]:
            decision = row.get("decision_id")
            if isinstance(decision, str):
                parts = decision.split("/")
                if len(parts) == 6 and parts[0] == original_cursor.get("game_id"):
                    phase = parts[1]
                    phases.add(phase)
                    if phase.endswith("_negotiation"):
                        suffix = "_adjustment" if phase.endswith("_winter_negotiation") else "_movement"
                        phases.add(phase[:-len("_negotiation")] + suffix)
        if cursor.get("phase_id") not in phases:
            activation = next((row for row in rows if row.get("kind") == "build_rule_activation_v1"
                               and row.get("sequence", -1) < entry.get("sequence", -1)), None)
            def order(value: Any) -> tuple[int, int] | None:
                parts = value.split("_") if isinstance(value, str) else []
                seasons = {"spring": 0, "fall": 1, "winter": 2}
                if len(parts) < 3 or not parts[0].isdigit() or parts[1] not in seasons:
                    return None
                return int(parts[0]), seasons[parts[1]]
            before = order(original_cursor.get("phase_id"))
            after = order(cursor.get("phase_id"))
            if activation is None or before is None or after is None or after < before:
                raise ValueError("build replay checkpoint is outside historical phases")
        if cursor.get("unit") not in {"phase_started", "phase_completed"}:
            raise ValueError("build replay checkpoint is outside historical phases")

    def _validate_build_rule_replay_transition(self, receipt: dict[str, Any],
                                               rows: list[dict[str, Any]]) -> None:
        """Validate an append-only replay-only transition; it does not authorize adoption."""
        events = [row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"]
        if len(events) != 1 or events[0] != receipt:
            raise ValueError("build replay transition is missing or duplicated")
        sequence = receipt.get("sequence")
        if (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1
                or sequence >= len(rows) or receipt.get("source_journal_count") != sequence
                or receipt.get("source_journal_head") != rows[sequence - 1].get("sha256")
                or receipt.get("previous_hash") != receipt.get("source_journal_head")):
            raise ValueError("build replay transition journal prefix mismatch")
        source, target = receipt.get("from_identity"), receipt.get("to_identity")
        if (not isinstance(source, dict) or not isinstance(target, dict)
                or target != self.identity or source.get("experiment_id") != self.root.name
                or {k: v for k, v in source.items() if k != "code_sha256"}
                != {k: v for k, v in target.items() if k != "code_sha256"}):
            raise ValueError("build replay transition changed experiment identity or configuration")
        old_manifest, new_manifest = receipt.get("source_code_manifest"), receipt.get("target_code_manifest")
        if (not isinstance(old_manifest, dict) or not isinstance(new_manifest, dict)
                or canonical_hash(old_manifest) != source.get("code_sha256")
                or canonical_hash(new_manifest) != target.get("code_sha256")):
            raise ValueError("build replay transition package manifest mismatch")
        changed = {name for name in set(old_manifest) | set(new_manifest)
                   if old_manifest.get(name) != new_manifest.get(name)}
        allowed = {"agents/llm_context.py", "agents/baseline_llm_runtime.py", "core/prompt_assets.py",
                   "runner/recovery.py", "runner/build_rule_replay_selector.py",
                   "runner/build_fix_recovery_plan.py",
                   "runner/build_rule_proof.py", "runner/build_rule_accounting.py"}
        if not changed or not changed <= allowed or receipt.get("changed_code_paths") != sorted(changed):
            raise ValueError("build replay transition contains an unapproved code path")
        checkpoint = receipt.get("source_checkpoint")
        if (not isinstance(checkpoint, dict) or checkpoint.get("version") != RECOVERY_VERSION
                or checkpoint.get("sha256") != canonical_hash({k: v for k, v in checkpoint.items() if k != "sha256"})
                or checkpoint.get("identity") != source or not checkpoint.get("halt_required")):
            raise ValueError("build replay transition source checkpoint mismatch")
        backup = self.path / "build-rule-source-checkpoint.json"
        if (receipt.get("source_checkpoint_file_sha256") != _file_hash(backup)
                or receipt.get("source_checkpoint_file_sha256") is None):
            raise ValueError("build replay original checkpoint backup changed")
        try:
            if json.loads(backup.read_text(encoding="utf-8")) != checkpoint:
                raise ValueError("build replay original checkpoint backup differs from receipt")
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("build replay original checkpoint backup is unreadable") from exc
        anchor = checkpoint.get("journal_anchor") or {}
        count = anchor.get("count")
        if (not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > sequence
                or rows[count - 1].get("sha256") != anchor.get("head")):
            raise ValueError("build replay transition checkpoint anchor mismatch")
        activations = [row for row in rows if row.get("kind") == "build_rule_activation_v1"]
        if len(activations) > 1:
            raise ValueError("build rule activation is duplicated")
        if (receipt.get("source_snapshot_inventory_sha256") != _file_hash(self.path / "snapshot_inventory.json")
                or any(receipt.get(k) is None for k in ("source_snapshot_inventory_sha256", "source_ledger_sha256", "source_budget_sha256"))):
            raise ValueError("build replay transition snapshot or accounting changed")
        if not activations:
            if (receipt.get("source_ledger_sha256") != _file_hash(self.root / "metrics" / "provider_attempts.jsonl")
                    or receipt.get("source_budget_sha256") != _file_hash(self.root / "metrics" / "budget" / "budget_state.json")):
                raise ValueError("build replay transition snapshot or accounting changed")
        else:
            self._validate_build_rule_activation(activations[0], receipt, rows)
        boundary = receipt.get("boundary")
        if not isinstance(boundary, dict) or set(boundary) != {"decision_id", "attempt_id", "prompt_hash", "response_hash", "amend"}:
            raise ValueError("build replay transition boundary is invalid")
        if not isinstance(boundary["amend"], bool):
            raise ValueError("build replay transition boundary mode is invalid")
        attempts = [row for row in rows[:sequence] if row.get("kind") == "attempt"
                    and row.get("outcome") == "received" and row.get("decision_id") == boundary["decision_id"]]
        if (not attempts or attempts[-1].get("attempt_id") != boundary["attempt_id"]
                or attempts[-1].get("prompt_hash") != boundary["prompt_hash"]
                or canonical_hash(attempts[-1].get("response")) != boundary["response_hash"]
                or any(row.get("kind") == "response_validation" and row.get("attempt_id") == boundary["attempt_id"]
                       for row in rows[:sequence])):
            raise ValueError("build replay transition does not bind the latest unvalidated response")
        if boundary["amend"] and not boundary["decision_id"].endswith("/submit_orders"):
            raise ValueError("build replay amended boundary is not an order decision")
        if not boundary["decision_id"].startswith(str(checkpoint.get("cursor", {}).get("game_id", "")) + "/"):
            raise ValueError("build replay boundary belongs to another game")
        if boundary["amend"]:
            decision_parts = boundary["decision_id"].split("/")
            if (len(decision_parts) != 6 or checkpoint.get("cursor", {}).get("unit") != "phase_started"
                    or decision_parts[1] != checkpoint.get("cursor", {}).get("phase_id")):
                raise ValueError("build replay amended boundary does not match the halted phase")
            try:
                content = attempts[-1]["response"]["content"]
                payload = json.loads(content)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("build replay boundary response is not a JSON object") from exc
            if not isinstance(payload, dict) or payload.get("orders") != []:
                raise ValueError("build replay amended boundary must be the saved empty order response")
        activation_sequence = activations[0]["sequence"] if activations else len(rows)
        if any(row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}
               for row in rows[sequence + 1:activation_sequence]):
            raise ValueError("build replay transition cannot authorize a new provider attempt")
        if any(row.get("kind") == "response_validation" and row.get("attempt_id") == boundary["attempt_id"]
               for row in rows[sequence + 1:]):
            raise ValueError("build replay boundary cannot be validated before adoption")
        current = self._read_checkpoint()
        source_body = {key: value for key, value in checkpoint.items() if key != "sha256"}
        if current != source_body:
            anchor = (current or {}).get("journal_anchor") or {}
            count = anchor.get("count")
            if (not isinstance(count, int) or isinstance(count, bool) or count <= sequence + 1
                    or count > len(rows)):
                raise ValueError("build replay transition checkpoint identity mismatch")
            entry = rows[count - 1]
            if entry.get("kind") != "build_rule_replay_checkpoint_v1":
                raise ValueError("build replay checkpoint lacks its durable receipt")
            self._validate_build_rule_replay_checkpoint(entry, receipt, rows)
            expected = {"version": RECOVERY_VERSION, "identity": entry["identity"], "cursor": entry["cursor"],
                        "state": entry["state"], "applied": entry["applied"],
                        "journal_anchor": {"count": count, "head": entry["sha256"]}}
            if (current or {}).get("halt_required") is True:
                reason, decision = current.get("halt_reason"), current.get("decision_id")
                if not any(item.get("kind") == "halt" and item.get("reason") == reason
                           and item.get("decision_id") == decision for item in rows[count:]):
                    raise ValueError("build replay halt lacks its durable journal record")
                expected.update(halt_required=True, halt_reason=reason, decision_id=decision)
            if current != expected:
                raise ValueError("build replay checkpoint differs from its durable receipt")
        if self.in_flight() or (self.path / "commit_manifest.json").exists():
            raise ValueError("build replay transition has unresolved work")
        self._validate_snapshot()

    def authorize_build_rule_replay(self, *, source_code_manifest: dict[str, str],
                                    target_code_manifest: dict[str, str], decision_id: str,
                                    attempt_id: str, prompt_hash: str, amend: bool) -> None:
        """Append a replay-only receipt under the exclusive recovery lock.

        The receipt does not validate or adopt the boundary response, activate
        the new rules, or authorize any provider call.
        """
        with RecoveryLock(self.path / "writer.lock"):
            rows = self._journal_rows()
            existing = [row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"]
            if existing:
                self._validate_build_rule_replay_transition(existing[0], rows)
                boundary = existing[0]["boundary"]
                if (boundary["decision_id"], boundary["attempt_id"], boundary["prompt_hash"], boundary["amend"]) != (
                        decision_id, attempt_id, prompt_hash, amend):
                    raise ValueError("existing build replay transition differs from requested boundary")
                if (existing[0]["source_code_manifest"] != source_code_manifest
                        or existing[0]["target_code_manifest"] != target_code_manifest):
                    raise ValueError("existing build replay transition differs from requested package")
                return
            self._validate_journal_semantics(rows)
            checkpoint = self._read_checkpoint()
            if checkpoint is None or not rows:
                raise ValueError("build replay transition requires saved recovery evidence")
            matches = [row for row in rows if row.get("kind") == "attempt" and row.get("attempt_id") == attempt_id
                       and row.get("decision_id") == decision_id and row.get("prompt_hash") == prompt_hash
                       and row.get("outcome") == "received"]
            if len(matches) != 1:
                raise ValueError("build replay boundary response is missing or ambiguous")
            before = checkpoint["identity"]
            backup = self.path / "build-rule-source-checkpoint.json"
            if backup.exists():
                if _file_hash(backup) != _file_hash(self.checkpoint_path):
                    raise ValueError("existing build replay checkpoint backup differs")
            else:
                _atomic_copy(self.checkpoint_path, backup)
            changed = {name for name in set(source_code_manifest) | set(target_code_manifest)
                       if source_code_manifest.get(name) != target_code_manifest.get(name)}
            payload = {"kind": "build_rule_replay_transition_v1", "from_identity": before,
                       "to_identity": self.identity, "source_code_manifest": source_code_manifest,
                       "target_code_manifest": target_code_manifest, "changed_code_paths": sorted(changed),
                       "source_journal_count": len(rows), "source_journal_head": rows[-1]["sha256"],
                       "source_checkpoint": {**checkpoint, "sha256": canonical_hash(checkpoint)},
                       "source_checkpoint_file_sha256": _file_hash(backup),
                       "source_snapshot_inventory_sha256": _file_hash(self.path / "snapshot_inventory.json"),
                       "source_ledger_sha256": _file_hash(self.root / "metrics" / "provider_attempts.jsonl"),
                       "source_budget_sha256": _file_hash(self.root / "metrics" / "budget" / "budget_state.json"),
                       "boundary": {"decision_id": decision_id, "attempt_id": attempt_id,
                                    "prompt_hash": prompt_hash, "response_hash": canonical_hash(matches[0]["response"]),
                                    "amend": amend}}
            provisional = {**payload, "sequence": len(rows), "previous_hash": rows[-1]["sha256"]}
            provisional["sha256"] = canonical_hash(provisional)
            self._validate_build_rule_replay_transition(provisional, [*rows, provisional])
            self._append(payload)
            self._validate_build_rule_replay_transition(self._journal_rows()[-1], self._journal_rows())

    def _validate_build_rule_adoption(self, adoption: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        from .build_rule_proof import verify_reviewed_boundary
        transitions = [row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"]
        adoptions = [row for row in rows if row.get("kind") == "build_rule_boundary_adoption_v1"]
        if len(transitions) != 1 or len(adoptions) != 1 or adoptions[0] != adoption:
            raise ValueError("build boundary adoption or transition is missing or duplicated")
        transition = transitions[0]
        if adoption.get("sequence", -1) <= transition["sequence"] or adoption.get("transition_sha256") != transition["sha256"]:
            raise ValueError("build boundary adoption does not continue its transition")
        evidence = adoption.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != {"result", "context", "tree_proof"}:
            raise ValueError("build boundary adoption evidence is incomplete")
        evidence_root = (self.path / "build-rule-evidence").resolve()
        paths, pins = {}, {}
        for key, item in evidence.items():
            if not isinstance(item, dict) or set(item) != {"name", "sha256"}:
                raise ValueError("build boundary adoption evidence entry is invalid")
            name = item["name"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("build boundary adoption evidence path is invalid")
            path = evidence_root / name
            if path.is_symlink() or not path.resolve().is_relative_to(evidence_root):
                raise ValueError("build boundary adoption evidence escapes its directory")
            paths[key], pins[key] = path, item["sha256"]
        proof = verify_reviewed_boundary(receipt=transition, journal_rows=rows,
            result_path=paths["result"], context_path=paths["context"], tree_proof_path=paths["tree_proof"],
            expected_hashes=pins)
        if adoption.get("proof") != proof:
            raise ValueError("build boundary adoption differs from reviewed replay proof")

    def authorize_build_rule_adoption(self, *, evidence: dict[str, dict[str, str]]) -> None:
        """Append an adoption of reviewed copied replay evidence; no activation occurs."""
        with RecoveryLock(self.path / "writer.lock"):
            rows = self._journal_rows()
            self._validate_journal_semantics(rows)
            transition = next((row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"), None)
            if transition is None:
                raise ValueError("build boundary adoption requires a replay transition")
            existing = [row for row in rows if row.get("kind") == "build_rule_boundary_adoption_v1"]
            if existing:
                self._validate_build_rule_adoption(existing[0], rows)
                if existing[0]["evidence"] != evidence:
                    raise ValueError("existing build boundary adoption differs from requested evidence")
                return
            current = self._read_checkpoint()
            if current is None or current.get("cursor") != transition["source_checkpoint"].get("cursor"):
                raise ValueError("build boundary adoption requires the reconstructed boundary cursor")
            payload = {"kind": "build_rule_boundary_adoption_v1", "transition_sha256": transition["sha256"],
                       "evidence": evidence}
            provisional = {**payload, "sequence": len(rows), "previous_hash": rows[-1]["sha256"]}
            provisional["sha256"] = canonical_hash(provisional)
            # Compute the proof through the same validator used on restart.
            from .build_rule_proof import verify_reviewed_boundary
            evidence_root = self.path / "build-rule-evidence"
            if not isinstance(evidence, dict) or set(evidence) != {"result", "context", "tree_proof"}:
                raise ValueError("build boundary adoption evidence is incomplete")
            for value in evidence.values():
                if (not isinstance(value, dict) or set(value) != {"name", "sha256"}
                        or not isinstance(value["name"], str) or Path(value["name"]).name != value["name"]):
                    raise ValueError("build boundary adoption evidence path is invalid")
            paths = {key: evidence_root / value["name"] for key, value in evidence.items()}
            pins = {key: value["sha256"] for key, value in evidence.items()}
            provisional["proof"] = verify_reviewed_boundary(receipt=transition, journal_rows=rows,
                result_path=paths["result"], context_path=paths["context"], tree_proof_path=paths["tree_proof"],
                expected_hashes=pins)
            provisional["sha256"] = canonical_hash({k: v for k, v in provisional.items() if k != "sha256"})
            self._validate_build_rule_adoption(provisional, [*rows, provisional])
            self._append({**payload, "proof": provisional["proof"]})

    def _validate_build_rule_activation(self, activation: dict[str, Any], transition: dict[str, Any],
                                        rows: list[dict[str, Any]]) -> None:
        from .build_rule_accounting import validate_postactivation_accounting
        adoptions = [row for row in rows if row.get("kind") == "build_rule_boundary_adoption_v1"]
        if (len(adoptions) != 1 or activation.get("adoption_sha256") != adoptions[0].get("sha256")
                or activation.get("transition_sha256") != transition.get("sha256")
                or activation.get("sequence", -1) <= adoptions[0].get("sequence", -1)):
            raise ValueError("build rule activation does not follow the exact adoption")
        self._validate_build_rule_adoption(adoptions[0], rows)
        validations = [row for row in rows if row.get("kind") == "build_rule_boundary_validation_v1"]
        if len(validations) > 1:
            raise ValueError("build boundary validation is duplicated")
        if validations:
            self._validate_build_rule_boundary_validation(validations[0], transition, adoptions[0], activation, rows)
        future = [row for row in rows if row.get("kind") in {"dispatch_prepared", "dispatch", "attempt"}
                  and row.get("sequence", -1) > activation.get("sequence", -1)]
        if future and (not validations or min(row["sequence"] for row in future) <= validations[0]["sequence"]):
            raise ValueError("new provider attempt precedes durable boundary validation")
        ledger_backup = self.path / "build-rule-source-ledger.jsonl"
        budget_backup = self.path / "build-rule-source-budget.json"
        if (_file_hash(ledger_backup) != transition["source_ledger_sha256"]
                or _file_hash(budget_backup) != transition["source_budget_sha256"]):
            raise ValueError("build rule activation accounting backup changed")
        try:
            baseline_budget = json.loads(budget_backup.read_bytes())
            current_budget = json.loads((self.root / "metrics/budget/budget_state.json").read_bytes())
            baseline_ledger = ledger_backup.read_bytes()
            current_ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError("build rule activation accounting is unreadable") from exc
        validate_postactivation_accounting(baseline_ledger=baseline_ledger, current_ledger=current_ledger,
            baseline_budget=baseline_budget, current_budget=current_budget, rows=rows,
            activation_sequence=activation["sequence"])

    def _validate_build_rule_boundary_validation(self, validation: dict[str, Any], transition: dict[str, Any],
            adoption: dict[str, Any], activation: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        boundary, proof = transition["boundary"], adoption["proof"]
        version = "amended_build_v1" if boundary["amend"] else "historical_negotiation_v1"
        expected = {"transition_sha256": transition["sha256"], "adoption_sha256": adoption["sha256"],
                    "activation_sha256": activation["sha256"], "decision_id": boundary["decision_id"],
                    "attempt_id": boundary["attempt_id"], "prompt_hash": boundary["prompt_hash"],
                    "response_hash": boundary["response_hash"], "context_sha256": proof["context_sha256"],
                    "validator_version": version, "valid": True}
        if (validation.get("sequence", -1) <= activation.get("sequence", -1)
                or any(validation.get(key) != value for key, value in expected.items())):
            raise ValueError("durable build boundary validation differs from the adopted response")

    def record_build_rule_boundary_validation(self) -> None:
        rows = self._journal_rows()
        transition = next((row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"), None)
        adoption = next((row for row in rows if row.get("kind") == "build_rule_boundary_adoption_v1"), None)
        activation = next((row for row in rows if row.get("kind") == "build_rule_activation_v1"), None)
        if (transition is None or adoption is None or activation is None
                or not getattr(getattr(self, "build_rule_replay_selector", None), "_boundary_crossed", False)):
            raise ValueError("boundary validation requires the exact adopted response to be replayed")
        existing = [row for row in rows if row.get("kind") == "build_rule_boundary_validation_v1"]
        if existing:
            if len(existing) != 1:
                raise ValueError("build boundary validation is duplicated")
            self._validate_build_rule_boundary_validation(existing[0], transition, adoption, activation, rows)
            return
        boundary = transition["boundary"]
        self._append({"kind": "build_rule_boundary_validation_v1", "transition_sha256": transition["sha256"],
            "adoption_sha256": adoption["sha256"], "activation_sha256": activation["sha256"],
            "decision_id": boundary["decision_id"], "attempt_id": boundary["attempt_id"],
            "prompt_hash": boundary["prompt_hash"], "response_hash": boundary["response_hash"],
            "context_sha256": adoption["proof"]["context_sha256"],
            "validator_version": "amended_build_v1" if boundary["amend"] else "historical_negotiation_v1",
            "valid": True})

    def authorize_build_rule_activation(self) -> None:
        """Activate a reviewed adoption under lock without dispatching a request."""
        with RecoveryLock(self.path / "writer.lock"):
            rows = self._journal_rows()
            self._validate_journal_semantics(rows)
            transition = next((row for row in rows if row.get("kind") == "build_rule_replay_transition_v1"), None)
            adoption = next((row for row in rows if row.get("kind") == "build_rule_boundary_adoption_v1"), None)
            if transition is None or adoption is None:
                raise ValueError("build rule activation requires validated transition and adoption")
            existing = [row for row in rows if row.get("kind") == "build_rule_activation_v1"]
            if existing:
                self._validate_build_rule_activation(existing[0], transition, rows)
                return
            if self.in_flight():
                raise ValueError("build rule activation cannot proceed with unresolved provider requests")
            for source, backup, expected in (
                (self.root / "metrics/provider_attempts.jsonl", self.path / "build-rule-source-ledger.jsonl", transition["source_ledger_sha256"]),
                (self.root / "metrics/budget/budget_state.json", self.path / "build-rule-source-budget.json", transition["source_budget_sha256"]),
            ):
                if _file_hash(source) != expected:
                    raise ValueError("build rule activation source accounting changed")
                if backup.exists():
                    if _file_hash(backup) != expected:
                        raise ValueError("build rule activation accounting backup differs")
                else:
                    _atomic_copy(source, backup)
            payload = {"kind": "build_rule_activation_v1", "transition_sha256": transition["sha256"],
                       "adoption_sha256": adoption["sha256"]}
            provisional = {**payload, "sequence": len(rows), "previous_hash": rows[-1]["sha256"]}
            provisional["sha256"] = canonical_hash(provisional)
            self._validate_build_rule_activation(provisional, transition, [*rows, provisional])
            self._append(payload)

    def _validate_journal_semantics(self, rows: list[dict[str, Any]]) -> None:
        attempts: dict[str, tuple[str, str]] = {}
        dispatched: set[str] = set()
        corrections: dict[str, dict[str, Any]] = {}
        for row in rows:
            kind = row["kind"]
            if kind == "build_rule_replay_transition_v1":
                self._validate_build_rule_replay_transition(row, rows)
            if kind == "build_rule_replay_checkpoint_v1":
                prior = [item for item in rows[:row["sequence"]]
                         if item.get("kind") == "build_rule_replay_transition_v1"]
                if len(prior) != 1:
                    raise ValueError("build replay checkpoint lacks its transition")
                self._validate_build_rule_replay_checkpoint(row, prior[0], rows)
            if kind == "build_rule_boundary_adoption_v1":
                self._validate_build_rule_adoption(row, rows)
            if kind == "build_rule_activation_v1":
                transition = next((item for item in rows[:row["sequence"]]
                                   if item.get("kind") == "build_rule_replay_transition_v1"), None)
                if transition is None:
                    raise ValueError("build rule activation lacks its transition")
                self._validate_build_rule_activation(row, transition, rows)
            if kind == "build_rule_boundary_validation_v1":
                transition = next((item for item in rows[:row["sequence"]] if item.get("kind") == "build_rule_replay_transition_v1"), None)
                adoption = next((item for item in rows[:row["sequence"]] if item.get("kind") == "build_rule_boundary_adoption_v1"), None)
                activation = next((item for item in rows[:row["sequence"]] if item.get("kind") == "build_rule_activation_v1"), None)
                if transition is None or adoption is None or activation is None:
                    raise ValueError("build boundary validation lacks its authorization chain")
                self._validate_build_rule_boundary_validation(row, transition, adoption, activation, rows)
            if kind == "repeat_correction_intent":
                if not self.repeat_received_invalid:
                    raise ValueError("repeat correction journal requires its explicit policy")
                self._validate_repeat_correction(row, rows)
            if kind == "order_correction_intent":
                decision_id = row.get("decision_id")
                correction_method = row.get("method", "submit_orders")
                if (not isinstance(decision_id, str) or not decision_id
                        or decision_id in corrections
                        or correction_method not in {"submit_orders", "reflect", "negotiate_tick"}
                        or decision_id.rsplit("/", 1)[-1] != correction_method
                        or not all(isinstance(row.get(key), str) and row.get(key)
                                   for key in ("base_prompt_hash", "parent_attempt_id",
                                               "parent_response_hash", "feedback_hash",
                                               "parent_validation_hash", "correction_prompt_hash"))
                        or not isinstance(row.get("feedback"), dict)
                        or canonical_hash(row["feedback"]) != row.get("feedback_hash")):
                    raise ValueError("recovery journal has an invalid order correction intent")
                parent_validation = next((validation for validation in rows[:row["sequence"]]
                                          if validation.get("kind") == "response_validation"
                                          and validation.get("decision_id") == decision_id
                                          and validation.get("prompt_hash") == row.get("base_prompt_hash")
                                          and validation.get("attempt_id") == row.get("parent_attempt_id")
                                          and validation.get("valid") is False
                                          and canonical_hash(validation) == row.get("parent_validation_hash")), None)
                if parent_validation is None:
                    raise ValueError("order correction intent is not bound to its rejected-answer validation")
                corrections[decision_id] = row
            if kind in {"dispatch_prepared", "dispatch", "attempt", "response_validation",
                        "response_skipped", "unknown_resolved"}:
                values = (row.get("attempt_id"), row.get("decision_id"), row.get("prompt_hash"))
                if not all(isinstance(value, str) and value for value in values):
                    raise ValueError("recovery journal decision record has invalid identity fields")
                attempt_id, decision_id, prompt_hash = values
                prior = attempts.setdefault(attempt_id, (decision_id, prompt_hash))
                if prior != (decision_id, prompt_hash):
                    raise ValueError("recovery journal reuses an attempt ID for another decision")
            if kind == "dispatch":
                attempt_id = row["attempt_id"]
                if attempt_id in dispatched:
                    raise ValueError("recovery journal duplicates a provider dispatch")
                dispatched.add(attempt_id)
            if kind in {"dispatch_prepared", "dispatch", "attempt"} and row.get("decision_id") in corrections:
                intent = corrections[row["decision_id"]]
                if row.get("prompt_hash") not in {intent["base_prompt_hash"], intent["correction_prompt_hash"]}:
                    raise ValueError("order correction dispatch prompt differs from its durable intent")
            if kind == "attempt" and row.get("outcome") not in {
                "received", "blocked", "known_failed", "unknown", "not_dispatched"
            }:
                raise ValueError("recovery journal has an invalid provider outcome")
            if kind == "response_validation" and not isinstance(row.get("valid"), bool):
                raise ValueError("recovery journal has invalid response validation")

    def record_dispatch(self, *, decision_id: str, prompt_hash: str, attempt_id: str,
                        provider: dict[str, Any]) -> None:
        self._append({"kind": "dispatch", "decision_id": decision_id, "prompt_hash": prompt_hash,
                      "attempt_id": attempt_id, "provider": provider,
                      "started_at": datetime.now(UTC).isoformat()})

    def cached_response(self, decision_id: str, prompt_hash: str) -> dict[str, Any] | None:
        if not self.journal_path.exists():
            return None
        matches = []
        skipped: set[str] = set()
        with self.journal_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("kind") == "response_skipped" and row.get("decision_id") == decision_id \
                        and row.get("prompt_hash") == prompt_hash:
                    skipped.add(row.get("attempt_id"))
                if (row.get("kind") == "attempt" and row.get("decision_id") == decision_id
                        and row.get("prompt_hash") == prompt_hash
                        and row.get("outcome") in {"received", "blocked"}):
                    matches.append(row)
        usable = [row for row in matches if row.get("attempt_id") not in skipped]
        return usable[-1] if usable else None

    def record_validation(self, *, decision_id: str, prompt_hash: str, attempt_id: str,
                          valid: bool, issue: str | None = None) -> None:
        payload = {"kind": "response_validation", "decision_id": decision_id,
                   "prompt_hash": prompt_hash, "attempt_id": attempt_id,
                   "valid": valid, "issue": issue}
        if any(row.get("kind") == "response_validation" and all(row.get(key) == value
               for key, value in payload.items()) for row in self._journal_rows()):
            return
        self._append(payload)

    def reject_latest_response(self, *, decision_id: str, issue: str) -> None:
        rows = self._journal_rows()
        received = next((row for row in reversed(rows)
                         if row.get("kind") == "attempt" and row.get("decision_id") == decision_id
                         and row.get("outcome") == "received"), None)
        if received is not None:
            self.record_validation(decision_id=decision_id, prompt_hash=received["prompt_hash"],
                                   attempt_id=received["attempt_id"], valid=False, issue=issue)

    def retry_cached_response(self, *, decision_id: str, prompt_hash: str,
                              attempt_id: str, choice: str) -> None:
        if choice != "retry_after_review":
            raise ValueError("cached response choice must be retry_after_review")
        if not any(row.get("kind") == "attempt" and row.get("decision_id") == decision_id
                   and row.get("prompt_hash") == prompt_hash and row.get("attempt_id") == attempt_id
                   and row.get("outcome") == "received" for row in self._journal_rows()):
            raise ValueError("response retry does not match a durable response")
        if not any(row.get("kind") == "response_validation" and row.get("decision_id") == decision_id
                   and row.get("prompt_hash") == prompt_hash and row.get("attempt_id") == attempt_id
                   and row.get("valid") is False for row in self._journal_rows()):
            raise ValueError("response retry requires a durably rejected response")
        self._append({"kind": "response_skipped", "decision_id": decision_id,
                      "prompt_hash": prompt_hash, "attempt_id": attempt_id,
                      "choice": choice})

    def has_unresolved_dispatch(self, decision_id: str, prompt_hash: str) -> bool:
        dispatched: set[str] = set()
        resolved: set[str] = set()
        for row in self._journal_rows():
            if row.get("decision_id") != decision_id or row.get("prompt_hash") != prompt_hash:
                continue
            attempt_id = row.get("attempt_id")
            if row.get("kind") in {"dispatch", "dispatch_prepared"} and isinstance(attempt_id, str):
                dispatched.add(attempt_id)
            if row.get("kind") == "unknown_resolved" and isinstance(attempt_id, str):
                resolved.add(attempt_id)
            if row.get("kind") == "attempt" and row.get("outcome") in {"received", "known_failed", "blocked", "not_dispatched"} \
                    and isinstance(attempt_id, str):
                resolved.add(attempt_id)
        return bool(dispatched - resolved)

    def resolve_unknown(self, *, decision_id: str, attempt_id: str, choice: str) -> None:
        if choice not in {"retry_after_review", "abandon_decision"}:
            raise ValueError("unknown outcome choice must be retry_after_review or abandon_decision")
        pending = next((row for row in self.in_flight()
                        if row.get("decision_id") == decision_id and row.get("attempt_id") == attempt_id), None)
        if pending is None:
            raise ValueError("unknown dispatch does not match an unresolved attempt")
        self._append({"kind": "unknown_resolved", "decision_id": decision_id,
                      "prompt_hash": pending["prompt_hash"], "attempt_id": attempt_id, "choice": choice})

    def is_abandoned(self, decision_id: str) -> bool:
        return any(row.get("kind") == "unknown_resolved" and row.get("decision_id") == decision_id
                   and row.get("choice") == "abandon_decision" for row in self._journal_rows())

    def _validate_fourth_cleanup(self, approvals: list[dict[str, Any]], checkpoint: Any,
                                *, completion: dict[str, Any] | None = None,
                                expected_code: dict[str, str] | None = None) -> None:
        cleanup = self._cleanup_two_pass_approval()
        if cleanup and expected_code is None:
            self._validate_cleanup_two_pass(cleanup, checkpoint)
            return
        if len(approvals) != 1 or self.root.name != "luna1912-1018-r2":
            raise ValueError("fourth cleanup authorization applies once to the saved run")
        approval = approvals[0]
        before, after = approval["from_identity"], approval["to_identity"]
        rows = self._journal_rows()
        completions = [row for row in rows if row.get("kind") == "fourth_cleanup_compatibility_completion"]
        if len(completions) > 1 or completion is not None and completions:
            raise ValueError("duplicate fourth cleanup completion")
        completion = completion or (completions[0] if completions else None)
        old_code, new_code = approval["source_code"], approval["target_code"]
        if completion is not None:
            completed_code = completion["target_code"]
            if (completion.get("approval_sha256") != approval.get("sha256")
                    or completion.get("from_identity") != after
                    or canonical_hash(new_code) != after["code_sha256"]
                    or set(completed_code) != set(new_code)
                    or {k for k in new_code if new_code[k] != completed_code[k]} != {"runner/recovery.py"}
                    or {k: v for k, v in completion["to_identity"].items() if k != "code_sha256"}
                    != {k: v for k, v in after.items() if k != "code_sha256"}):
                raise ValueError("fourth cleanup completion binding mismatch")
            after, new_code = completion["to_identity"], completed_code
        if (after != self.identity or checkpoint["identity"] not in (before, approval["to_identity"], after)
                or {k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in after.items() if k != "code_sha256"}):
            raise ValueError("fourth cleanup identity mismatch")
        source_root = Path(__file__).parents[1]
        actual = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if (canonical_hash(old_code) != before["code_sha256"]
                or canonical_hash(new_code) != after["code_sha256"] or (expected_code if expected_code is not None else actual) != new_code
                or set(old_code) != set(new_code)
                or {k for k in old_code if old_code[k] != new_code[k]}
                != {"runner/recovery.py", "agents/baseline_llm_runtime.py"}):
            raise ValueError("fourth cleanup code binding mismatch")
        count = approval["source_journal_count"]
        decision = "game_001/game_end_cleanup/once/austria/birsan/game_end_cleanup"
        prompt = "88c00803ccb7f545dac6e7cbc4524fc8025eaa85c28a97eded802d329b989ac7"
        parent = "8baac9c8-dd74-4e7f-bfbf-fee2b45040db"
        if (count != 5674 or len(rows) < count or rows[count - 1]["sha256"] != approval["source_journal_head"]
                or approval.get("decision_id") != decision or approval.get("prompt_hash") != prompt
                or approval.get("parent_attempt_id") != parent or approval.get("max_attempts") != 4):
            raise ValueError("fourth cleanup scope mismatch")
        prefix = rows[:count]
        dispatches = [row for row in prefix if row.get("kind") == "dispatch" and row.get("decision_id") == decision]
        if (len(dispatches) != 3 or any(row.get("prompt_hash") != prompt for row in dispatches)
                or prefix[-1].get("reason") != "invalid_output"
                or prefix[-2].get("attempt_id") != parent or prefix[-2].get("valid") is not False
                or any(row.get("kind") == "order_correction_intent" and row.get("decision_id") == decision for row in rows)):
            raise ValueError("fourth cleanup requires the exact exhausted rejection")
        source_checkpoint = approval["source_checkpoint"]
        if (source_checkpoint["identity"] != before or not source_checkpoint.get("halt_required")
                or source_checkpoint.get("decision_id") != decision
                or approval.get("source_checkpoint_sha256") != canonical_hash(source_checkpoint)):
            raise ValueError("fourth cleanup requires halted source checkpoint")
        predecessor = ExperimentRecovery(self.root, identity=before, resume=True)
        budget = next(row for row in rows if row.get("kind") == "input_budget_upgrade")
        predecessor._validate_input_budget_upgrade(budget, source_checkpoint, expected_code=old_code)
        ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        if hashlib.sha256(ledger[:approval["source_ledger_bytes"]]).hexdigest() != approval["source_ledger_sha256"]:
            raise ValueError("fourth cleanup request ledger prefix mismatch")

    def attempt_limit(self, decision_id: str, prompt_hash: str) -> int | float:
        if self.repeat_received_invalid:
            return float("inf")
        italy = self._italy_cleanup_approval()
        if italy and decision_id == italy["decision_id"]:
            self._validate_italy_cleanup(italy, self._read_checkpoint())
            intent = next((r for r in self._journal_rows()
                           if r.get("kind") == "italy_cleanup_feedback_intent"
                           and r.get("prompt_hash") == prompt_hash), None)
            return intent["pass_number"] if intent else 3
        england = self._england_cleanup_approval()
        if england and decision_id == england["decision_id"]:
            self._validate_england_cleanup(england, self._read_checkpoint())
            intent = next((r for r in self._journal_rows()
                           if r.get("kind") == "england_cleanup_feedback_intent"
                           and r.get("prompt_hash") == prompt_hash), None)
            return intent["pass_number"] if intent else 3
        russia = self._russia_cleanup_approval()
        if russia and decision_id == russia["decision_id"]:
            self._validate_russia_cleanup(russia, self._read_checkpoint())
            intent = next((r for r in self._journal_rows()
                           if r.get("kind") == "russia_cleanup_feedback_intent"
                           and r.get("prompt_hash") == prompt_hash), None)
            return intent["pass_number"] if intent else 3
        cleanup = self._cleanup_two_pass_approval()
        if cleanup:
            self._validate_cleanup_two_pass(cleanup, self._read_checkpoint())
            if decision_id == cleanup["decision_id"]:
                intent = next((r for r in self._journal_rows() if r.get("kind") == "cleanup_feedback_intent"
                               and r.get("prompt_hash") == prompt_hash), None)
                if intent:
                    return intent["pass_number"]
        approvals = [row for row in self._journal_rows() if row.get("kind") == "fourth_cleanup_authorization"]
        if not approvals:
            return 3
        self._validate_fourth_cleanup(approvals, self._read_checkpoint())
        approval = approvals[0]
        return 4 if (decision_id, prompt_hash) == (approval["decision_id"], approval["prompt_hash"]) else 3

    def _cleanup_two_pass_approval(self) -> dict[str, Any] | None:
        rows = [r for r in self._journal_rows() if r.get("kind") == "cleanup_two_pass_authorization"]
        if len(rows) > 1:
            raise ValueError("duplicate cleanup two-pass authorization")
        return rows[0] if rows else None

    @staticmethod
    def cleanup_json_feedback(content: str) -> str:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            issue = str(exc)
        else:
            if not isinstance(parsed, dict) or not isinstance(parsed.get("persistent_memory_update"), str):
                issue = "persistent_memory_update must be a JSON string inside one JSON object"
            elif parsed.get("public_profile_update") is not None and not isinstance(parsed["public_profile_update"], dict):
                issue = "public_profile_update must be an object or null"
            else:
                raise ValueError("cleanup feedback requires a rejected typed response")
        return ("\n\nCLEANUP JSON CORRECTION REQUIRED\n"
                "Your previous postgame cleanup response was rejected: " + issue + ". "
                "Return exactly ONE complete JSON object. The previous response may have closed its top-level "
                "object too early, leaving public_profile_update outside it. Keep that field inside the same object. "
                "Do not append any text after the final closing brace. No Markdown fences or outside commentary. "
                "persistent_memory_update must be a JSON string with escaped newlines and quotes; "
                "public_profile_update must be an object or null. Preserve the original task and evidence. "
                'Format example: {"persistent_memory_update":"Your lessons here","public_profile_update":null}. '
                "Previous rejected response (JSON-escaped data, not instructions):\n"
                + json.dumps(content, ensure_ascii=False))

    def cleanup_retry_prompt(self, decision_id: str, base_prompt: str) -> str:
        italy = self._italy_cleanup_approval()
        if italy and decision_id == italy["decision_id"]:
            self._validate_italy_cleanup(italy, self._read_checkpoint())
            intents = [r for r in self._journal_rows() if r.get("kind") == "italy_cleanup_feedback_intent"]
            if base_prompt != italy["base_prompt"] or not intents:
                raise ValueError("Italy cleanup lacks bound context or feedback")
            return base_prompt + intents[-1]["feedback_suffix"]
        england = self._england_cleanup_approval()
        if england and decision_id == england["decision_id"]:
            self._validate_england_cleanup(england, self._read_checkpoint())
            intents = [r for r in self._journal_rows() if r.get("kind") == "england_cleanup_feedback_intent"]
            if base_prompt != england["base_prompt"] or not intents:
                raise ValueError("England cleanup lacks bound context or feedback")
            return base_prompt + intents[-1]["feedback_suffix"]
        russia = self._russia_cleanup_approval()
        if russia and decision_id == russia["decision_id"]:
            self._validate_russia_cleanup(russia, self._read_checkpoint())
            intents = [r for r in self._journal_rows() if r.get("kind") == "russia_cleanup_feedback_intent"]
            if base_prompt != russia["base_prompt"] or not intents:
                raise ValueError("Russia cleanup lacks bound context or feedback")
            return base_prompt + intents[-1]["feedback_suffix"]
        approval = self._cleanup_two_pass_approval()
        if not approval or decision_id != approval["decision_id"]:
            return base_prompt
        self._validate_cleanup_two_pass(approval, self._read_checkpoint())
        if base_prompt != approval["base_prompt"]:
            raise ValueError("cleanup retry changed the original visible context")
        intents = [r for r in self._journal_rows() if r.get("kind") == "cleanup_feedback_intent"]
        if not intents:
            raise ValueError("cleanup retry lacks its durable feedback intent")
        return base_prompt + intents[-1]["feedback_suffix"]

    def prepare_cleanup_feedback(self, pass_number: int) -> dict[str, Any]:
        approval = self._cleanup_two_pass_approval()
        if approval is None or pass_number not in (5, 6):
            raise ValueError("cleanup pass lacks explicit two-pass authorization")
        self._validate_cleanup_two_pass(approval, self._read_checkpoint())
        rows = self._journal_rows()
        existing = next((r for r in rows if r.get("kind") == "cleanup_feedback_intent"
                         and r.get("pass_number") == pass_number), None)
        if existing:
            return existing
        decision = approval["decision_id"]
        if (self.in_flight() or self.attempt_count(decision) != pass_number - 1
                or not self._read_checkpoint().get("halt_required")
                or sum(r.get("kind") == "cleanup_feedback_intent" for r in rows) != pass_number - 5):
            raise ValueError("cleanup feedback requires the next received-invalid pass")
        parent = next(r for r in reversed(rows) if r.get("kind") == "attempt"
                      and r.get("decision_id") == decision)
        if parent.get("outcome") != "received" or not any(
                r.get("kind") == "response_validation" and r.get("attempt_id") == parent["attempt_id"]
                and r.get("valid") is False for r in rows):
            raise ValueError("cleanup feedback requires a rejected received answer")
        suffix = self.cleanup_json_feedback(parent["response"]["content"])
        self._append(dict(kind="cleanup_feedback_intent", decision_id=decision, pass_number=pass_number,
                          parent_attempt_id=parent["attempt_id"], parent_response_sha256=canonical_hash(parent["response"]),
                          feedback_suffix=suffix,
                          prompt_hash=hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()))
        self._validate_cleanup_two_pass(approval, self._read_checkpoint())
        return self._journal_rows()[-1]

    def _validate_cleanup_two_pass(self, approval: dict[str, Any], checkpoint: Any,
                                   *, expected_code: dict[str, str] | None = None) -> None:
        england = self._england_cleanup_approval()
        if england and expected_code is None:
            self._validate_england_cleanup(england, checkpoint)
            return
        russia = self._russia_cleanup_approval()
        if russia and expected_code is None:
            self._validate_russia_cleanup(russia, checkpoint)
            return
        rows = self._journal_rows()
        before, after = approval["from_identity"], approval["to_identity"]
        if (self.root.name != "luna1912-1018-r2" or after != self.identity
                or checkpoint["identity"] not in (before, after)
                or {k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in after.items() if k != "code_sha256"}):
            raise ValueError("cleanup two-pass identity mismatch")
        old_code, new_code = approval["source_code"], approval["target_code"]
        source_root = Path(__file__).parents[1]
        actual = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if (canonical_hash(old_code) != before["code_sha256"]
                or canonical_hash(new_code) != after["code_sha256"]
                or (expected_code if expected_code is not None else actual) != new_code
                or set(old_code) != set(new_code)
                or {k for k in old_code if old_code[k] != new_code[k]}
                != {"runner/recovery.py", "agents/baseline_llm_runtime.py"}):
            raise ValueError("cleanup two-pass code binding mismatch")
        decision = "game_001/game_end_cleanup/once/austria/birsan/game_end_cleanup"
        parent = "8bbe2ef9-b9d5-4097-9b86-4724d87bca60"
        base_hash = "88c00803ccb7f545dac6e7cbc4524fc8025eaa85c28a97eded802d329b989ac7"
        count = approval["source_journal_count"]
        if (count != 5685 or len(rows) < count or rows[count - 1]["sha256"] != approval["source_journal_head"]
                or approval.get("decision_id") != decision or approval.get("parent_attempt_id") != parent
                or approval.get("max_attempts") != 6 or approval.get("base_prompt_hash") != base_hash
                or hashlib.sha256(approval["base_prompt"].encode("utf8")).hexdigest() != base_hash):
            raise ValueError("cleanup two-pass scope mismatch")
        prefix = rows[:count]
        if (prefix[-3].get("attempt_id") != parent or prefix[-3].get("outcome") != "received"
                or prefix[-2].get("valid") is not False or prefix[-2].get("attempt_id") != parent
                or prefix[-1].get("reason") != "invalid_output"
                or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision for r in prefix) != 4):
            raise ValueError("cleanup two-pass parent mismatch")
        source_checkpoint = approval["source_checkpoint"]
        if (source_checkpoint["identity"] != before or not source_checkpoint.get("halt_required")
                or source_checkpoint.get("decision_id") != decision
                or canonical_hash(source_checkpoint) != approval.get("source_checkpoint_sha256")):
            raise ValueError("cleanup two-pass requires its halted source checkpoint")
        predecessor = ExperimentRecovery(self.root, identity=before, resume=True)
        predecessor._validate_fourth_cleanup([r for r in rows if r.get("kind") == "fourth_cleanup_authorization"],
                                             source_checkpoint, expected_code=old_code)
        ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        if hashlib.sha256(ledger[:approval["source_ledger_bytes"]]).hexdigest() != approval["source_ledger_sha256"]:
            raise ValueError("cleanup two-pass ledger prefix mismatch")
        intents = [r for r in rows if r.get("kind") == "cleanup_feedback_intent"]
        if len(intents) > 2:
            raise ValueError("cleanup feedback intents exhausted")
        for index, intent in enumerate(intents):
            parent_row = next((r for r in rows if r.get("kind") == "attempt"
                               and r.get("attempt_id") == intent.get("parent_attempt_id")), None)
            rejection = next((r for r in rows if r.get("kind") == "response_validation"
                              and r.get("attempt_id") == intent.get("parent_attempt_id") and r.get("valid") is False), None)
            if (parent_row is None or rejection is None or parent_row.get("outcome") != "received"
                    or parent_row.get("decision_id") != decision or intent.get("decision_id") != decision
                    or parent_row["sequence"] >= intent["sequence"] or rejection["sequence"] >= intent["sequence"]
                    or any(r.get("kind") == "response_validation" and r.get("attempt_id") == intent.get("parent_attempt_id")
                           and r.get("valid") is True for r in rows)
                    or intent.get("pass_number") != 5 + index
                    or index == 0 and intent.get("parent_attempt_id") != parent
                    or index == 1 and parent_row.get("prompt_hash") != intents[0]["prompt_hash"]
                    or intent.get("parent_response_sha256") != canonical_hash(parent_row["response"])):
                raise ValueError("cleanup feedback parent mismatch")
            suffix = self.cleanup_json_feedback(parent_row["response"]["content"])
            if (intent.get("feedback_suffix") != suffix or intent.get("prompt_hash") !=
                    hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()):
                raise ValueError("cleanup feedback prompt mismatch")

    def _russia_cleanup_approval(self) -> dict[str, Any] | None:
        rows = [r for r in self._journal_rows() if r.get("kind") == "russia_cleanup_authorization"]
        if len(rows) > 1:
            raise ValueError("duplicate Russia cleanup authorization")
        return rows[0] if rows else None

    def prepare_russia_cleanup_feedback(self, pass_number: int) -> dict[str, Any]:
        approval = self._russia_cleanup_approval()
        if approval is None or pass_number not in (3, 4):
            raise ValueError("Russia cleanup requires explicit two-attempt authorization")
        self._validate_russia_cleanup(approval, self._read_checkpoint())
        rows = self._journal_rows()
        intents = [r for r in rows if r.get("kind") == "russia_cleanup_feedback_intent"]
        existing = next((r for r in intents if r.get("pass_number") == pass_number), None)
        if existing:
            return existing
        decision = approval["decision_id"]
        checkpoint = self._read_checkpoint()
        if (self.in_flight() or self.attempt_count(decision) != pass_number - 1
                or not checkpoint.get("halt_required") or checkpoint.get("decision_id") != decision
                or len(intents) != pass_number - 3):
            raise ValueError("Russia cleanup requires the next halted received-invalid pass")
        parent = next(r for r in reversed(rows) if r.get("kind") == "attempt"
                      and r.get("decision_id") == decision)
        if parent.get("outcome") != "received" or not any(
                r.get("kind") == "response_validation" and r.get("attempt_id") == parent["attempt_id"]
                and r.get("valid") is False for r in rows):
            raise ValueError("Russia cleanup requires a rejected received answer")
        suffix = self.cleanup_json_feedback(parent["response"]["content"])
        self._append(dict(kind="russia_cleanup_feedback_intent", decision_id=decision, pass_number=pass_number,
                          parent_attempt_id=parent["attempt_id"], parent_response_sha256=canonical_hash(parent["response"]),
                          feedback_suffix=suffix,
                          prompt_hash=hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()))
        self._validate_russia_cleanup(approval, self._read_checkpoint())
        return self._journal_rows()[-1]

    def _validate_russia_cleanup(self, approval: dict[str, Any], checkpoint: Any,
                                 *, expected_code: dict[str, str] | None = None) -> None:
        england = self._england_cleanup_approval()
        if england and expected_code is None:
            self._validate_england_cleanup(england, checkpoint)
            return
        rows = self._journal_rows()
        before, after = approval["from_identity"], approval["to_identity"]
        if (self.root.name != "luna1912-1018-r2" or after != self.identity
                or checkpoint["identity"] not in (before, after)
                or {k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in after.items() if k != "code_sha256"}):
            raise ValueError("Russia cleanup identity mismatch")
        old_code, new_code = approval["source_code"], approval["target_code"]
        source_root = Path(__file__).parents[1]
        actual = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if (canonical_hash(old_code) != before["code_sha256"]
                or canonical_hash(new_code) != after["code_sha256"] or (expected_code if expected_code is not None else actual) != new_code
                or set(old_code) != set(new_code)
                or {k for k in old_code if old_code[k] != new_code[k]} != {"runner/recovery.py"}):
            raise ValueError("Russia cleanup code binding mismatch")
        decision = "game_001/game_end_cleanup/once/russia/birsan_ii/game_end_cleanup"
        parent = "f6011b14-3a81-47a2-a6a2-e38ac6c167f4"
        base_hash = "1554235271557e997bef9295d977f81d885e883cb5de64656a1523daa988ad95"
        count = approval["source_journal_count"]
        if (count != 5713 or len(rows) < count or rows[count - 1]["sha256"] != approval["source_journal_head"]
                or approval.get("decision_id") != decision or approval.get("parent_attempt_id") != parent
                or approval.get("max_attempts") != 4 or approval.get("base_prompt_hash") != base_hash
                or hashlib.sha256(approval["base_prompt"].encode("utf8")).hexdigest() != base_hash):
            raise ValueError("Russia cleanup scope mismatch")
        prefix = rows[:count]
        if (prefix[-3].get("attempt_id") != parent or prefix[-3].get("outcome") != "received"
                or prefix[-2].get("valid") is not False or prefix[-2].get("attempt_id") != parent
                or prefix[-1].get("reason") != "invalid_output"
                or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision for r in prefix) != 2
                or any(r.get("kind") == "order_correction_intent" and r.get("decision_id") == decision for r in rows)):
            raise ValueError("Russia cleanup parent mismatch")
        source_checkpoint = approval["source_checkpoint"]
        if (source_checkpoint["identity"] != before or not source_checkpoint.get("halt_required")
                or source_checkpoint.get("decision_id") != decision
                or canonical_hash(source_checkpoint) != approval.get("source_checkpoint_sha256")):
            raise ValueError("Russia cleanup requires halted source checkpoint")
        predecessor = ExperimentRecovery(self.root, identity=before, resume=True)
        prior = predecessor._cleanup_two_pass_approval()
        if prior is None:
            raise ValueError("Russia cleanup requires the prior validated source chain")
        predecessor._validate_cleanup_two_pass(prior, source_checkpoint, expected_code=old_code)
        ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        if hashlib.sha256(ledger[:approval["source_ledger_bytes"]]).hexdigest() != approval["source_ledger_sha256"]:
            raise ValueError("Russia cleanup ledger prefix mismatch")
        intents = [r for r in rows if r.get("kind") == "russia_cleanup_feedback_intent"]
        if len(intents) > 2:
            raise ValueError("Russia cleanup feedback exhausted")
        for index, intent in enumerate(intents):
            parent_row = next((r for r in rows if r.get("kind") == "attempt"
                               and r.get("attempt_id") == intent.get("parent_attempt_id")), None)
            rejection = next((r for r in rows if r.get("kind") == "response_validation"
                              and r.get("attempt_id") == intent.get("parent_attempt_id") and r.get("valid") is False), None)
            if (parent_row is None or rejection is None or parent_row.get("outcome") != "received"
                    or parent_row.get("decision_id") != decision or intent.get("decision_id") != decision
                    or parent_row["sequence"] >= intent["sequence"] or rejection["sequence"] >= intent["sequence"]
                    or any(r.get("kind") == "response_validation" and r.get("attempt_id") == intent.get("parent_attempt_id")
                           and r.get("valid") is True for r in rows)
                    or intent.get("pass_number") != 3 + index
                    or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision
                           and r["sequence"] < intent["sequence"] for r in rows) != 2 + index
                    or index == 0 and intent.get("parent_attempt_id") != parent
                    or index == 1 and parent_row.get("prompt_hash") != intents[0]["prompt_hash"]
                    or intent.get("parent_response_sha256") != canonical_hash(parent_row["response"])):
                raise ValueError("Russia cleanup feedback parent mismatch")
            suffix = self.cleanup_json_feedback(parent_row["response"]["content"])
            if (intent.get("feedback_suffix") != suffix or intent.get("prompt_hash") !=
                    hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()):
                raise ValueError("Russia cleanup feedback prompt mismatch")

    def _england_cleanup_approval(self) -> dict[str, Any] | None:
        rows = [r for r in self._journal_rows() if r.get("kind") == "england_cleanup_authorization"]
        if len(rows) > 1:
            raise ValueError("duplicate England cleanup authorization")
        return rows[0] if rows else None

    def prepare_england_cleanup_feedback(self, pass_number: int) -> dict[str, Any]:
        approval = self._england_cleanup_approval()
        if approval is None or pass_number not in (2, 3):
            raise ValueError("England cleanup requires explicit two-attempt authorization")
        self._validate_england_cleanup(approval, self._read_checkpoint())
        rows = self._journal_rows()
        intents = [r for r in rows if r.get("kind") == "england_cleanup_feedback_intent"]
        existing = next((r for r in intents if r.get("pass_number") == pass_number), None)
        if existing:
            return existing
        decision = approval["decision_id"]
        checkpoint = self._read_checkpoint()
        if (self.in_flight() or self.attempt_count(decision) != pass_number - 1
                or not checkpoint.get("halt_required") or checkpoint.get("decision_id") != decision
                or len(intents) != pass_number - 2):
            raise ValueError("England cleanup requires the next halted received-invalid pass")
        parent = next(r for r in reversed(rows) if r.get("kind") == "attempt"
                      and r.get("decision_id") == decision)
        if parent.get("outcome") != "received" or not any(
                r.get("kind") == "response_validation" and r.get("attempt_id") == parent["attempt_id"]
                and r.get("valid") is False for r in rows):
            raise ValueError("England cleanup requires a rejected received answer")
        suffix = self.cleanup_json_feedback(parent["response"]["content"])
        self._append(dict(kind="england_cleanup_feedback_intent", decision_id=decision, pass_number=pass_number,
                          parent_attempt_id=parent["attempt_id"], parent_response_sha256=canonical_hash(parent["response"]),
                          feedback_suffix=suffix,
                          prompt_hash=hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()))
        self._validate_england_cleanup(approval, self._read_checkpoint())
        return self._journal_rows()[-1]

    def _validate_england_cleanup(self, approval: dict[str, Any], checkpoint: Any,
                                  *, expected_code: dict[str, str] | None = None) -> None:
        italy = self._italy_cleanup_approval()
        if italy and expected_code is None:
            self._validate_italy_cleanup(italy, checkpoint)
            return
        rows = self._journal_rows()
        before, after = approval["from_identity"], approval["to_identity"]
        if (self.root.name != "luna1912-1018-r2" or after != self.identity
                or checkpoint["identity"] not in (before, after)
                or {k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in after.items() if k != "code_sha256"}):
            raise ValueError("England cleanup identity mismatch")
        old_code, new_code = approval["source_code"], approval["target_code"]
        source_root = Path(__file__).parents[1]
        actual = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if (canonical_hash(old_code) != before["code_sha256"]
                or canonical_hash(new_code) != after["code_sha256"] or (expected_code if expected_code is not None else actual) != new_code
                or set(old_code) != set(new_code)
                or {k for k in old_code if old_code[k] != new_code[k]} != {"runner/recovery.py"}):
            raise ValueError("England cleanup code binding mismatch")
        decision = "game_001/game_end_cleanup/once/england/dolos/game_end_cleanup"
        parent = "ff68702b-6807-4cab-9374-227e77b3a552"
        base_hash = "ae84c9c846c0d975aee715274dbf472a9ed52b8c0f18a8d50298927d36c30341"
        count = approval["source_journal_count"]
        if (count != 5726 or len(rows) < count or rows[count - 1]["sha256"] != approval["source_journal_head"]
                or approval.get("decision_id") != decision or approval.get("parent_attempt_id") != parent
                or approval.get("max_attempts") != 3 or approval.get("base_prompt_hash") != base_hash
                or hashlib.sha256(approval["base_prompt"].encode("utf8")).hexdigest() != base_hash):
            raise ValueError("England cleanup scope mismatch")
        prefix = rows[:count]
        if (prefix[-3].get("attempt_id") != parent or prefix[-3].get("outcome") != "received"
                or prefix[-2].get("valid") is not False or prefix[-2].get("attempt_id") != parent
                or prefix[-1].get("reason") != "invalid_output"
                or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision for r in prefix) != 1
                or any(r.get("kind") == "order_correction_intent" and r.get("decision_id") == decision for r in rows)):
            raise ValueError("England cleanup parent mismatch")
        source_checkpoint = approval["source_checkpoint"]
        if (source_checkpoint["identity"] != before or not source_checkpoint.get("halt_required")
                or source_checkpoint.get("decision_id") != decision
                or canonical_hash(source_checkpoint) != approval.get("source_checkpoint_sha256")):
            raise ValueError("England cleanup requires halted source checkpoint")
        predecessor = ExperimentRecovery(self.root, identity=before, resume=True)
        prior = predecessor._russia_cleanup_approval()
        if prior is None:
            raise ValueError("England cleanup requires the prior validated source chain")
        predecessor._validate_russia_cleanup(prior, source_checkpoint, expected_code=old_code)
        ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        if hashlib.sha256(ledger[:approval["source_ledger_bytes"]]).hexdigest() != approval["source_ledger_sha256"]:
            raise ValueError("England cleanup ledger prefix mismatch")
        intents = [r for r in rows if r.get("kind") == "england_cleanup_feedback_intent"]
        if len(intents) > 2:
            raise ValueError("England cleanup feedback exhausted")
        for index, intent in enumerate(intents):
            parent_row = next((r for r in rows if r.get("kind") == "attempt"
                               and r.get("attempt_id") == intent.get("parent_attempt_id")), None)
            rejection = next((r for r in rows if r.get("kind") == "response_validation"
                              and r.get("attempt_id") == intent.get("parent_attempt_id") and r.get("valid") is False), None)
            if (parent_row is None or rejection is None or parent_row.get("outcome") != "received"
                    or parent_row.get("decision_id") != decision or intent.get("decision_id") != decision
                    or parent_row["sequence"] >= intent["sequence"] or rejection["sequence"] >= intent["sequence"]
                    or any(r.get("kind") == "response_validation" and r.get("attempt_id") == intent.get("parent_attempt_id")
                           and r.get("valid") is True for r in rows)
                    or intent.get("pass_number") != 2 + index
                    or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision
                           and r["sequence"] < intent["sequence"] for r in rows) != 1 + index
                    or index == 0 and intent.get("parent_attempt_id") != parent
                    or index == 1 and parent_row.get("prompt_hash") != intents[0]["prompt_hash"]
                    or intent.get("parent_response_sha256") != canonical_hash(parent_row["response"])):
                raise ValueError("England cleanup feedback parent mismatch")
            suffix = self.cleanup_json_feedback(parent_row["response"]["content"])
            if (intent.get("feedback_suffix") != suffix or intent.get("prompt_hash") !=
                    hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()):
                raise ValueError("England cleanup feedback prompt mismatch")

    def _italy_cleanup_approval(self) -> dict[str, Any] | None:
        rows = [r for r in self._journal_rows() if r.get("kind") == "italy_cleanup_authorization"]
        if len(rows) > 1:
            raise ValueError("duplicate Italy cleanup authorization")
        return rows[0] if rows else None

    def prepare_italy_cleanup_feedback(self, pass_number: int) -> dict[str, Any]:
        approval = self._italy_cleanup_approval()
        if approval is None or pass_number not in (2, 3, 4):
            raise ValueError("Italy cleanup requires explicit three-attempt authorization")
        self._validate_italy_cleanup(approval, self._read_checkpoint())
        rows = self._journal_rows()
        intents = [r for r in rows if r.get("kind") == "italy_cleanup_feedback_intent"]
        existing = next((r for r in intents if r.get("pass_number") == pass_number), None)
        if existing:
            return existing
        decision = approval["decision_id"]
        checkpoint = self._read_checkpoint()
        if (self.in_flight() or self.attempt_count(decision) != pass_number - 1
                or not checkpoint.get("halt_required") or checkpoint.get("decision_id") != decision
                or len(intents) != pass_number - 2):
            raise ValueError("Italy cleanup requires the next halted received-invalid pass")
        parent = next(r for r in reversed(rows) if r.get("kind") == "attempt"
                      and r.get("decision_id") == decision)
        if parent.get("outcome") != "received" or not any(
                r.get("kind") == "response_validation" and r.get("attempt_id") == parent["attempt_id"]
                and r.get("valid") is False for r in rows):
            raise ValueError("Italy cleanup requires a rejected received answer")
        suffix = self.cleanup_json_feedback(parent["response"]["content"])
        self._append(dict(kind="italy_cleanup_feedback_intent", decision_id=decision, pass_number=pass_number,
                          parent_attempt_id=parent["attempt_id"], parent_response_sha256=canonical_hash(parent["response"]),
                          feedback_suffix=suffix,
                          prompt_hash=hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()))
        self._validate_italy_cleanup(approval, self._read_checkpoint())
        return self._journal_rows()[-1]

    def _validate_italy_cleanup(self, approval: dict[str, Any], checkpoint: Any) -> None:
        rows = self._journal_rows()
        before, after = approval["from_identity"], approval["to_identity"]
        if (self.root.name != "luna1912-1018-r2" or after != self.identity
                or checkpoint["identity"] not in (before, after)
                or {k: v for k, v in before.items() if k != "code_sha256"}
                != {k: v for k, v in after.items() if k != "code_sha256"}):
            raise ValueError("Italy cleanup identity mismatch")
        old_code, new_code = approval["source_code"], approval["target_code"]
        source_root = Path(__file__).parents[1]
        actual = {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(source_root.rglob("*")) if p.is_file() and not p.is_symlink()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if (canonical_hash(old_code) != before["code_sha256"]
                or canonical_hash(new_code) != after["code_sha256"] or actual != new_code
                or set(old_code) != set(new_code)
                or {k for k in old_code if old_code[k] != new_code[k]} != {"runner/recovery.py"}):
            raise ValueError("Italy cleanup code binding mismatch")
        decision = "game_001/game_end_cleanup/once/italy/major/game_end_cleanup"
        parent = "f3e3f62d-e0a7-4564-a629-261c08195a0e"
        base_hash = "196806778fcf017e723390fbb36d30b22c06578dc01c20b417b7cef850d5e937"
        count = approval["source_journal_count"]
        if (count != 5739 or len(rows) < count or rows[count - 1]["sha256"] != approval["source_journal_head"]
                or approval.get("decision_id") != decision or approval.get("parent_attempt_id") != parent
                or approval.get("max_attempts") != 4 or approval.get("base_prompt_hash") != base_hash
                or hashlib.sha256(approval["base_prompt"].encode("utf8")).hexdigest() != base_hash):
            raise ValueError("Italy cleanup scope mismatch")
        prefix = rows[:count]
        if (prefix[-3].get("attempt_id") != parent or prefix[-3].get("outcome") != "received"
                or prefix[-2].get("valid") is not False or prefix[-2].get("attempt_id") != parent
                or prefix[-1].get("reason") != "invalid_output"
                or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision for r in prefix) != 1
                or any(r.get("kind") == "order_correction_intent" and r.get("decision_id") == decision for r in rows)):
            raise ValueError("Italy cleanup parent mismatch")
        source_checkpoint = approval["source_checkpoint"]
        if (source_checkpoint["identity"] != before or not source_checkpoint.get("halt_required")
                or source_checkpoint.get("decision_id") != decision
                or canonical_hash(source_checkpoint) != approval.get("source_checkpoint_sha256")):
            raise ValueError("Italy cleanup requires halted source checkpoint")
        predecessor = ExperimentRecovery(self.root, identity=before, resume=True)
        prior = predecessor._england_cleanup_approval()
        if prior is None:
            raise ValueError("Italy cleanup requires the prior validated source chain")
        predecessor._validate_england_cleanup(prior, source_checkpoint, expected_code=old_code)
        ledger = (self.root / "metrics/provider_attempts.jsonl").read_bytes()
        if hashlib.sha256(ledger[:approval["source_ledger_bytes"]]).hexdigest() != approval["source_ledger_sha256"]:
            raise ValueError("Italy cleanup ledger prefix mismatch")
        intents = [r for r in rows if r.get("kind") == "italy_cleanup_feedback_intent"]
        if len(intents) > 3:
            raise ValueError("Italy cleanup feedback exhausted")
        for index, intent in enumerate(intents):
            parent_row = next((r for r in rows if r.get("kind") == "attempt"
                               and r.get("attempt_id") == intent.get("parent_attempt_id")), None)
            rejection = next((r for r in rows if r.get("kind") == "response_validation"
                              and r.get("attempt_id") == intent.get("parent_attempt_id") and r.get("valid") is False), None)
            if (parent_row is None or rejection is None or parent_row.get("outcome") != "received"
                    or parent_row.get("decision_id") != decision or intent.get("decision_id") != decision
                    or parent_row["sequence"] >= intent["sequence"] or rejection["sequence"] >= intent["sequence"]
                    or any(r.get("kind") == "response_validation" and r.get("attempt_id") == intent.get("parent_attempt_id")
                           and r.get("valid") is True for r in rows)
                    or intent.get("pass_number") != 2 + index
                    or sum(r.get("kind") == "dispatch" and r.get("decision_id") == decision
                           and r["sequence"] < intent["sequence"] for r in rows) != 1 + index
                    or index == 0 and intent.get("parent_attempt_id") != parent
                    or index > 0 and parent_row.get("prompt_hash") != intents[index - 1]["prompt_hash"]
                    or intent.get("parent_response_sha256") != canonical_hash(parent_row["response"])):
                raise ValueError("Italy cleanup feedback parent mismatch")
            suffix = self.cleanup_json_feedback(parent_row["response"]["content"])
            if (intent.get("feedback_suffix") != suffix or intent.get("prompt_hash") !=
                    hashlib.sha256((approval["base_prompt"] + suffix).encode("utf8")).hexdigest()):
                raise ValueError("Italy cleanup feedback prompt mismatch")

    def attempt_count(self, decision_id: str) -> int:
        rows = self._journal_rows()
        dispatch_ids = {row.get("attempt_id") for row in rows
                        if row.get("kind") == "dispatch" and row.get("decision_id") == decision_id}
        resolved = {row.get("attempt_id") for row in rows
                    if row.get("kind") == "attempt" and row.get("outcome") in {"blocked", "not_dispatched"}}
        return sum(1 for row in rows if row.get("kind") == "dispatch" and row.get("decision_id") == decision_id) + sum(
            1 for row in rows if row.get("kind") == "dispatch_prepared"
            and row.get("decision_id") == decision_id and row.get("attempt_id") not in resolved
            and row.get("attempt_id") not in dispatch_ids
        )

    def record_operation(self, *, operation_id: str, expected_before: str,
                         resulting_state: dict[str, Any], targets: dict[str, str]) -> None:
        self._append({"kind": "operation_prepared", "operation_id": operation_id,
                      "expected_before": expected_before, "result_hash": canonical_hash(resulting_state),
                      "resulting_state": resulting_state, "targets": targets})

    def operation_applied(self, operation_id: str) -> None:
        self._append({"kind": "operation_applied", "operation_id": operation_id})

    def status(self) -> dict[str, Any]:
        checkpoint = self._read_checkpoint()
        rows = self._journal_rows()
        validations = {}
        for row in rows:
            if row.get("kind") == "response_validation":
                validations[row["attempt_id"]] = row
        upgrade = self._relationship_upgrade()
        correction_upgrade = next((row for row in self._journal_rows()
                                   if row.get("kind") == "order_correction_code_upgrade"), None)
        reflection_upgrade = next((row for row in self._journal_rows()
                                   if row.get("kind") == "reflection_correction_code_upgrade"), None)
        prose_migration = next((row for row in self._journal_rows()
                                if row.get("kind") == "free_text_reflection_migration"), None)
        negotiation_upgrade = next((row for row in self._journal_rows()
                                    if row.get("kind") == "negotiation_correction_code_upgrade"), None)
        adjustment_upgrade = next((row for row in self._journal_rows()
                                   if row.get("kind") == "adjustment_order_correction_code_upgrade"), None)
        retreat_upgrade = next((row for row in self._journal_rows()
                                if row.get("kind") == "retreat_missing_order_upgrade"), None)
        if adjustment_upgrade:
            self._validate_adjustment_order_correction_code_upgrade(
                checkpoint.get("identity") if checkpoint else None)
        elif prose_migration:
            self._validate_free_text_reflection_migration(checkpoint.get("identity") if checkpoint else None)
        elif negotiation_upgrade:
            self._validate_negotiation_correction_code_upgrade(checkpoint.get("identity") if checkpoint else None)
        elif reflection_upgrade:
            self._validate_reflection_correction_code_upgrade(checkpoint.get("identity") if checkpoint else None)
        elif correction_upgrade:
            self._validate_order_correction_code_upgrade(checkpoint.get("identity") if checkpoint else None)
        elif retreat_upgrade:
            self._validate_retreat_missing_order_upgrade()
        elif upgrade is not None and upgrade.get("to_identity") != self.identity:
            raise ValueError("relationship compatibility approval does not match current code")
        # Preserve the original relationship approval's historical validation
        # exemptions, but never hide a later rejection of the same attempt.
        approved_validation_hashes = set()
        if upgrade:
            relationship_attempts = set(upgrade["revalidated_attempts"])
            approved_validation_hashes.update(
                canonical_hash(row) for row in rows[:upgrade["sequence"]]
                if row.get("kind") == "response_validation" and row.get("valid") is False
                and row.get("attempt_id") in relationship_attempts
            )
        if retreat_upgrade:
            approved_validation_hashes.update(retreat_upgrade["historical_validation_hashes"].values())
        if prose_migration:
            adoption = prose_migration["adoption"]
            adopted_validation = next((row for row in rows[:prose_migration["sequence"]]
                if row.get("kind") == "response_validation" and row.get("decision_id") == adoption["decision_id"]
                and row.get("attempt_id") == adoption["attempt_id"] and row.get("valid") is False), None)
            if adopted_validation is not None:
                approved_validation_hashes.add(canonical_hash(adopted_validation))
        for intent in rows:
            if intent.get("kind") != "order_correction_intent":
                continue
            response = self.cached_response(intent["decision_id"], intent["correction_prompt_hash"])
            accepted = validations.get(response["attempt_id"]) if response else None
            if accepted and accepted.get("valid") is True:
                approved_validation_hashes.add(intent["parent_validation_hash"])
        rejected = [{key: row.get(key) for key in ("decision_id", "prompt_hash", "attempt_id", "issue")}
                    for attempt, row in validations.items()
                    if row.get("valid") is False and canonical_hash(row) not in approved_validation_hashes]
        return {"checkpoint_valid": checkpoint is not None,
                "cursor": checkpoint.get("cursor") if checkpoint else None,
                "journal_path": str(self.journal_path),
                "halt_required": bool(checkpoint and checkpoint.get("halt_required")),
                "safe_halt_requested": self.safe_halt_path.exists(),
                "in_flight": self.in_flight(), "rejected_responses": rejected}

    def in_flight(self) -> list[dict[str, Any]]:
        outstanding: dict[str, dict[str, Any]] = {}
        for row in self._journal_rows():
            if row.get("kind") in {"dispatch", "dispatch_prepared"}:
                outstanding[row["attempt_id"]] = row
            elif row.get("kind") == "attempt" and row.get("outcome") in {"received", "known_failed", "blocked", "not_dispatched"}:
                outstanding.pop(row.get("attempt_id"), None)
            elif row.get("kind") == "unknown_resolved":
                outstanding.pop(row.get("attempt_id"), None)
        return list(outstanding.values())

    def halt(self, *, reason: str, decision_id: str | None = None) -> None:
        current = self._read_checkpoint()
        if current is None:
            raise ValueError("cannot record halt without a checkpoint")
        self.checkpoint(cursor=current["cursor"], state=current.get("state", {}),
                        applied=current.get("applied", []))
        updated = self._read_checkpoint()
        body = {key: value for key, value in updated.items() if key != "sha256"}
        body["halt_required"] = True
        body["halt_reason"] = reason
        body["decision_id"] = decision_id
        atomic_json(self.checkpoint_path, {**body, "sha256": canonical_hash(body)})
        self._append({"kind": "halt", "reason": reason, "decision_id": decision_id})

    def _read_checkpoint(self) -> dict[str, Any] | None:
        if not self.checkpoint_path.exists():
            return None
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            digest = data.pop("sha256")
            if data.get("version") != RECOVERY_VERSION or canonical_hash(data) != digest:
                raise ValueError("checkpoint version or checksum is invalid")
            rows = self._journal_rows()
            anchor = data.get("journal_anchor")
            if not isinstance(anchor, dict) or not isinstance(anchor.get("count"), int):
                raise ValueError("checkpoint journal anchor is missing or invalid")
            count = anchor["count"]
            head = rows[count - 1].get("sha256") if count and count <= len(rows) else None
            if count > len(rows) or head != anchor.get("head"):
                raise ValueError("checkpoint journal anchor does not match durable history")
            return data
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"recovery checkpoint is corrupt: {exc}") from exc

    def _append(self, row: dict[str, Any]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        if row.get("kind") not in JOURNAL_KINDS:
            raise ValueError("unknown recovery journal event kind")
        rows = self._journal_rows()
        body = {**row, "sequence": len(rows), "previous_hash": rows[-1]["sha256"] if rows else None}
        durable = {**body, "sha256": canonical_hash(body)}
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(durable, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_json(self.journal_head_path, {"sequence": durable["sequence"], "sha256": durable["sha256"]})

    def _journal_rows(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        with self.journal_path.open("r", encoding="utf-8") as handle:
            rows = []
            previous = None
            for expected_sequence, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"recovery journal is corrupt at line {expected_sequence + 1}") from exc
                if not isinstance(row, dict) or row.get("kind") not in JOURNAL_KINDS:
                    raise ValueError(f"recovery journal has an invalid event at line {expected_sequence + 1}")
                digest = row.get("sha256")
                body = {key: value for key, value in row.items() if key != "sha256"}
                if (row.get("sequence") != expected_sequence or row.get("previous_hash") != previous
                        or not isinstance(digest, str) or canonical_hash(body) != digest):
                    raise ValueError(f"recovery journal integrity failed at line {expected_sequence + 1}")
                previous = digest
                rows.append(row)
        if rows:
            if not self.journal_head_path.is_file():
                if len(rows) == 1 and rows[0].get("kind") == "created":
                    return rows
                raise ValueError("recovery journal head marker is missing")
            try:
                head = json.loads(self.journal_head_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("recovery journal head marker is corrupt") from exc
            sequence = head.get("sequence") if isinstance(head, dict) else None
            if (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0
                    or sequence >= len(rows)
                    or head != {"sequence": sequence, "sha256": rows[sequence]["sha256"]}):
                raise ValueError("recovery journal is truncated or its head marker mismatches")
            # Journal rows are fsynced before the small head marker. A crash in
            # that gap leaves a valid hash-chained tail and an older head;
            # prepare() reconciles this after validating every row.
        return rows


    def _tree_inventory(self, root: Path) -> list[dict[str, Any]]:
        if any(path.is_symlink() for path in root.rglob("*")):
            raise ValueError("recovery snapshot cannot contain symlinks")
        return [{"path": path.relative_to(root).as_posix(), "sha256": _file_hash(path)}
                for path in sorted(root.rglob("*") if root.exists() else ()) if path.is_file()]

    def _validate_snapshot(self) -> None:
        snapshot = self.path / "initial_snapshot"
        manifest_path = self.path / "snapshot_inventory.json"
        if not snapshot.exists() and not manifest_path.exists():
            return
        if not snapshot.is_dir() or not manifest_path.is_file():
            raise ValueError("recovery snapshot or inventory is incomplete")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            inventory = manifest["inventory"]
            if (manifest.get("version") != 1 or manifest.get("sha256") != canonical_hash(inventory)
                    or inventory != self._tree_inventory(snapshot)):
                raise ValueError("recovery snapshot inventory mismatch")
        except (OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("recovery snapshot inventory is corrupt") from exc
