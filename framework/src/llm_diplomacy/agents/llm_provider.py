from __future__ import annotations

import os
import urllib.error
import urllib.request
import json
import hashlib
import math
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from llm_diplomacy.guardrails.budget import BudgetController, BudgetDecision
from llm_diplomacy.metrics.provider_usage import ProviderUsageTracker

from .model_profiles import resolve_model_profile_config


@dataclass(frozen=True)
class LLMRequest:
    schema_version: str
    agent_id: str
    method: str
    prompt: str
    context: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    schema_version: str
    content: str
    model: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class LLMProviderConfig:
    kind: str
    model: str
    temperature: float | None
    max_output_tokens: int | None
    timeout_seconds: int
    base_url: str | None
    api_key_env_var: str
    input_cost_per_1k_tokens: float | None = None
    output_cost_per_1k_tokens: float | None = None
    provider_name: str = "unknown"
    api_style: str = "chat_completions"
    model_profile: str | None = None
    reasoning_effort: str | None = None
    reasoning_effort_param_style: str = "none"
    supports_temperature: bool = True
    supports_reasoning_effort: bool = False
    supports_max_output_tokens: bool = True
    fallback_providers: tuple["LLMProviderConfig", ...] = ()
    service_tier: str | None = None
    prompt_cache_mode: str | None = None

    def metadata(self, *, resolved_model: str | None = None) -> dict[str, Any]:
        return {
            **({"service_tier": self.service_tier} if self.service_tier is not None else {}),
            **({"prompt_cache_mode": self.prompt_cache_mode} if self.prompt_cache_mode is not None else {}),
            "provider_name": self.provider_name,
            "api_style": self.api_style,
            "requested_model_profile": self.model_profile,
            "requested_model": self.model,
            "resolved_model": resolved_model,
            "reasoning_effort": self.reasoning_effort,
            "base_url": self.base_url,
        }


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


class ProviderHTTPError(RuntimeError):
    def __init__(self, *, status_code: int, error_body: str, metadata: dict[str, Any], retry_after_seconds: float | None = None) -> None:
        super().__init__(f"Provider HTTP error: {status_code}")
        self.status_code = status_code
        self.error_body = error_body
        self.metadata = dict(metadata)
        self.retry_after_seconds = min(30.0, max(0.0, retry_after_seconds)) if retry_after_seconds is not None else None

    def artifact_data(self) -> dict[str, Any]:
        return {"status_code": self.status_code, "error_body": self.error_body, **self.metadata}

    @property
    def category(self) -> str:
        body = self.error_body.lower()
        try:
            payload = json.loads(self.error_body)
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = str(error.get("code", "")).lower() if isinstance(error, dict) else ""
            error_type = str(error.get("type", "")).lower() if isinstance(error, dict) else ""
        except (TypeError, ValueError):
            code, error_type = "", ""
        if code in {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit"} or error_type in {"insufficient_quota", "billing"}:
            return "provider_quota"
        if self.status_code in {401, 403}:
            return "provider_auth"
        if self.status_code == 429:
            if any(word in body for word in ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit")):
                return "provider_quota"
            return "provider_rate_limit"
        if self.status_code in {408, 409} or self.status_code >= 500:
            return "provider_transient_http"
        return "provider_http"


class ProviderNetworkError(RuntimeError):
    pass


class ProviderUnavailable(RuntimeError):
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise ProviderUnavailable("Configured provider route is unavailable.")


def classify_provider_error(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "provider_timeout"
    if isinstance(exc, ProviderNetworkError):
        return "provider_network"
    if isinstance(exc, ProviderUnavailable):
        return "provider_unavailable"
    if isinstance(exc, ProviderHTTPError):
        return exc.category
    return "provider_error"


_SUPPORTED_PROVIDER_KINDS = {"fake", "env_stub", "openai_compatible", "real_openai_compatible", "responses", "codex_cli"}


def parse_llm_provider_config(raw: Any, *, require_selection: bool = False) -> LLMProviderConfig:
    """Resolve one provider selection without ever retaining credential values."""
    if raw is None:
        return LLMProviderConfig(
            kind="fake",
            model="fake-llm",
            temperature=None,
            max_output_tokens=None,
            timeout_seconds=600,
            base_url=None,
            api_key_env_var="OPENAI_API_KEY",
            input_cost_per_1k_tokens=None,
            output_cost_per_1k_tokens=None,
            provider_name="fake",
            api_style="none",
        )
    if isinstance(raw, str):
        kind = raw.strip().lower()
        if kind in {"fake", "env_stub"}:
            return LLMProviderConfig(
                kind=kind,
                model="fake-llm" if kind == "fake" else "env-stub-llm",
                temperature=None,
                max_output_tokens=None,
                timeout_seconds=600,
                base_url=None,
                api_key_env_var="OPENAI_API_KEY",
                input_cost_per_1k_tokens=None,
                output_cost_per_1k_tokens=None,
                provider_name=kind,
                api_style="none",
            )
        raise ValueError(f"Unsupported llm_provider string value: {raw}")
    if not isinstance(raw, dict):
        raise ValueError("llm_provider must be string or object.")
    raw = dict(raw)
    fallback_raw = raw.pop("fallback_providers", [])
    if not isinstance(fallback_raw, list) or any(not isinstance(item, dict) for item in fallback_raw):
        raise ValueError("fallback_providers must be an ordered list of provider configuration objects.")
    if len(fallback_raw) > 2:
        raise ValueError("At most two explicit fallback providers are allowed per decision.")
    fallback_configs = tuple(parse_llm_provider_config(item, require_selection=True) for item in fallback_raw)
    if any(item.fallback_providers for item in fallback_configs):
        raise ValueError("fallback provider entries cannot define nested fallback_providers.")
    if "endpoint" in raw and "base_url" not in raw:
        raw["base_url"] = raw.pop("endpoint")
    if str(raw.get("kind", "")).strip().lower() == "codex_cli":
        allowed = {
            "kind", "model", "reasoning_effort", "timeout_seconds", "provider_name",
            "api_style", "base_url", "api_key_env_var", "temperature", "max_output_tokens",
            "pricing", "model_profile", "reasoning_effort_param_style", "supports_temperature",
            "supports_reasoning_effort", "supports_max_output_tokens",
        }
        unsupported = sorted(set(raw) - allowed)
        if unsupported:
            raise ValueError(f"codex_cli does not support provider options: {unsupported}")
        forbidden_values = (
            raw.get("api_style", "codex_cli") != "codex_cli",
            raw.get("base_url") is not None,
            raw.get("api_key_env_var", "not_applicable") != "not_applicable",
            raw.get("temperature") is not None,
            raw.get("max_output_tokens") is not None,
            raw.get("model_profile") is not None,
            raw.get("pricing", {"input_cost_per_1k_tokens": None, "output_cost_per_1k_tokens": None})
            != {"input_cost_per_1k_tokens": None, "output_cost_per_1k_tokens": None},
            raw.get("reasoning_effort_param_style", "none") != "none",
            raw.get("supports_temperature", False) is not False,
            raw.get("supports_reasoning_effort", True) is not True,
            raw.get("supports_max_output_tokens", False) is not False,
        )
        if any(forbidden_values):
            raise ValueError("codex_cli does not support API-specific provider options.")
        if not isinstance(raw.get("model"), str) or not raw["model"].strip():
            raise ValueError("codex_cli requires model as a non-empty string.")
        if not isinstance(raw.get("reasoning_effort"), str):
            raise ValueError("codex_cli requires reasoning_effort as a string.")
        requested_timeout = raw.get("timeout_seconds", 30)
        if not isinstance(requested_timeout, int) or isinstance(requested_timeout, bool):
            raise ValueError("codex_cli timeout_seconds must be an integer from 1 to 600.")
        if not 1 <= requested_timeout <= 600:
            raise ValueError("codex_cli timeout_seconds must be an integer from 1 to 600.")
    _validate_provider_selection(raw, require_selection=require_selection)
    if isinstance(raw.get("model_profile"), str):
        original_kind = str(raw.get("kind", "")).strip().lower()
        _profile, raw = resolve_model_profile_config(raw)
        if original_kind and original_kind != str(raw.get("kind", "")).strip().lower():
            raise ValueError("model_profile conflicts with provider kind.")
    kind = str(raw.get("kind", "fake")).strip().lower()
    model = str(raw.get("model", "fake-llm")).strip()
    temperature = raw.get("temperature")
    max_output_tokens = raw.get("max_output_tokens")
    timeout_default = 30 if kind == "codex_cli" else 600
    timeout_raw = raw.get("timeout_seconds", timeout_default)
    if not isinstance(timeout_raw, int) or isinstance(timeout_raw, bool) or not 1 <= timeout_raw <= 600:
        raise ValueError("timeout_seconds must be an integer from 1 to 600.")
    timeout_seconds = timeout_raw
    base_url = raw.get("base_url")
    api_key_env_var = str(raw.get("api_key_env_var", "OPENAI_API_KEY")).strip()
    provider_name = str(raw.get("provider_name", "openai" if kind in {"openai_compatible", "real_openai_compatible"} else kind)).strip()
    api_style = str(raw.get("api_style", "codex_cli" if kind == "codex_cli" else "responses" if kind == "responses" else "chat_completions")).strip()
    model_profile_raw = raw.get("model_profile")
    model_profile = model_profile_raw.strip() if isinstance(model_profile_raw, str) and model_profile_raw.strip() else None
    reasoning_raw = raw.get("reasoning_effort")
    reasoning_effort = reasoning_raw.strip() if isinstance(reasoning_raw, str) and reasoning_raw.strip() else None
    reasoning_effort_param_style = str(raw.get("reasoning_effort_param_style", "none")).strip()
    pricing = raw.get("pricing", {})
    input_cost_per_1k_tokens = None
    output_cost_per_1k_tokens = None
    if isinstance(pricing, dict):
        if isinstance(pricing.get("input_cost_per_1k_tokens"), (int, float)):
            input_cost_per_1k_tokens = float(pricing["input_cost_per_1k_tokens"])
        if isinstance(pricing.get("output_cost_per_1k_tokens"), (int, float)):
            output_cost_per_1k_tokens = float(pricing["output_cost_per_1k_tokens"])
    for label, price in (("input_cost_per_1k_tokens", input_cost_per_1k_tokens), ("output_cost_per_1k_tokens", output_cost_per_1k_tokens)):
        if price is not None and (not math.isfinite(price) or price < 0):
            raise ValueError(f"{label} must be a finite non-negative number.")
    if max_output_tokens is not None and (not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens <= 0):
        raise ValueError("max_output_tokens must be a positive integer when provided.")
    if kind not in _SUPPORTED_PROVIDER_KINDS:
        raise ValueError(f"Unsupported llm provider kind: {kind}")
    if kind in {"openai_compatible", "real_openai_compatible", "responses"} and not model:
        raise ValueError("Real provider config requires non-empty 'model'.")
    if kind == "codex_cli":
        if not model or model == "fake-llm":
            raise ValueError("codex_cli requires a non-empty model.")
        if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("codex_cli requires reasoning_effort: low, medium, high, xhigh, or max.")
    if api_style not in {"none", "chat_completions", "responses", "codex_cli"}:
        raise ValueError(f"Unsupported provider api_style: {api_style}")
    if kind == "codex_cli" and api_style != "codex_cli":
        raise ValueError("Codex CLI provider requires api_style 'codex_cli'.")
    if kind == "responses" and api_style != "responses":
        raise ValueError("Responses provider requires api_style 'responses'.")
    if kind in {"openai_compatible", "real_openai_compatible"} and api_style != "chat_completions":
        raise ValueError("OpenAI-compatible provider requires api_style 'chat_completions'.")
    service_tier, cache_mode = raw.get("service_tier"), raw.get("prompt_cache_mode")
    if service_tier not in (None, "default") or cache_mode not in (None, "explicit"):
        raise ValueError("Only explicit Standard service and explicit-only cache mode are supported")
    if (service_tier is not None or cache_mode is not None) and api_style != "responses":
        raise ValueError("Service/cache controls require the Responses API")
    return LLMProviderConfig(
        service_tier=service_tier, prompt_cache_mode=cache_mode,
        kind=kind,
        model=model,
        temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
        max_output_tokens=int(max_output_tokens) if isinstance(max_output_tokens, int) else None,
        timeout_seconds=timeout_seconds,
        base_url=str(base_url).strip() if isinstance(base_url, str) and base_url.strip() else None,
        api_key_env_var="not_applicable" if kind == "codex_cli" else api_key_env_var,
        input_cost_per_1k_tokens=input_cost_per_1k_tokens,
        output_cost_per_1k_tokens=output_cost_per_1k_tokens,
        provider_name=str(raw.get("provider_name", "codex_cli")).strip() if kind == "codex_cli" else provider_name,
        api_style=api_style,
        model_profile=model_profile,
        reasoning_effort=reasoning_effort,
        reasoning_effort_param_style=reasoning_effort_param_style,
        supports_temperature=False if kind == "codex_cli" else bool(raw.get("supports_temperature", True)),
        supports_reasoning_effort=True if kind == "codex_cli" else bool(raw.get("supports_reasoning_effort", False)),
        supports_max_output_tokens=False if kind == "codex_cli" else bool(raw.get("supports_max_output_tokens", True)),
        fallback_providers=fallback_configs,
    )


def _validate_provider_selection(raw: dict[str, Any], *, require_selection: bool) -> None:
    if require_selection and not any(str(raw.get(key, "")).strip() for key in ("model_profile", "kind")):
        raise ValueError("Explicit provider_config requires model_profile or kind.")
    for key, value in raw.items():
        normalized = str(key).strip().lower()
        if normalized in {"api_key", "credential", "credentials", "token", "access_token"} or (
            normalized.endswith("_key") and normalized != "api_key_env_var"
        ):
            if value not in (None, ""):
                raise ValueError("Provider credentials must be supplied by environment-variable name, not inline.")
    if "model_profile" in raw and raw.get("model_profile") is not None and not isinstance(raw.get("model_profile"), str):
        raise ValueError("model_profile must be a non-empty string.")
    if "kind" in raw and not isinstance(raw.get("kind"), str):
        raise ValueError("provider kind must be a string.")
    if "api_key_env_var" in raw and not str(raw.get("api_key_env_var", "")).strip():
        raise ValueError("api_key_env_var must be a non-empty environment-variable name.")


def canonical_provider_config(raw: Any, *, require_selection: bool = False) -> dict[str, Any]:
    """Stable, safe provider provenance used for reuse and mixed-run metadata."""
    cfg = raw if isinstance(raw, LLMProviderConfig) else parse_llm_provider_config(raw, require_selection=require_selection)
    return {
        **({"service_tier": cfg.service_tier} if cfg.service_tier is not None else {}),
        **({"prompt_cache_mode": cfg.prompt_cache_mode} if cfg.prompt_cache_mode is not None else {}),
        "kind": cfg.kind,
        "provider_name": cfg.provider_name,
        "api_style": cfg.api_style,
        "model_profile": cfg.model_profile,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key_env_var": cfg.api_key_env_var,
        "temperature": cfg.temperature,
        "max_output_tokens": cfg.max_output_tokens,
        "timeout_seconds": cfg.timeout_seconds,
        "reasoning_effort": cfg.reasoning_effort,
        "reasoning_effort_param_style": cfg.reasoning_effort_param_style,
        "supports_temperature": cfg.supports_temperature,
        "supports_reasoning_effort": cfg.supports_reasoning_effort,
        "supports_max_output_tokens": cfg.supports_max_output_tokens,
        "pricing": {
            "input_cost_per_1k_tokens": cfg.input_cost_per_1k_tokens,
            "output_cost_per_1k_tokens": cfg.output_cost_per_1k_tokens,
        },
        "fallback_providers": [canonical_provider_config(item, require_selection=True) for item in cfg.fallback_providers],
    }


def provider_config_fingerprint(raw: Any, *, require_selection: bool = False) -> str:
    payload = canonical_provider_config(raw, require_selection=require_selection)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


class FakeLLMProvider:
    """Deterministic provider for tests and offline runs."""

    def __init__(self, *, responses_by_method: dict[str, str] | None = None, model: str = "fake-llm") -> None:
        self.responses_by_method = responses_by_method or {}
        self.model = model

    def generate(self, request: LLMRequest) -> LLMResponse:
        default_payload = {
            "negotiate_tick": (
                '{"private_messages":[],"global_messages":[],"relationship_updates":[],'
                '"withheld_information":[]}'
            ),
            "submit_orders": (
                '{"orders":[],"order_rationale":{"goal_supported":"noop",'
                '"why_this_advances_strategy":"noop"},"explanation":{"summary":"noop"}}'
            ),
            "reflect": (
                '{"schema_version":"1.0","objective_observations":[],"belief_updates":[],'
                '"strategic_intent":{"short_term":"","medium_term":"","risk_notes":[]},'
                '"memory_update":"","explanation":{"summary":"noop"}}'
            ),
            "game_end_cleanup": '{"persistent_memory_update": "", "public_profile_update": null, "explanation": {"summary": "noop"}}',
        }
        content = self.responses_by_method.get(request.method, default_payload.get(request.method, "{}"))
        return LLMResponse(
            schema_version=request.schema_version,
            content=content,
            model=self.model,
            raw={"provider": "fake", "method": request.method},
        )


class EnvironmentConfiguredProviderStub:
    """
    Environment-configured provider stub for future real API integration.
    No external network calls are made.
    """

    def __init__(
        self,
        *,
        response_env_var: str = "LLM_PROVIDER_STUB_RESPONSE",
        model_env_var: str = "LLM_PROVIDER_STUB_MODEL",
    ) -> None:
        self.response_env_var = response_env_var
        self.model_env_var = model_env_var

    def generate(self, request: LLMRequest) -> LLMResponse:
        content = os.environ.get(self.response_env_var, "").strip()
        if not content:
            content = (
                '{"private_messages":[],"global_messages":[],"relationship_updates":[],'
                '"withheld_information":[]}'
            )
        model = os.environ.get(self.model_env_var, "env-stub-llm")
        return LLMResponse(
            schema_version=request.schema_version,
            content=content,
            model=model,
            raw={"provider": "env_stub", "env_var": self.response_env_var},
        )


class OpenAICompatibleProvider:
    """OpenAI-compatible Chat Completions adapter using standard library HTTP."""

    def __init__(self, *, config: LLMProviderConfig) -> None:
        self.config = config
        self.base_url = (config.base_url or "https://api.openai.com/v1").rstrip("/")
        api_key = os.environ.get(config.api_key_env_var, "").strip()
        if not api_key:
            raise ValueError(
                f"Missing required API key env var for real provider: {config.api_key_env_var}"
            )
        self.api_key = api_key

    def generate(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": request.prompt},
            ],
        }
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.max_output_tokens is not None:
            body["max_tokens"] = self.config.max_output_tokens

        payload = json.dumps(body).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-Client-Request-Id": str(request.context.get("client_request_id", uuid.uuid4())),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                response_headers = getattr(resp, "headers", None)
                provider_request_id = response_headers.get("x-request-id") if response_headers else None
                raw_text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ProviderHTTPError(
                status_code=exc.code,
                error_body=error_body,
                metadata={**self.config.metadata(), "client_request_id": request.context.get("client_request_id"),
                          "provider_request_id": exc.headers.get("x-request-id") if exc.headers else None},
                retry_after_seconds=_retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None),
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderNetworkError("Provider network error") from exc

        response_payload = json.loads(raw_text)
        content = ""
        if isinstance(response_payload, dict):
            choices = response_payload.get("choices", [])
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message", {})
                    if isinstance(msg, dict):
                        content = str(msg.get("content", ""))
        return LLMResponse(
            schema_version=request.schema_version,
            content=content,
            model=self.config.model,
            raw={
                "provider": "openai_compatible",
                **self.config.metadata(resolved_model=str(response_payload.get("model", self.config.model))),
                "provider_request_id": provider_request_id,
                "response": response_payload,
                "usage": response_payload.get("usage") if isinstance(response_payload, dict) else None,
            },
        )


class ResponsesAPIProvider:
    """Responses API adapter shared by OpenAI and compatible provider profiles."""

    def __init__(self, *, config: LLMProviderConfig) -> None:
        self.config = config
        self.base_url = (config.base_url or "https://api.openai.com/v1").rstrip("/")
        api_key = os.environ.get(config.api_key_env_var, "").strip()
        if not api_key:
            raise ValueError(f"Missing required API key env var for real provider: {config.api_key_env_var}")
        self.api_key = api_key

    def generate(self, request: LLMRequest) -> LLMResponse:
        body = self._request_body(request)
        req = urllib.request.Request(
            url=f"{self.base_url}/responses",
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "X-Client-Request-Id": str(request.context.get("client_request_id", uuid.uuid4()))},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                response_headers = getattr(resp, "headers", None)
                provider_request_id = response_headers.get("x-request-id") if response_headers else None
                raw_text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ProviderHTTPError(
                status_code=exc.code,
                error_body=error_body,
                metadata={**self.config.metadata(), "client_request_id": request.context.get("client_request_id"),
                          "provider_request_id": exc.headers.get("x-request-id") if exc.headers else None},
                retry_after_seconds=_retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None),
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderNetworkError("Provider network error") from exc
        response_payload = json.loads(raw_text)
        if not isinstance(response_payload, dict):
            raise RuntimeError("Responses API returned a non-object payload.")
        resolved_model = str(response_payload.get("model", self.config.model))
        usage = self._normalize_usage(response_payload.get("usage"))
        return LLMResponse(
            schema_version=request.schema_version,
            content=self._extract_text(response_payload),
            model=resolved_model,
            raw={
                "provider": "responses",
                **self.config.metadata(resolved_model=resolved_model),
                "provider_request_id": provider_request_id,
                "text": self._extract_text(response_payload),
                "response": response_payload,
                "usage": usage,
            },
        )

    def _request_body(self, request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.config.model, "input": request.prompt}
        if self.config.service_tier is not None:
            body["service_tier"] = self.config.service_tier
        if self.config.prompt_cache_mode is not None:
            body["prompt_cache_options"] = {"mode": self.config.prompt_cache_mode}
        if self.config.max_output_tokens is not None and self.config.supports_max_output_tokens:
            body["max_output_tokens"] = self.config.max_output_tokens
        if self.config.temperature is not None and self.config.supports_temperature:
            body["temperature"] = self.config.temperature
        if self.config.reasoning_effort and self.config.supports_reasoning_effort:
            if self.config.reasoning_effort_param_style == "nested_reasoning":
                body["reasoning"] = {"effort": self.config.reasoning_effort}
            elif self.config.reasoning_effort_param_style == "top_level":
                body["reasoning_effort"] = self.config.reasoning_effort
        return body

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text
        parts: list[str] = []
        output = payload.get("output", [])
        if isinstance(output, list):
            for item in output:
                content = item.get("content", []) if isinstance(item, dict) else []
                if not isinstance(content, list):
                    continue
                for block in content:
                    text = block.get("text") if isinstance(block, dict) else None
                    if isinstance(text, str):
                        parts.append(text)
        return "".join(parts)

    @staticmethod
    def _normalize_usage(raw: Any) -> dict[str, Any]:
        usage = raw if isinstance(raw, dict) else {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        details = usage.get("output_tokens_details", {})
        reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
        return {
            "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
            "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
            "reasoning_tokens": reasoning_tokens if isinstance(reasoning_tokens, int) else None,
            "total_tokens": total_tokens if isinstance(total_tokens, int) else None,
            # Backward-compatible aliases used by the existing budget/runtime accounting.
            "prompt_tokens": input_tokens if isinstance(input_tokens, int) else None,
            "completion_tokens": output_tokens if isinstance(output_tokens, int) else None,
        }


def build_provider_from_config(config: LLMProviderConfig) -> LLMProvider:
    if config.kind == "fake":
        return FakeLLMProvider(model=config.model or "fake-llm")
    if config.kind == "env_stub":
        return EnvironmentConfiguredProviderStub(model_env_var="LLM_PROVIDER_STUB_MODEL")
    if config.kind in {"openai_compatible", "real_openai_compatible"}:
        return OpenAICompatibleProvider(config=config)
    if config.kind == "responses":
        return ResponsesAPIProvider(config=config)
    if config.kind == "codex_cli":
        from .native_codex_provider import NativeCodexCLIProvider
        return NativeCodexCLIProvider(config=config)
    raise ValueError(f"Unsupported llm provider kind: {config.kind}")


class ProviderRoute:
    """Explicit ordered alternatives; each call dispatches to exactly one configured route."""

    def __init__(self, providers: list[LLMProvider], metadata: list[dict[str, Any]]) -> None:
        self.providers = providers
        self.metadata = metadata

    def generate_route(self, request: LLMRequest, route_index: int) -> LLMResponse:
        response = self.providers[route_index].generate(request)
        raw = dict(response.raw) if isinstance(response.raw, dict) else {}
        raw["provider_route_index"] = route_index
        raw["provider_route_metadata"] = self.metadata[route_index]
        return LLMResponse(response.schema_version, response.content, response.model, raw)

    def route_count(self) -> int:
        return len(self.providers)


def build_provider_chain(config: LLMProviderConfig, builder=None) -> tuple[list[LLMProvider], list[dict[str, Any]]]:
    builder = builder or build_provider_from_config
    configs = (config, *config.fallback_providers)
    providers: list[LLMProvider] = []
    metadata = [item.metadata() | {"max_output_tokens": item.max_output_tokens,
                                  "output_ceiling_tokens": item.max_output_tokens if item.supports_max_output_tokens else None,
                                  "supports_max_output_tokens": item.supports_max_output_tokens,
                                  "timeout_seconds": item.timeout_seconds} for item in configs]
    available = 0
    for index, item in enumerate(configs):
        try:
            providers.append(builder(item))
            available += 1
        except Exception:
            if len(configs) == 1:
                raise
            providers.append(ProviderUnavailable())
            metadata[index]["route_available"] = False
    if available == 0:
        raise ValueError("No configured provider route is available.")
    return providers, metadata


class BudgetedLLMProvider:
    """Provider wrapper with pre-call budget checks and policy enforcement."""

    def __init__(
        self,
        *,
        inner: LLMProvider,
        budget: BudgetController | None,
        input_cost_per_1k_tokens: float | None = None,
        output_cost_per_1k_tokens: float | None = None,
        usage_tracker: ProviderUsageTracker | None = None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.inner = inner
        self.usage_tracker = usage_tracker
        self.provider_metadata = provider_metadata or {}
        self.budget = budget
        self.input_cost_per_1k_tokens = input_cost_per_1k_tokens
        self.output_cost_per_1k_tokens = output_cost_per_1k_tokens

    def generate(self, request: LLMRequest) -> LLMResponse:
        if isinstance(self.inner, ProviderUnavailable):
            raise ProviderUnavailable("Configured provider route is unavailable before dispatch.")
        game_id = str((request.context or {}).get("game_id", "unknown_game"))
        phase_id = str((request.context or {}).get("phase_id", "unknown_phase"))
        reservation_id = str(uuid.uuid4())
        input_bound = len(request.prompt.encode("utf-8")) + 256
        effective_output_ceiling = self.provider_metadata.get(
            "output_ceiling_tokens", self.provider_metadata.get("max_output_tokens")
        )
        output_bound = int(effective_output_ceiling or 0)
        if self.budget is not None and self.budget.config is not None and self.budget.config.max_output_tokens is not None and output_bound == 0:
            output_bound = self.budget.config.max_output_tokens + 1
        calculable_cost = 0.0
        if self.input_cost_per_1k_tokens is not None:
            calculable_cost += input_bound / 1000 * self.input_cost_per_1k_tokens
        if output_bound > 0 and self.output_cost_per_1k_tokens is not None:
            calculable_cost += output_bound / 1000 * self.output_cost_per_1k_tokens
        has_adapter_ceiling = bool(self.provider_metadata.get("supports_max_output_tokens", True)) and output_bound > 0
        complete_cost = has_adapter_ceiling and self.input_cost_per_1k_tokens is not None and self.output_cost_per_1k_tokens is not None
        reserve_cost = calculable_cost if complete_cost else None
        if not complete_cost and self.budget is not None and self.budget.config is not None:
            safe_reserve = self.budget.config.safe_reserve_usd_per_call
            reserve_cost = max(safe_reserve, calculable_cost) if safe_reserve is not None else None
        if self.budget is not None:
            decision = self.budget.reserve_before_call(
                reservation_id=reservation_id, game_id=game_id, phase_id=phase_id,
                agent_id=request.agent_id, method=request.method, input_tokens=input_bound,
                output_tokens=output_bound, cost_usd=reserve_cost,
            )
            if not decision.allow_provider_call:
                if self.usage_tracker is not None:
                    self.usage_tracker.record(request, blocked=True, provider=self.provider_metadata)
                return self._fallback_response(request, decision)
        try:
            response = self.inner.generate(request)
        except Exception as exc:
            if self.usage_tracker is not None:
                self.usage_tracker.record(request, error=exc, provider=self.provider_metadata)
            error_metadata = exc.artifact_data() if isinstance(exc, ProviderHTTPError) else {}
            if self.budget is not None:
                self.budget.settle_reservation(reservation_id=reservation_id, usage=None, uncertain=True)
            raise
        usage = {}
        malformed_usage = False
        if isinstance(response.raw, dict):
            usage = response.raw.get("usage", {})
            if isinstance(usage, dict):
                usage, malformed_usage = BudgetController._validated_usage(usage)
                response.raw["usage"] = usage
                self._inject_estimated_cost(usage)
        if self.budget is not None:
            self.budget.settle_reservation(reservation_id=reservation_id,
                usage=usage if isinstance(usage, dict) else {}, uncertain=malformed_usage)
        if self.usage_tracker is not None:
            self.usage_tracker.record(request, response=response, provider=self.provider_metadata)
        return response

    def _inject_estimated_cost(self, usage: dict[str, Any]) -> None:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if (not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool) or prompt_tokens < 0
                or not isinstance(completion_tokens, int) or isinstance(completion_tokens, bool) or completion_tokens < 0):
            return
        if self.input_cost_per_1k_tokens is None or self.output_cost_per_1k_tokens is None:
            return
        estimated_cost = (prompt_tokens / 1000.0) * self.input_cost_per_1k_tokens
        estimated_cost += (completion_tokens / 1000.0) * self.output_cost_per_1k_tokens
        usage["estimated_cost_usd"] = round(estimated_cost, 8)

    def _fallback_response(self, request: LLMRequest, decision: BudgetDecision) -> LLMResponse:
        payload_by_method = {
            "negotiate_tick": {
                "private_messages": [],
                "global_messages": [],
                "relationship_updates": [],
                "withheld_information": [],
            },
            "submit_orders": {
                "orders": [],
                "order_rationale": {
                    "goal_supported": "budget_fallback",
                    "why_this_advances_strategy": "provider call blocked by budget",
                },
                "explanation": {"summary": "budget_fallback"},
            },
            "reflect": {
                "schema_version": "1.0",
                "objective_observations": [],
                "belief_updates": [],
                "strategic_intent": {"short_term": "", "medium_term": "", "risk_notes": []},
                "memory_update": "",
                "explanation": {"summary": "budget_fallback"},
            },
            "game_end_cleanup": {
                "persistent_memory_update": "",
                "public_profile_update": None,
                "explanation": {"summary": "budget_fallback"},
            },
        }
        payload = payload_by_method.get(request.method, {})
        return LLMResponse(
            schema_version=request.schema_version,
            content=json.dumps(payload, sort_keys=True),
            model="budget_fallback",
            raw={
                "provider": "budget_fallback",
                "budget_blocked": True,
                "budget_reason": decision.reason,
                "budget_stop_policy": decision.stop_policy,
                "usage": {},
            },
        )
