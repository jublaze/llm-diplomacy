import json

import pytest

from llm_diplomacy.compliance.replay import compare_experiment_dirs


def pair(tmp_path):
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "experiment_id": "x"}))
        (root / "state.json").write_text('{"units": ["a"]}')
    return roots


def test_empty_missing_and_manifest_only_runs_fail(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert not compare_experiment_dirs(a, b).deterministic
    a, b = pair(tmp_path)
    for root in (a, b):
        (root / "state.json").unlink()
    assert not compare_experiment_dirs(a, b).deterministic
    for root in (a, b):
        (root / "manifest.json").unlink()
    assert not compare_experiment_dirs(a, b).deterministic


@pytest.mark.parametrize("field", ["source_path", "evidence_path", "timestamp", "created_at", "input_tokens"])
def test_evidence_and_usage_changes_remain_visible(tmp_path, field):
    a, b = pair(tmp_path)
    for root, value in ((a, "first"), (b, "second")):
        (root / "evidence.json").write_text(json.dumps({"payload": {"event_type": "quoted_evidence", field: value}}))
    result = compare_experiment_dirs(a, b)
    assert not result.deterministic
    assert any(f"#/payload/{field}" in item for item in result.differences)


def test_known_paths_are_relocated_not_discarded(tmp_path):
    a, b = pair(tmp_path)
    for root in (a, b):
        (root / "summary.json").write_text(json.dumps({"cost_summary_path": str(root / "cost.json")}))
    assert compare_experiment_dirs(a, b).deterministic
    (b / "summary.json").write_text(json.dumps({"cost_summary_path": str(b / "wrong.json")}))
    assert not compare_experiment_dirs(a, b).deterministic


@pytest.mark.parametrize("filename,contents", [("bad.json", "{bad"), ("bad.jsonl", '{}\n{bad')])
def test_invalid_input_returns_diagnostic_not_exception(tmp_path, filename, contents):
    a, b = pair(tmp_path)
    for root in (a, b):
        (root / filename).write_text(contents)
    result = compare_experiment_dirs(a, b)
    assert not result.deterministic
    assert any("invalid_artifact_a" in item for item in result.differences)
    assert all("{bad" not in item for item in result.differences)


def test_boolean_is_not_a_numeric_counter(tmp_path):
    a, b = pair(tmp_path)
    (a / "state.json").write_text('{"count": true}')
    (b / "state.json").write_text('{"count": 1}')
    assert not compare_experiment_dirs(a, b).deterministic
