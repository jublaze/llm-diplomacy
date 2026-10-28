from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_diplomacy.agents.llm_provider import canonical_provider_config
from llm_diplomacy.factory import load_blueprint
from llm_diplomacy.population.config import _to_config

DEFAULT_VIEWER_ROOT = (
    ROOT / "viewer" if (ROOT / "viewer").is_dir()
    else ROOT.parent / "viewer" if (ROOT.parent / "viewer").is_dir()
    else ROOT.parent / "llm-diplomacy-viewer"
)
PLAYER_IDS = tuple(f"a{i}" for i in range(7))
POWERS = ("austria", "england", "france", "germany", "italy", "russia", "turkey")
SPECIALIST_BLUEPRINTS = (
    "blueprints/atlas.yaml",
    "blueprints/dorian.yaml",
    "blueprints/helena.yaml",
    "blueprints/iris.yaml",
    "blueprints/metternich.yaml",
    "blueprints/selene.yaml",
    "blueprints/shaver.yaml",
)
STUDY_ARMS: dict[str, tuple[tuple[str, str, str, int | None, str | None, str | None], ...]] = {
    "ticks": (
        ("n1", "negotiation_ticks_1", "vt1", 1, None, "ticks_1"),
        ("n3", "negotiation_ticks_3", "vt3", 3, None, "ticks_3"),
    ),
    "roster": (
        ("rg", "roster_general", "vrg", None, "general", "roster_general"),
        ("rs", "roster_specialist", "vrs", None, "specialist", "roster_specialist"),
    ),
    "memory": (
        ("mw", "memory_warmup", "vmw", None, None, "memory_warmup"),
        ("mr", "memory_reuse", "vmr", None, None, "memory_reuse"),
        ("mf", "memory_fresh", "vmf", None, None, "memory_fresh"),
    ),
}
TEMPLATE_BY_KIND = {
    "ticks_1": "viewer_study_general.yaml",
    "ticks_3": "viewer_study_general.yaml",
    "roster_general": "viewer_study_general.yaml",
    "roster_specialist": "viewer_study_specialist.yaml",
    "memory_warmup": "viewer_study_general.yaml",
    "memory_reuse": "viewer_study_memory_reuse.yaml",
    "memory_fresh": "viewer_study_general.yaml",
}
MEMORY_MARKER_PREFIX = "SYNTHETIC_MEMORY_VISIBILITY_PROBE_"
MAX_PROVIDER_CALLS = 200


@dataclass(frozen=True)
class PlannedArm:
    study: str
    label: str
    tag: str
    experiment_id: str
    template_path: Path
    template_sha256: str
    config_bytes: bytes
    config_sha256: str
    config: dict[str, Any]
    canonical_providers: dict[str, dict[str, Any]]
    agents_root_key: str


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction and is_junction():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_no_links(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    if _is_link(current):
        raise ValueError("linked path component is not allowed")
    for component in absolute.parts[1:]:
        current = current / component
        if current.exists() and _is_link(current):
            raise ValueError("symlink or junction is not allowed: " + current.name)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_output_root(output_root: str | Path, *, source_roots: Sequence[Path]) -> Path:
    destination = Path(os.path.abspath(output_root))
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("output root already exists; choose a fresh destination")
    if not destination.parent.is_dir():
        raise FileNotFoundError("output root parent must already exist")
    _assert_no_links(destination.parent)
    for source in source_roots:
        source = source.resolve(strict=True)
        if _inside(destination, source) or _inside(source, destination):
            raise ValueError("output root overlaps a source tree")
    return destination


def _provider_raw(config: dict[str, Any]) -> Any:
    runtime = config.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be an object")
    runtime_config = runtime.get("provider_config")
    if runtime_config is not None:
        if not isinstance(runtime_config, dict):
            raise ValueError("runtime.provider_config must be an object")
        return runtime_config
    if "llm_provider" in config:
        return config["llm_provider"]
    return {"kind": runtime.get("provider", "fake")}


def validate_study_config(config: dict[str, Any], *, template: Path) -> dict[str, dict[str, Any]]:
    parsed = _to_config(config, source=str(template))
    runtime = config.get("runtime")
    games = config.get("games")
    budget = config.get("budget")
    if not isinstance(runtime, dict) or runtime.get("kind") != "baseline_llm":
        raise ValueError(f"{template.name}: only baseline_llm runtime is supported")
    if config.get("dry_run") is True:
        raise ValueError(f"{template.name}: study configs must execute one bounded game")
    if not isinstance(games, dict) or games.get("count") != 1 or games.get("max_year") != 1901:
        raise ValueError(f"{template.name}: exactly one game capped at 1901 is required")
    if games.get("country_assignment") != "fixed" or games.get("seed") != 20261016:
        raise ValueError(f"{template.name}: fixed matched seed and country assignment are required")
    if not isinstance(budget, dict) or not isinstance(budget.get("max_provider_calls"), int):
        raise ValueError(f"{template.name}: finite provider call budget is required")
    if not 1 <= budget["max_provider_calls"] <= MAX_PROVIDER_CALLS:
        raise ValueError(f"{template.name}: provider call budget exceeds the offline cap")
    if budget.get("stop_policy") != "stop_experiment":
        raise ValueError(f"{template.name}: stop_experiment budget policy is required")
    ticks = runtime.get("max_ticks")
    if ticks not in {1, 3}:
        raise ValueError(f"{template.name}: negotiation ticks must be 1 or 3")
    selected = runtime.get("llm_agent_ids")
    if selected != list(PLAYER_IDS):
        raise ValueError(f"{template.name}: every fixed roster slot must use the fake LLM runtime")
    if len(parsed.players) != 7 or tuple(player.agent_id for player in parsed.players) != PLAYER_IDS:
        raise ValueError(f"{template.name}: exactly seven ordered stable player slots are required")
    if parsed.agent_state_mode not in {"fresh", "reuse"}:
        raise ValueError(f"{template.name}: invalid state mode")

    global_raw = _provider_raw(config)
    provider_rows: dict[str, dict[str, Any]] = {}
    global_provider = canonical_provider_config(global_raw, require_selection=True)
    if global_provider.get("kind") != "fake":
        raise ValueError(f"{template.name}: only effective fake providers are allowed")
    if global_provider.get("base_url") or (isinstance(global_raw, dict) and global_raw.get("api_style") not in (None, "none")):
        raise ValueError(f"{template.name}: fake provider must not define an endpoint or API style")
    for player in parsed.players:
        raw = player.provider_config if player.provider_config is not None else global_raw
        resolved = canonical_provider_config(raw, require_selection=True)
        if resolved.get("kind") != "fake":
            raise ValueError(f"{template.name}: non-fake provider override rejected for {player.agent_id}")
        if resolved.get("base_url") or (
            isinstance(raw, dict)
            and (raw.get("api_style") not in (None, "none") or raw.get("model_profile"))
        ):
            raise ValueError(f"{template.name}: fake provider must not define an endpoint or API style")
        provider_rows[player.agent_id] = resolved

    for player in parsed.players:
        blueprint_path = Path(player.blueprint)
        if not blueprint_path.is_absolute():
            blueprint_path = ROOT / blueprint_path
        blueprint_path = Path(os.path.abspath(blueprint_path))
        _assert_no_links(blueprint_path)
        resolved_blueprint = blueprint_path.resolve(strict=True)
        if not _inside(resolved_blueprint, ROOT.resolve(strict=True)):
            raise ValueError(f"{template.name}: blueprint escapes the framework source tree")
        load_blueprint(resolved_blueprint)
    return provider_rows


def _effective_config(template: Path, *, study: str, tag: str, experiment_id: str,
                      ticks: int | None, roster: str | None, mode: str) -> tuple[dict[str, Any], bytes]:
    source_bytes = template.read_bytes()
    raw = yaml.safe_load(source_bytes.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{template.name}: config root must be an object")
    raw = copy.deepcopy(raw)
    raw["experiment_id"] = experiment_id
    if ticks is not None:
        raw["runtime"]["max_ticks"] = ticks
    if roster == "general":
        for player in raw["agents"]:
            player["blueprint"] = "blueprints/rowan.yaml"
    elif roster == "specialist":
        for player, blueprint in zip(raw["agents"], SPECIALIST_BLUEPRINTS, strict=True):
            player["blueprint"] = blueprint
    raw["agent_state_mode"] = mode
    encoded = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100).encode("utf-8")
    return raw, encoded


def plan_studies(study_names: Sequence[str], output_root: str | Path, *,
                 config_dir: str | Path = ROOT / "config" / "examples",
                 viewer_root: str | Path = DEFAULT_VIEWER_ROOT) -> tuple[Path, Path, list[PlannedArm]]:
    selected = tuple(dict.fromkeys(study_names))
    if not selected or any(name not in STUDY_ARMS for name in selected):
        raise ValueError("select ticks, roster, memory, or all")
    if "all" in selected:
        selected = tuple(STUDY_ARMS)
    config_dir = Path(os.path.abspath(config_dir))
    viewer_root = Path(os.path.abspath(viewer_root))
    _assert_no_links(config_dir)
    _assert_no_links(viewer_root)
    config_dir = config_dir.resolve(strict=True)
    viewer_root = viewer_root.resolve(strict=True)
    if not (viewer_root / "scripts" / "validate_schema.ts").is_file() or not (viewer_root / "scripts" / "validate_bundle.ts").is_file():
        raise FileNotFoundError("viewer schema and semantic validators are required")
    if not (viewer_root / "scripts" / "build_report_catalog.ts").is_file():
        raise FileNotFoundError("viewer report catalog builder is required")
    if not (viewer_root / "node_modules" / "typescript").is_dir():
        raise FileNotFoundError("read-only viewer node_modules dependency tree is required; no install will be run")
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("Node.js is required for viewer validation")
    destination = validate_output_root(output_root, source_roots=(ROOT.resolve(strict=True), viewer_root, config_dir))
    try:
        os.path.relpath(destination, ROOT)
        for tag, *_ in (arm for rows in STUDY_ARMS.values() for arm in rows):
            os.path.relpath(destination / "agents" / ("memory-treatment" if tag in {"mw", "mr"} else tag), ROOT)
    except ValueError as exc:
        raise ValueError("output root and framework must share a drive for portable relative run paths") from exc

    arms: list[PlannedArm] = []
    for name in selected:
        for tag, label, experiment_id, ticks, roster, kind in STUDY_ARMS[name]:
            template_path = Path(os.path.abspath(config_dir / TEMPLATE_BY_KIND[kind]))
            _assert_no_links(template_path)
            template_path = template_path.resolve(strict=True)
            source_sha = hashlib.sha256(template_path.read_bytes()).hexdigest()
            mode = "reuse" if kind == "memory_reuse" else "fresh"
            config, config_bytes = _effective_config(
                template_path,
                study=name,
                tag=tag,
                experiment_id=experiment_id,
                ticks=ticks,
                roster=roster,
                mode=mode,
            )
            providers = validate_study_config(config, template=template_path)
            arms.append(PlannedArm(
                study=name,
                label=label,
                tag=tag,
                experiment_id=experiment_id,
                template_path=template_path,
                template_sha256=source_sha,
                config_bytes=config_bytes,
                config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                config=config,
                canonical_providers=providers,
                agents_root_key="memory-treatment" if tag in {"mw", "mr"} else tag,
            ))
    _validate_plan_matches(arms)
    _planned_destinations(arms)
    return destination, viewer_root, arms


def _validate_plan_matches(arms: Sequence[PlannedArm]) -> None:
    by_tag = {arm.tag: arm for arm in arms}
    for left, right in (("n1", "n3"), ("rg", "rs")):
        if left not in by_tag or right not in by_tag:
            continue
        a, b = by_tag[left].config, by_tag[right].config
        if a["games"] != b["games"] or a["agents"] and [p["agent_id"] for p in a["agents"]] != [p["agent_id"] for p in b["agents"]]:
            raise ValueError(f"matched conditions differ unexpectedly in {left}/{right}")
        if a["runtime"]["llm_agent_ids"] != b["runtime"]["llm_agent_ids"]:
            raise ValueError(f"player slots differ unexpectedly in {left}/{right}")
        if left == "n1":
            a_copy, b_copy = copy.deepcopy(a), copy.deepcopy(b)
            a_copy["experiment_id"] = b_copy["experiment_id"] = "matched"
            a_copy["runtime"]["max_ticks"] = b_copy["runtime"]["max_ticks"] = 1
            if a_copy != b_copy:
                raise ValueError("negotiation pair varies beyond its tick-count intervention")
        else:
            a_copy, b_copy = copy.deepcopy(a), copy.deepcopy(b)
            for config in (a_copy, b_copy):
                config.pop("experiment_id", None)
                for player in config["agents"]:
                    player["blueprint"] = "matched-roster"
            if a_copy != b_copy:
                raise ValueError("roster pair varies beyond its roster intervention")
            if a["agents"] == b["agents"]:
                raise ValueError("roster pair does not encode distinct roster intervention")
    if all(tag in by_tag for tag in ("mw", "mr", "mf")):
        warm, reuse, fresh = (copy.deepcopy(by_tag[tag].config) for tag in ("mw", "mr", "mf"))
        for config in (warm, reuse, fresh):
            config.pop("experiment_id", None)
            config.pop("agent_state_mode", None)
        if warm != reuse or warm != fresh:
            raise ValueError("memory arms must match outside state carryover")
        if by_tag["mw"].agents_root_key != by_tag["mr"].agents_root_key or by_tag["mw"].agents_root_key == by_tag["mf"].agents_root_key:
            raise ValueError("memory treatment and fresh control roots are not isolated")
    experiment_ids = [arm.experiment_id for arm in arms]
    if len(set(experiment_ids)) != len(experiment_ids):
        raise ValueError("every arm must have a unique experiment_id for report/catalog provenance")


def _planned_destinations(arms: Sequence[PlannedArm]) -> tuple[str, ...]:
    result = ["configs"]
    agent_roots: set[str] = set()
    for arm in arms:
        result.extend((f"runs/{arm.tag}", f"bundles/{arm.tag}", f"public/{arm.tag}", f"catalogs/{arm.tag}"))
        agent_roots.add(arm.agents_root_key)
    result.extend(f"agents/{key}" for key in sorted(agent_roots))
    if len(result) != len(set(result)):
        raise ValueError("planned output destinations overlap")
    return tuple(result)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _captured_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_command(command: Sequence[str], *, cwd: Path, timeout: int = 600) -> dict[str, Any]:
    record: dict[str, Any] = {
        "command": list(command), "cwd": str(cwd), "exit_code": None,
        "stdout": "", "stderr": "",
    }
    try:
        completed = subprocess.run(
            list(command), cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        record.update({
            "stdout": _captured_text(exc.stdout), "stderr": _captured_text(exc.stderr),
            "execution_error": f"TimeoutExpired after {timeout} seconds",
            "timeout_seconds": timeout, "timed_out": True,
        })
        return record
    except OSError as exc:
        record["stderr"] = str(exc)
        record["execution_error"] = f"{type(exc).__name__}: {exc}"
        return record
    record.update({
        "exit_code": completed.returncode,
        "stdout": _captured_text(completed.stdout),
        "stderr": _captured_text(completed.stderr),
    })
    return record


def _run_and_record(record: dict[str, Any], command: Sequence[str], *, cwd: Path,
                    command_runner: Callable[..., dict[str, Any]], manifest: dict[str, Any],
                    manifest_path: Path, timeout: int = 600) -> dict[str, Any]:
    try:
        result = command_runner(command, cwd=cwd, timeout=timeout)
    except Exception as exc:
        result = {
            "command": list(command), "cwd": str(cwd), "exit_code": None,
            "stdout": _captured_text(getattr(exc, "stdout", None)),
            "stderr": _captured_text(getattr(exc, "stderr", None)),
            "execution_error": f"{type(exc).__name__}: {exc}",
        }
        if isinstance(exc, subprocess.TimeoutExpired):
            result.update({"timed_out": True, "timeout_seconds": timeout})
    result = dict(result)
    result.setdefault("command", list(command))
    result.setdefault("cwd", str(cwd))
    result.setdefault("exit_code", None)
    result.setdefault("stdout", "")
    result.setdefault("stderr", "")
    record["commands"].append(result)
    _write_json(manifest_path, manifest)
    if result.get("exit_code") != 0 or result.get("execution_error"):
        code = result.get("exit_code")
        detail = result.get("execution_error") or _captured_text(result.get("stderr"))[-1200:]
        raise RuntimeError(f"command failed with exit {code}: {detail}")
    return result


def _require_completed(experiment_root: Path, experiment_id: str) -> dict[str, Any]:
    summary_path = experiment_root / "run_summary.json"
    if not summary_path.is_file():
        raise ValueError(f"completed run summary is missing for {experiment_id}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("experiment_id") != experiment_id or summary.get("status") != "completed":
        raise ValueError(f"run did not complete successfully: {experiment_id}")
    if summary.get("games_requested") != 1 or summary.get("games_completed") != 1 or summary.get("games_failed") != 0:
        raise ValueError(f"run did not complete exactly one bounded game: {experiment_id}")
    return summary


def _seed_synthetic_memory(agents_root: Path, marker: str, *, arm_output: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    block = (
        "\n\n## Offline study synthetic fixture\n"
        f"{marker}\n"
        "This is synthetic test text, not an observed game fact and not a strategic instruction.\n"
    )
    for agent_id in PLAYER_IDS:
        agent_dir = agents_root / agent_id
        path = agent_dir / "persistent_memory.md"
        if not agent_dir.is_dir() or not path.is_file():
            raise ValueError(f"warmup did not create treatment folio for {agent_id}")
        if marker in path.read_text(encoding="utf-8"):
            raise ValueError("synthetic memory marker already exists; refusing to duplicate it")
        path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
        hashes[agent_id] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_json(arm_output / "synthetic-memory-fixture.json", {
        "schema_version": "viewer_study_synthetic_memory_v1",
        "marker": marker,
        "source": "explicitly seeded by offline viewer study harness after warmup",
        "claim_boundary": "not learned memory, not game evidence, not a strategic instruction",
        "agent_ids": list(PLAYER_IDS),
        "persistent_memory_sha256": hashes,
    })
    return hashes


def audit_memory_prompts(experiment_root: Path, *, marker: str, expected_present: bool) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for agent_id in PLAYER_IDS:
        outputs = experiment_root / "games" / "game_001" / "agents" / agent_id / "runtime" / "phase_outputs"
        prompts: list[str] = []
        if outputs.is_dir():
            for path in sorted(outputs.glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                meta = payload.get("llm_meta", {}) if isinstance(payload, dict) else {}
                prompt = meta.get("prompt") if isinstance(meta, dict) else None
                if isinstance(prompt, str) and prompt:
                    prompts.append(prompt)
        if not prompts:
            raise ValueError(f"no recorded provider prompt found for {agent_id} in {experiment_root.name}")
        matches = sum(marker in prompt for prompt in prompts)
        if expected_present and matches == 0:
            raise ValueError(f"synthetic memory marker was absent from every provider prompt for {agent_id}")
        if not expected_present and matches:
            raise ValueError(f"synthetic treatment memory leaked into fresh control prompts for {agent_id}")
        result[agent_id] = {"recorded_prompts": len(prompts), "marker_prompts": matches}
    return result


def _public_metrics(report_path: Path, experiment_root: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    run_summary = json.loads((experiment_root / "run_summary.json").read_text(encoding="utf-8"))
    game_root = experiment_root / "games" / "game_001"
    usage = json.loads((experiment_root / "metrics" / "provider_usage.json").read_text(encoding="utf-8"))
    totals = usage.get("totals")
    if not isinstance(totals, dict) or not isinstance(totals.get("provider_calls"), int):
        raise ValueError("provider usage summary lacks its attempt total")
    attempt_rows = [
        json.loads(line)
        for line in (experiment_root / "metrics" / "provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    request_rows = [row for row in attempt_rows if row.get("kind") == "request"]
    if len(request_rows) != totals["provider_calls"]:
        raise ValueError("provider attempt ledger and usage summary disagree")
    attempts_by_method = Counter(str(row.get("method")) for row in request_rows)
    lifecycle_rows = [row for row in attempt_rows if row.get("kind") == "lifecycle"]
    event_rows = [
        json.loads(line)
        for line in (experiment_root / "logs" / "experiment_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tick_events = [row for row in event_rows if row.get("event_type") == "negotiation_tick_participants"]
    ticks_by_phase: dict[str, list[int]] = {}
    for row in tick_events:
        phase_id, tick = row.get("phase_id"), row.get("tick")
        if not isinstance(phase_id, str) or not isinstance(tick, int) or isinstance(tick, bool):
            raise ValueError("negotiation tick event has invalid phase or tick metadata")
        ticks_by_phase.setdefault(phase_id, []).append(tick)
    for phase_id, ticks in ticks_by_phase.items():
        if sorted(set(ticks)) != list(range(1, max(ticks) + 1)):
            raise ValueError(f"negotiation tick events are not contiguous for {phase_id}")
    fallbacks_by_method = {
        method: int(row.get("fallback_invocations", 0))
        for method, row in sorted(usage.get("by_method", {}).items())
    }
    game = report.get("game", {})
    return {
        "run_status": run_summary.get("status"),
        "games_completed": run_summary.get("games_completed"),
        "stop_condition": game.get("stop_condition"),
        "years_played_range": game.get("years_played"),
        "outcome": game.get("outcome"),
        "public_message_count": len(report.get("messages", {})) if isinstance(report.get("messages"), dict) else None,
        "public_order_count": len(report.get("orders", {})) if isinstance(report.get("orders"), dict) else None,
        "negotiation_tick_event_count": len(tick_events),
        "negotiation_ticks_by_phase": {phase: sorted(ticks) for phase, ticks in sorted(ticks_by_phase.items())},
        "negotiation_participant_slots": sum(int(row.get("active_participant_count", 0)) for row in tick_events),
        "provider_request_attempt_count": len(request_rows),
        "provider_request_attempts_by_method": dict(sorted(attempts_by_method.items())),
        "provider_lifecycle_invocation_count": len(lifecycle_rows),
        "provider_retry_count": int(totals.get("repair_calls", 0)),
        "provider_fallback_invocation_count": int(totals.get("fallback_invocations", 0)),
        "provider_fallback_invocations_by_method": fallbacks_by_method,
        "provider_errors": int(totals.get("provider_errors", 0)),
        "provider_budget_blocked_attempts": int(totals.get("budget_blocked_attempts", 0)),
    }


def _validate_projection(public_root: Path, public_report: Path, experiment_id: str) -> dict[str, Any]:
    report = json.loads(public_report.read_text(encoding="utf-8"))
    audience = report.get("extensions", {}).get("audience", {})
    if audience.get("mode") != "postgame_public" or audience.get("approved_for_static_public_distribution") is not True:
        raise ValueError("public projection lacks explicit postgame-public approval")
    if report.get("source", {}).get("experiment_id") != experiment_id:
        raise ValueError("projected report experiment identity does not match the arm")
    audit_path = public_root / "projection-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    rows = audit.get("reports", [])
    if len(rows) != 1:
        raise ValueError("expected one projection audit row per one-game arm")
    row_audit = rows[0].get("audit", {})
    for key in ("unresolved_reference_count", "disallowed_private_reference_count", "absolute_path_count"):
        if row_audit.get(key) != 0:
            raise ValueError(f"public projection audit has nonzero {key}")
    return row_audit


def _validate_catalog(catalog_root: Path) -> dict[str, Any]:
    catalog_path = catalog_root / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    reports = catalog.get("reports", [])
    if len(reports) != 1:
        raise ValueError("expected a fresh one-report viewer catalog per arm")
    entry = reports[0]
    if entry.get("approved_for_static_public_distribution") is not True or entry.get("fixture") is not False:
        raise ValueError("viewer catalog did not retain public-safe, nonfixture status")
    return {"path": str(catalog_path), "report_count": len(reports), "entry": entry}


def execute_studies(study_names: Sequence[str], output_root: str | Path, *,
                    config_dir: str | Path = ROOT / "config" / "examples",
                    viewer_root: str | Path = DEFAULT_VIEWER_ROOT,
                    command_runner: Callable[..., dict[str, Any]] = _run_command) -> dict[str, Any]:
    # This complete read-only plan validates all configs/providers/paths before mkdir or file writes.
    destination, viewer, arms = plan_studies(study_names, output_root, config_dir=config_dir, viewer_root=viewer_root)
    planned = _planned_destinations(arms)
    destination.mkdir(parents=False, exist_ok=False)
    configs_root = destination / "configs"
    configs_root.mkdir()
    for arm in arms:
        (configs_root / f"{arm.tag}.yaml").write_bytes(arm.config_bytes)

    run_id = destination.name
    memory_marker = MEMORY_MARKER_PREFIX + re.sub(r"[^A-Za-z0-9]", "", run_id).upper()
    manifest: dict[str, Any] = {
        "schema_version": "viewer_study_run_v1",
        "run_id": run_id,
        "status": "running",
        "study_names": list(dict.fromkeys(arm.study for arm in arms)),
        "catalogs": {arm.tag: f"catalogs/{arm.tag}/catalog.json" for arm in arms},
        "claim_boundary": "Built-in fake provider ignores prompts and persona content; outputs demonstrate plumbing, not gameplay or learning effectiveness.",
        "provider_policy": {"allowed_effective_kind": "fake", "networked_or_env_stub_providers": "rejected before output creation"},
        "run_bounds": {"games_per_arm": 1, "max_year": 1901, "max_provider_calls": MAX_PROVIDER_CALLS, "seed": 20261016},
        "memory_fixture": {
            "marker": memory_marker,
            "source": "synthetically seeded in treatment folios after the warmup game",
            "treatment_agents_root": "agents/memory-treatment",
            "control_agents_root": "agents/mf",
            "claim_boundary": "visibility only; not learned memory or game evidence",
        } if any(arm.tag == "mw" for arm in arms) else None,
        "planned_destinations": list(planned),
        "arms": [],
    }
    _write_json(destination / "study_manifest.json", manifest)
    arm_records: dict[str, dict[str, Any]] = {}
    try:
        for arm in arms:
            run_root = destination / "runs" / arm.tag
            bundle_root = destination / "bundles" / arm.tag
            public_root = destination / "public" / arm.tag
            catalog_root = destination / "catalogs" / arm.tag
            agent_key = arm.agents_root_key
            agents_root = destination / "agents" / agent_key
            experiment_root = run_root / "experiments" / arm.experiment_id
            config_path = configs_root / f"{arm.tag}.yaml"
            record = {
                "study": arm.study,
                "arm": arm.label,
                "tag": arm.tag,
                "experiment_id": arm.experiment_id,
                "source_template": arm.template_path.name,
                "source_template_sha256": arm.template_sha256,
                "effective_config_path": f"configs/{arm.tag}.yaml",
                "effective_config_sha256": arm.config_sha256,
                "seed": arm.config["games"]["seed"],
                "country_assignment": arm.config["games"]["country_assignment"],
                "power_to_agent": {power: agent_id for power, agent_id in zip(POWERS, PLAYER_IDS, strict=True)},
                "provider_config_by_agent": arm.canonical_providers,
                "agents_root": str(agents_root.relative_to(destination)),
                "experiment_root": str(experiment_root.relative_to(destination)),
                "bundle_root": str(bundle_root.relative_to(destination)),
                "public_projection_root": str(public_root.relative_to(destination)),
                "catalog_root": str(catalog_root.relative_to(destination)),
                "commands": [],
                "status": "pending",
            }
            arm_records[arm.tag] = record
            manifest["arms"].append(record)
            _write_json(destination / "study_manifest.json", manifest)

            run_command = [
                sys.executable, str(ROOT / "scripts" / "run_population_experiment.py"),
                "--config", str(config_path),
                "--output-root", os.path.relpath(run_root, ROOT),
                "--agents-root", os.path.relpath(agents_root, ROOT),
            ]
            _run_and_record(
                record, run_command, cwd=ROOT, command_runner=command_runner,
                manifest=manifest, manifest_path=destination / "study_manifest.json",
            )
            run_summary = _require_completed(experiment_root, arm.experiment_id)
            record["run_summary"] = run_summary

            if arm.tag == "mw":
                record["synthetic_memory_sha256"] = _seed_synthetic_memory(
                    agents_root, memory_marker, arm_output=destination / "evidence" / "memory",
                )
                record["status"] = "warmup_complete_memory_seeded"
                _write_json(destination / "study_manifest.json", manifest)
            if arm.tag == "mr":
                record["memory_prompt_visibility"] = audit_memory_prompts(
                    experiment_root, marker=memory_marker, expected_present=True,
                )
            elif arm.tag == "mf" and any(item.tag == "mw" for item in arms):
                record["memory_prompt_visibility"] = audit_memory_prompts(
                    experiment_root, marker=memory_marker, expected_present=False,
                )

            record["status"] = "run_complete"
            export_command = [
                sys.executable, str(ROOT / "scripts" / "export_report_bundle.py"),
                "--experiment-root", str(experiment_root), "--output-root", str(bundle_root),
                "--all-games", "--audience", "omniscient_postgame",
            ]
            _run_and_record(
                record, export_command, cwd=ROOT, command_runner=command_runner,
                manifest=manifest, manifest_path=destination / "study_manifest.json",
            )
            omniscient_reports = sorted(bundle_root.rglob("report.json"))
            if len(omniscient_reports) != 1:
                raise ValueError(f"expected one omniscient report for {arm.tag}")

            project_command = [
                sys.executable, str(ROOT / "scripts" / "project_report_bundle.py"),
                "--source", str(bundle_root), "--output-root", str(public_root),
                "--audience-mode", "postgame_public", "--raw-experiment-root", str(experiment_root),
            ]
            _run_and_record(
                record, project_command, cwd=ROOT, command_runner=command_runner,
                manifest=manifest, manifest_path=destination / "study_manifest.json",
            )
            public_reports = sorted(public_root.rglob("report.json"))
            if len(public_reports) != 1:
                raise ValueError(f"expected one postgame-public report for {arm.tag}")
            record["projection_audit"] = _validate_projection(public_root, public_reports[0], arm.experiment_id)

            node = shutil.which("node")
            if not node:
                raise FileNotFoundError("Node.js disappeared after study preflight")
            for validator in ("validate_schema.ts", "validate_bundle.ts"):
                for report_path in (omniscient_reports[0], public_reports[0]):
                    command = [
                        node, "--experimental-strip-types", str(viewer / "scripts" / validator), str(report_path),
                    ]
                    _run_and_record(
                        record, command, cwd=viewer, command_runner=command_runner,
                        manifest=manifest, manifest_path=destination / "study_manifest.json",
                    )

            catalog_command = [
                node, "--experimental-strip-types", str(viewer / "scripts" / "build_report_catalog.ts"),
                "--source-root", str(public_root), "--output-root", str(catalog_root), "--require-public-safe",
            ]
            _run_and_record(
                record, catalog_command, cwd=viewer, command_runner=command_runner,
                manifest=manifest, manifest_path=destination / "study_manifest.json",
            )
            record["catalog_validation"] = _validate_catalog(catalog_root)
            record["metrics"] = _public_metrics(public_reports[0], experiment_root)
            record["status"] = "complete"
            _write_json(destination / "study_manifest.json", manifest)

        statuses = [record.get("status") for record in manifest["arms"]]
        expected_completed = len(arms)
        if sum(status == "complete" for status in statuses) != expected_completed:
            raise ValueError("one or more study arms did not complete the export/viewer pipeline")
        manifest["status"] = "complete"
        manifest["completed_arms"] = sum(status == "complete" for status in statuses)
        _write_json(destination / "study_manifest.json", manifest)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = f"{exc.__class__.__name__}: {exc}"
        _write_json(destination / "study_manifest.json", manifest)
        raise
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded fake-provider studies through the viewer report pipeline.")
    parser.add_argument("--study", choices=("ticks", "roster", "memory", "all"), default="all")
    parser.add_argument("--output-root", required=True, help="Fresh short destination outside both source checkouts.")
    parser.add_argument("--viewer-root", type=Path, default=DEFAULT_VIEWER_ROOT)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config" / "examples")
    args = parser.parse_args(argv)
    studies = tuple(STUDY_ARMS) if args.study == "all" else (args.study,)
    manifest = execute_studies(studies, args.output_root, config_dir=args.config_dir, viewer_root=args.viewer_root)
    print(json.dumps({"status": manifest["status"], "run_id": manifest["run_id"], "completed_arms": manifest.get("completed_arms", 0)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
