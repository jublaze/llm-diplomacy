import pytest

from llm_diplomacy.runner.recovery import ExperimentRecovery


def test_orphan_timeout_upgrade_is_rejected():
    recovery = object.__new__(ExperimentRecovery)
    with pytest.raises(ValueError, match="historical source chain"):
        recovery._validate_journal_semantics([{"kind": "timeout_accounting_upgrade_v1", "sequence": 0}])


def test_timeout_upgrade_cannot_precede_http_predecessor():
    recovery = object.__new__(ExperimentRecovery)
    receipt = {"kind": "timeout_accounting_upgrade_v1", "sequence": 1}
    rows = [{"kind": "created"}, receipt]
    with pytest.raises(ValueError, match="identity or package"):
        recovery._validate_timeout_accounting_upgrade({"sequence": 2}, rows, {})
