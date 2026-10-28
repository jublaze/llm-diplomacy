from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider_name: str
    api_style: str
    model: str
    base_url: str
    api_key_env_var: str
    supports_temperature: bool
    supports_reasoning_effort: bool
    reasoning_effort_values: tuple[str, ...]
    reasoning_effort_param_style: str
    supports_max_output_tokens: bool
    default_temperature: float | None = None
    default_reasoning_effort: str | None = None
    input_cost_per_1k_tokens: float | None = None
    output_cost_per_1k_tokens: float | None = None
    notes: str = ""
    service_tier: str | None = None
    prompt_cache_mode: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {key:value for key,value in asdict(self).items()
                if key not in {"service_tier", "prompt_cache_mode"} or value is not None}


def default_model_profiles_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "model_profiles"


def load_model_profile(profile_id: str, *, profiles_dir: str | Path | None = None) -> ModelProfile:
    normalized_id = str(profile_id).strip()
    if not normalized_id:
        raise ValueError("model_profile must be a non-empty string.")
    root = Path(profiles_dir) if profiles_dir is not None else default_model_profiles_dir()
    path = root / f"{normalized_id}.yaml"
    if not path.exists():
        raise ValueError(f"Unknown model profile: {normalized_id}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Model profile must be an object: {path}")
    profile = _profile_from_raw(raw, source=str(path))
    if profile.id != normalized_id:
        raise ValueError(f"Model profile id mismatch in {path}: expected {normalized_id}, got {profile.id}")
    return profile


def resolve_model_profile_config(
    raw: dict[str, Any], *, profiles_dir: str | Path | None = None
) -> tuple[ModelProfile, dict[str, Any]]:
    profile = load_model_profile(str(raw.get("model_profile", "")), profiles_dir=profiles_dir)
    resolved: dict[str, Any] = {
        "kind": "responses" if profile.api_style == "responses" else "openai_compatible",
        "provider_name": profile.provider_name,
        "api_style": profile.api_style,
        "model_profile": profile.id,
        "model": profile.model,
        "base_url": profile.base_url,
        "api_key_env_var": profile.api_key_env_var,
        "supports_temperature": profile.supports_temperature,
        "supports_reasoning_effort": profile.supports_reasoning_effort,
        "reasoning_effort_values": list(profile.reasoning_effort_values),
        "reasoning_effort_param_style": profile.reasoning_effort_param_style,
        "supports_max_output_tokens": profile.supports_max_output_tokens,
        "temperature": profile.default_temperature,
        "reasoning_effort": profile.default_reasoning_effort,
        "pricing": {
            "input_cost_per_1k_tokens": profile.input_cost_per_1k_tokens,
            "output_cost_per_1k_tokens": profile.output_cost_per_1k_tokens,
        },
    }
    for key, value in raw.items():
        if key != "model_profile":
            resolved[key] = value
    for key in ("service_tier", "prompt_cache_mode"):
        if key not in resolved and getattr(profile, key) is not None:
            resolved[key] = getattr(profile, key)
    _validate_resolved_overrides(profile, resolved)
    return profile, resolved


def _profile_from_raw(raw: dict[str, Any], *, source: str) -> ModelProfile:
    required = ("id", "provider_name", "api_style", "model", "base_url", "api_key_env_var")
    missing = [key for key in required if not isinstance(raw.get(key), str) or not str(raw.get(key)).strip()]
    if missing:
        raise ValueError(f"Missing required model profile fields in {source}: {missing}")
    api_style = str(raw["api_style"]).strip()
    if api_style not in {"chat_completions", "responses"}:
        raise ValueError(f"Unsupported api_style in {source}: {api_style}")
    param_style = str(raw.get("reasoning_effort_param_style", "none")).strip()
    if param_style not in {"nested_reasoning", "top_level", "none"}:
        raise ValueError(f"Unsupported reasoning_effort_param_style in {source}: {param_style}")
    values_raw = raw.get("reasoning_effort_values", [])
    if not isinstance(values_raw, list) or not all(isinstance(row, str) for row in values_raw):
        raise ValueError(f"reasoning_effort_values must be list[str] in {source}")
    if raw.get("service_tier") not in (None,"default") or raw.get("prompt_cache_mode") not in (None,"explicit"):
        raise ValueError("Unsupported profile service/cache controls")
    if (raw.get("service_tier") is not None or raw.get("prompt_cache_mode") is not None) and api_style != "responses":
        raise ValueError("Profile service/cache controls require Responses")
    return ModelProfile(
        service_tier=raw.get("service_tier"), prompt_cache_mode=raw.get("prompt_cache_mode"),
        id=str(raw["id"]).strip(),
        provider_name=str(raw["provider_name"]).strip(),
        api_style=api_style,
        model=str(raw["model"]).strip(),
        base_url=str(raw["base_url"]).rstrip("/"),
        api_key_env_var=str(raw["api_key_env_var"]).strip(),
        supports_temperature=bool(raw.get("supports_temperature", False)),
        supports_reasoning_effort=bool(raw.get("supports_reasoning_effort", False)),
        reasoning_effort_values=tuple(str(row) for row in values_raw),
        reasoning_effort_param_style=param_style,
        supports_max_output_tokens=bool(raw.get("supports_max_output_tokens", False)),
        default_temperature=_optional_float(raw.get("default_temperature")),
        default_reasoning_effort=_optional_string(raw.get("default_reasoning_effort")),
        input_cost_per_1k_tokens=_optional_float(raw.get("input_cost_per_1k_tokens")),
        output_cost_per_1k_tokens=_optional_float(raw.get("output_cost_per_1k_tokens")),
        notes=str(raw.get("notes", "")),
    )


def _validate_resolved_overrides(profile: ModelProfile, resolved: dict[str, Any]) -> None:
    effort = resolved.get("reasoning_effort")
    if effort is not None:
        if not profile.supports_reasoning_effort:
            raise ValueError(f"Model profile {profile.id} does not support reasoning_effort.")
        if profile.reasoning_effort_values and effort not in profile.reasoning_effort_values:
            raise ValueError(f"Unsupported reasoning_effort for {profile.id}: {effort}")
    if resolved.get("temperature") is not None and not profile.supports_temperature:
        raise ValueError(f"Model profile {profile.id} does not support temperature.")
    if resolved.get("max_output_tokens") is not None and not profile.supports_max_output_tokens:
        raise ValueError(f"Model profile {profile.id} does not support max_output_tokens.")


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _optional_string(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, str) and value.strip() else None
