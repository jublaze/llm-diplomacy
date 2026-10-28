"""Build checked-in Phase 0 fixtures directly from completed experiment artifacts."""
from __future__ import annotations
import argparse, hashlib, json
from collections import defaultdict
from pathlib import Path
from game_001_editorial import apply_editorial

ROOT = Path(__file__).resolve().parents[1]

def load(path: Path): return json.loads(path.read_text(encoding="utf-8"))
def digest(path: Path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path: Path, experiment: Path): return path.relative_to(experiment).as_posix()
def board(snapshot):
    p = snapshot["payload"]
    return {"supply_ownership": p["supply_ownership"], "units": p["units"], "standoff_provinces": p.get("standoff_provinces", [])}
def source(index, path: Path, experiment: Path, kind, visibility, pointer=None):
    key = f"source_{hashlib.sha256((rel(path, experiment) + (pointer or '')).encode()).hexdigest()[:16]}"
    if key not in index: index[key] = {"source_id": key, "artifact_path": rel(path, experiment), "sha256": digest(path), "visibility": visibility, "kind": kind, **({"json_pointer": pointer} if pointer else {})}
    return key
def phase_parts(phase_id):
    year, season, kind = phase_id.split("_", 2)
    return int(year), season, kind
def normalized_outcome(raw, final_year, scheduled_max_year=None):
    countries = raw.get("country_results", {})
    leaders = sorted(({"agent_id": item["agent_id"], "power": power, "supply_centers": item["owned_centers"]} for power, item in countries.items()), key=lambda item: (-item["supply_centers"], item["power"]))
    leaders = leaders[:1] if leaders else []
    recorded_result = raw.get("result")
    claimed = raw.get("solo_victor", {})
    final_phase_id = raw.get("final_phase_id")
    solo = leaders[0] if (
        len(leaders) == 1 and leaders[0]["supply_centers"] >= 18 and recorded_result == "solo_victory"
        and final_phase_id in {f"{final_year}_fall_movement", f"{final_year}_fall_retreat"}
        and claimed.get("agent_id") == leaders[0]["agent_id"]
        and claimed.get("power") == leaders[0]["power"]
        and claimed.get("owned_centers") == leaders[0]["supply_centers"]
    ) else None
    if recorded_result == "draw": termination = {"kind": "draw", "label": "Recorded draw", "final_year": final_year}
    elif solo: termination = {"kind": "solo_victory", "label": f"Solo victory by {solo['power'].title()} ({solo['supply_centers']} supply centers)", "final_year": final_year, "final_phase_id": final_phase_id}
    elif recorded_result == "solo_victory": termination = {"kind": "other_recorded_reason", "label": "Recorded solo-victory result lacks matching final Fall evidence", "final_year": final_year}
    elif recorded_result == "completed" and isinstance(scheduled_max_year, int) and not isinstance(scheduled_max_year, bool) and final_year == scheduled_max_year: termination = {"kind": "scheduled_max_year", "label": f"Leader at the scheduled {scheduled_max_year} cutoff", "final_year": final_year}
    else: termination = {"kind": "other_recorded_reason", "label": "Recorded end reason is not sufficient to classify a solo victory or draw", "final_year": final_year}
    return {"termination": termination, "leaders": leaders, "solo_victor": solo}

def main():
    parser = argparse.ArgumentParser(description="Build report fixtures from a completed experiment root.")
    parser.add_argument("--source-root", required=True, help="Path to the completed experiment root.")
    parser.add_argument("--output-root", default=str(ROOT / "fixtures"), help="Destination fixture directory (default: ./fixtures).")
    parser.add_argument("--scheduled-max-year", type=int, help="Declared schedule cutoff year; omitted unless the source run summary records it.")
    args = parser.parse_args()
    experiment = Path(args.source_root).resolve()
    if not (experiment / "manifest.json").is_file() or not (experiment / "games").is_dir(): parser.error("--source-root must be a completed experiment root containing manifest.json and games/")
    output_root = Path(args.output_root).resolve()
    experiment_id = experiment.name
    manifest_hash = digest(experiment / "manifest.json")
    roster_manifest = load(experiment / "population_manifest.json")
    run_summary = load(experiment / "run_summary.json")
    population_by_id = {agent["agent_id"]: agent for agent in roster_manifest["agents"]}
    cost_summary = load(experiment / "metrics" / "cost_summary.json")
    for game_id in ("game_001", "game_002"):
        game = experiment / "games" / game_id
        evidence, message_map, order_map, record_map = {}, {}, {}, {}
        snapshots = {(p.stem.removesuffix("_state")): (p, load(p)) for p in (game / "state" / "snapshots").glob("*.json")}
        previous = snapshots["initial"][1]
        # Preserve every original message exactly once, retaining recipient context and source line pointer.
        messages_by_phase = defaultdict(list)
        seen = set()
        for file in sorted((game / "messages" / "private").glob("*/*.jsonl")):
            for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                raw = json.loads(line)
                if raw["message_id"] in seen: continue
                seen.add(raw["message_id"]); sid = source(evidence, file, experiment, "message", "private", f"/line/{line_no}")
                recipients = [raw["to"]["to_agent_id"]]
                sender = raw["from_agent_id"]
                thread = "__".join(sorted([sender, *recipients]))
                msg = {"message_id": raw["message_id"], "phase_id": raw["phase_id"], "chronological_position": raw.get("tick", 0), "sender_agent_id": sender, "recipient_agent_ids": recipients, "channel": "private", "thread_id": thread, "text": raw["body"], "visibility": "private", "source_ref": sid}
                message_map[msg["message_id"]] = msg; messages_by_phase[msg["phase_id"]].append(msg["message_id"])
        global_file = game / "messages" / "global" / "global.jsonl"
        if global_file.exists():
            for line_no, line in enumerate(global_file.read_text(encoding="utf-8").splitlines(), 1):
                raw = json.loads(line); sid = source(evidence, global_file, experiment, "message", "public", f"/line/{line_no}")
                msg = {"message_id": raw["message_id"], "phase_id": raw["phase_id"], "chronological_position": raw.get("tick", 0), "sender_agent_id": raw["from_agent_id"], "recipient_agent_ids": [], "channel": "global", "thread_id": "global", "text": raw["body"], "visibility": "public", "source_ref": sid}
                message_map[msg["message_id"]] = msg; messages_by_phase[msg["phase_id"]].append(msg["message_id"])
        chronology = {x["phase_id"]: x for x in load(game / "reporter" / "secret_history.json")["chronology"]}
        reporter = game / "reporter" / "secret_history.json"; reporter_source = source(evidence, reporter, experiment, "report", "omniscient_postgame")
        phases = []
        for phase_id in sorted(k for k in snapshots if k != "initial"):
            year, season, kind = phase_parts(phase_id); snapshot_file, snapshot = snapshots[phase_id]
            board_after = board(snapshot); snapshot_source = source(evidence, snapshot_file, experiment, "board_snapshot", "public", "/payload")
            phase_dir = game / "phases" / phase_id
            submitted_file = phase_dir / "orders" / "submitted_orders.json"; resolution_file = phase_dir / "resolution" / "resolution.json"
            submitted, resolution = load(submitted_file)["orders"], load(resolution_file)
            order_source = source(evidence, submitted_file, experiment, "orders", "public"); resolution_source = source(evidence, resolution_file, experiment, "adjudication", "public")
            order_ids = []
            for item in snapshot["payload"].get("latest_public_resolution", {}).get("public_final_orders", []):
                oid = f"{phase_id}:{item['unit_id']}"; order_map[oid] = {**item, "source_ref": order_source}; order_ids.append(oid)
            record_ids = []
            for reflection_file in sorted((phase_dir / "reflection").glob("*.json")):
                agent = reflection_file.stem; rid = f"{phase_id}:{agent}:reflection"; record_map[rid] = {"record_id": rid, "agent_id": agent, "record_type": "reflection", "visibility": "private", "payload": load(reflection_file), "source_ref": source(evidence, reflection_file, experiment, "private_record", "private")}; record_ids.append(rid)
            negotiation = f"{year}_{season}_negotiation"
            entry = chronology.get(phase_id, {})
            phases.append({"phase_id": phase_id, "kind": kind, "year": year, "season": season, "board_before": board(previous), "board_after": board_after, "messages": messages_by_phase.get(negotiation, []) + messages_by_phase.get(phase_id, []), "orders": order_ids, "adjudication": resolution, "private_records": record_ids, "narrative": {"overview": entry.get("summary", "No postgame narrative available for this phase."), "theater_sections": [], "aftermath": "", "source_refs": [reporter_source]}, "source_refs": [snapshot_source, order_source, resolution_source, reporter_source], "extensions": {"submitted_order_count": len(submitted), "negotiation_context_phase": negotiation}})
            previous = snapshot
        actual_years = sorted({phase["year"] for phase in phases})
        if not actual_years or actual_years[0] < 1901 or any(current != previous + 1 for previous, current in zip(actual_years, actual_years[1:])):
            raise ValueError(f"{game_id} phase evidence has missing, invalid, or non-contiguous played years")
        years = [{"year": year, "phases": [phase for phase in phases if phase["year"] == year]} for year in actual_years]
        # The completed-game event is the authoritative assignment and outcome record.
        game_result = {}
        for line in (game / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("phase_id") == "game_end" and "country_results" in event.get("payload", {}).get("result", {}):
                game_result = event["payload"]["result"]
                break
        assignment = {entry["agent_id"]: power for power, entry in game_result.get("country_results", {}).items()}
        roster = []
        for agent_dir in sorted((game / "agents").iterdir()):
            profile = load(agent_dir / "folio" / "manifest.json")
            person = population_by_id[agent_dir.name]
            power = assignment[agent_dir.name]
            roster.append({"agent_id": agent_dir.name, "display_name": profile.get("display_name", person["display_name"]), "profile_version": profile.get("schema_version", "unknown"), "power": power, "power_assignment_history": [{"game_id": game_id, "power": power}], "runtime": person["runtime"]})
        final_year = actual_years[-1]
        scheduled_max_year = args.scheduled_max_year
        if scheduled_max_year is None:
            recorded_cutoff = run_summary.get("scheduled_max_year")
            scheduled_max_year = recorded_cutoff if isinstance(recorded_cutoff, int) and not isinstance(recorded_cutoff, bool) else None
        bundle = {"schema_version": "diplomacy_report_bundle_v1", "bundle_id": f"{experiment_id}:{game_id}:v1", "source": {"experiment_id": experiment_id, "game_id": game_id, "artifact_manifest_sha256": manifest_hash, "visibility_mode": "omniscient_postgame"}, "map": {"map_id": "standard_v1", "geometry_version": "standard_geometry_v1"}, "game": {"title": f"{experiment_id} / {game_id}", "years_played": [actual_years[0], final_year], "stop_condition": game_result.get("result", "completed"), "outcome": normalized_outcome(game_result, final_year, scheduled_max_year), "model_provenance": roster_manifest["model_provider"], "cost_and_call_metadata": cost_summary, "provenance": {"raw_outcome": game_result, "experiment_manifest": rel(experiment / "manifest.json", experiment), "run_summary": run_summary}}, "roster": roster, "summary": {"chronology_source_ref": reporter_source}, "strategic_arcs": [], "years": years, "messages": message_map, "orders": order_map, "private_records": record_map, "evidence_index": evidence, "extensions": {"batch_b": {}, "normalization": {"generator": "scripts/build_fixtures.py"}}}
        if game_id == "game_001":
            apply_editorial(bundle)
        output = output_root / game_id / "report.json"; output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(output)
if __name__ == "__main__": main()
