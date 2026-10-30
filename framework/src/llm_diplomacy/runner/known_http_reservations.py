"""Validate retained HTTP-failure reserves without modifying saved state."""
from datetime import datetime


def match_http_reservations(failures, reservations, events):
    """Return attempt-to-reservation bindings, rejecting ambiguous evidence."""
    used = set()
    result = {}
    for failure in failures:
        identity = failure["identity"]
        start = datetime.fromisoformat(failure["started_at"])
        if start.utcoffset() is None:
            raise ValueError("HTTP attempt timestamp is not timezone-aware")
        end = failure.get("next_started_at")
        end = datetime.fromisoformat(end) if end else None
        candidates = []
        for event in events:
            if (event.get("event_type") != "provider_reservation"
                    or event.get("experiment_id") != failure["experiment_id"]):
                continue
            payload = event.get("payload", {})
            if any(payload.get(k) != v for k, v in identity.items()):
                continue
            when = datetime.fromisoformat(event["timestamp"])
            if when.utcoffset() is None or when < start or (end is not None and when >= end):
                continue
            key = payload.get("reservation_id")
            if payload.get("status") != "pending":
                raise ValueError("HTTP reservation lifecycle is not pending")
            actual = reservations.get(key)
            expected = {**payload, "status": "uncertain"}
            settlements = [e for e in events if e.get("event_type") == "provider_reservation_uncertain"
                           and e.get("experiment_id") == failure["experiment_id"]
                           and e.get("payload") == {"reservation_id": key}]
            if actual != expected or len(settlements) != 1:
                raise ValueError("HTTP reservation or uncertain settlement mismatch")
            settled = datetime.fromisoformat(settlements[0]["timestamp"])
            if settled.utcoffset() is None or settled < when or (end is not None and settled >= end):
                raise ValueError("HTTP uncertain settlement is outside the attempt window")
            candidates.append(key)
        if len(candidates) != 1 or candidates[0] in used:
            raise ValueError("HTTP reservation binding is missing or ambiguous")
        used.add(candidates[0])
        result[failure["attempt_id"]] = candidates[0]
    if used != set(reservations):
        raise ValueError("unrelated retained reservation")
    return result
