"""Version checks for raw artifact envelopes; nested payloads own their contracts."""

from typing import Any, Mapping

RAW_LAYOUT_VERSION = "1.0"


def raw_version_error(payload: Any, expected: str = RAW_LAYOUT_VERSION) -> str | None:
    if not isinstance(payload, Mapping):
        return "raw artifact must be an object"
    version = payload.get("schema_version")
    if version != expected:
        return f"unsupported schema_version {version!r}; expected raw layout {expected}"
    return None
