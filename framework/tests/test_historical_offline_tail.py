import pytest

from llm_diplomacy.runner.recovery import _is_recorded_offline_tail_upgrade


SOURCE = "73c458a7ba5dfea6619c8d5a398edde6121c1df51684e6d8d11aa11238424668"
TARGET = "bb94ed2e5690732d3398a25ebd4d4fa41aa73f9901a29fc3558fd25657c31a2c"
PATH = "runner/offline_tail_transaction.py"
CONTENT = "167f2f0f3e15e010c2747db34c8120cd483f1ffef36cf3026e1c0954abb10499"


@pytest.mark.parametrize("name", ["luna1912-memory-1028-plan", "terra61-1028-g1"])
def test_exact_historical_package_is_allowed(name):
    assert _is_recorded_offline_tail_upgrade(
        {"experiment_id": name, "code_sha256": SOURCE}, {"code_sha256": TARGET}, {}, {PATH: CONTENT})


@pytest.mark.parametrize("changed", ["experiment", "source", "target", "old_path", "content"])
def test_different_package_is_not_allowed(changed):
    source = {"experiment_id": "terra61-1028-g1", "code_sha256": SOURCE}
    target, old, new = {"code_sha256": TARGET}, {}, {PATH: CONTENT}
    if changed == "experiment":
        source["experiment_id"] = "another-run"
    elif changed == "source":
        source["code_sha256"] = "different"
    elif changed == "target":
        target["code_sha256"] = "different"
    elif changed == "old_path":
        old[PATH] = "already-present"
    else:
        new[PATH] = "different"
    assert not _is_recorded_offline_tail_upgrade(source, target, old, new)
