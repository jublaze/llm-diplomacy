import copy
import pytest

from llm_diplomacy.runner.known_http_reservations import match_http_reservations


def fixture():
    identity = {"game_id": "g", "phase_id": "p", "agent_id": "a", "method": "reflect"}
    payload = {**identity, "reservation_id": "r", "status": "pending", "input_tokens": 9,
               "output_tokens": 10, "cost_usd": 0.1}
    events = [{"event_type": "provider_reservation", "experiment_id": "e",
               "timestamp": "2026-01-01T00:00:01+00:00", "payload": payload},
              {"event_type": "provider_reservation_uncertain", "experiment_id": "e",
               "timestamp": "2026-01-01T00:00:02+00:00", "payload": {"reservation_id": "r"}}]
    failures = [{"attempt_id": "different-id", "experiment_id": "e", "identity": identity,
                 "started_at": "2026-01-01T00:00:00+00:00"}]
    return failures, {"r": {**payload, "status": "uncertain"}}, events


def test_binds_distinct_ids_and_retains_reserve():
    failures, reservations, events = fixture()
    before = copy.deepcopy(reservations)
    assert match_http_reservations(failures, reservations, events) == {"different-id": "r"}
    assert reservations == before


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "wrong_status", "wrong_identity", "extra", "wrong_experiment"])
def test_rejects_ambiguous_or_unrelated_evidence(mutation):
    failures, reservations, events = fixture()
    if mutation == "duplicate": events.append(copy.deepcopy(events[0]))
    if mutation == "missing": events.pop()
    if mutation == "wrong_status": reservations["r"]["status"] = "pending"
    if mutation == "wrong_identity": failures[0]["identity"]["agent_id"] = "other"
    if mutation == "extra": reservations["other"] = copy.deepcopy(reservations["r"])
    if mutation == "wrong_experiment": events[0]["experiment_id"] = "other"
    with pytest.raises(ValueError): match_http_reservations(failures, reservations, events)


def test_rejects_settlement_after_next_attempt():
    failures, reservations, events = fixture()
    failures[0]["next_started_at"] = "2026-01-01T00:00:02+00:00"
    with pytest.raises(ValueError, match="attempt window"):
        match_http_reservations(failures, reservations, events)
