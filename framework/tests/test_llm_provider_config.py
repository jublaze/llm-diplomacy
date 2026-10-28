from __future__ import annotations

import os

import pytest

from llm_diplomacy.agents.llm_provider import (
    LLMRequest,
    OpenAICompatibleProvider,
    build_provider_from_config,
    parse_llm_provider_config,
)
from llm_diplomacy.config.loader import load_experiment_config


def test_new_arm_standard_no_cache_controls_are_explicit_and_default_compatible():
    from llm_diplomacy.agents.llm_provider import ResponsesAPIProvider, canonical_provider_config
    base={'kind':'responses','model':'gpt-6.1-terra','reasoning_effort':'medium',
          'supports_reasoning_effort':True,'reasoning_effort_param_style':'nested_reasoning'}
    request=LLMRequest('1.0','a','negotiate_tick','unchanged exact player prompt',{})
    old=parse_llm_provider_config(base)
    pinned=parse_llm_provider_config({**base,'service_tier':'default','prompt_cache_mode':'explicit'})
    old_provider=object.__new__(ResponsesAPIProvider);old_provider.config=old
    provider=object.__new__(ResponsesAPIProvider);provider.config=pinned
    body=provider._request_body(request)
    assert body['service_tier']=='default'
    assert body['prompt_cache_options']=={'mode':'explicit'}
    assert body['input']==request.prompt
    assert {k:v for k,v in body.items() if k not in {'service_tier','prompt_cache_options'}}==old_provider._request_body(request)
    assert 'service_tier' not in canonical_provider_config(old)
    assert 'prompt_cache_mode' not in canonical_provider_config(old)
    assert canonical_provider_config(pinned)['prompt_cache_mode']=='explicit'
    for fields in ({'service_tier':'auto'},{'service_tier':'fast'},{'prompt_cache_mode':'implicit'},
                   {'service_tier':True},{'prompt_cache_mode':{}},{'kind':'openai_compatible','service_tier':'default'}):
        with pytest.raises(ValueError):parse_llm_provider_config({**base,**fields})


def test_config_loader_accepts_real_provider_shape(tmp_path) -> None:
    cfg = {
        "schema_version": "1.0",
        "experiment_id": "exp_real_cfg",
        "games": 1,
        "agents": [{"agent_id": "agent_llm", "runtime_kind": "llm"}],
        "rule_engine": {"name": "diplomacy"},
        "negotiation": {"max_ticks": 1},
        "seed": 1,
        "llm_provider": {
            "kind": "openai_compatible",
            "model": "gpt-4.1-mini",
            "temperature": 0.2,
            "max_output_tokens": 200,
            "timeout_seconds": 30,
        },
    }
    path = tmp_path / "cfg.json"
    path.write_text(__import__("json").dumps(cfg), encoding="utf-8")
    loaded = load_experiment_config(path)
    parsed = parse_llm_provider_config(loaded.raw.get("llm_provider"))
    assert parsed.kind == "openai_compatible"
    assert parsed.model == "gpt-4.1-mini"


def test_real_provider_refuses_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = parse_llm_provider_config(
        {
            "kind": "openai_compatible",
            "model": "gpt-4.1-mini",
            "timeout_seconds": 10,
        }
    )
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(config=cfg)


def test_fake_provider_remains_default() -> None:
    parsed = parse_llm_provider_config(None)
    provider = build_provider_from_config(parsed)
    assert parsed.kind == "fake"
    assert provider.__class__.__name__ == "FakeLLMProvider"


def test_explicit_ordered_provider_alternatives_are_canonical_and_bounded():
    cfg = parse_llm_provider_config({
        "kind": "openai_compatible", "model": "primary", "max_output_tokens": 100,
        "fallback_providers": [{"kind": "responses", "model": "alternate", "max_output_tokens": 80}],
    })
    assert [item.model for item in cfg.fallback_providers] == ["alternate"]
    assert parse_llm_provider_config({"kind": "openai_compatible", "model": "primary",
        "fallback_providers": [{"kind": "responses", "model": "alternate"}]}).fallback_providers[0].max_output_tokens is None
    with pytest.raises(ValueError, match="positive integer"):
        parse_llm_provider_config({"kind": "responses", "model": "bad", "max_output_tokens": 0})


def test_http_timeout_defaults_and_explicit_bounds_are_preserved():
    for kind in ("responses", "openai_compatible", "real_openai_compatible"):
        assert parse_llm_provider_config({"kind": kind, "model": "m"}).timeout_seconds == 600
    assert parse_llm_provider_config({"kind": "responses", "model": "m", "timeout_seconds": 300}).timeout_seconds == 300
    assert parse_llm_provider_config({"kind": "codex_cli", "model": "m", "reasoning_effort": "low"}).timeout_seconds == 30
    for value in (0, 601, True, 3.5):
        with pytest.raises(ValueError, match="timeout_seconds"):
            parse_llm_provider_config({"kind": "responses", "model": "m", "timeout_seconds": value})


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_LLM_PROVIDER_TEST", "").strip() != "1",
    reason="Set RUN_REAL_LLM_PROVIDER_TEST=1 to enable optional real-provider smoke test.",
)
def test_real_provider_optional_smoke() -> None:
    cfg = parse_llm_provider_config(
        {
            "kind": "openai_compatible",
            "model": os.environ.get("REAL_LLM_MODEL", "gpt-4.1-mini"),
            "timeout_seconds": int(os.environ.get("REAL_LLM_TIMEOUT", "60")),
            "base_url": os.environ.get("REAL_LLM_BASE_URL", None),
        }
    )
    provider = OpenAICompatibleProvider(config=cfg)
    response = provider.generate(
        LLMRequest(
            schema_version="1.0",
            agent_id="agent_llm",
            method="negotiate_tick",
            prompt='Return JSON: {"done": true, "messages": [], "explanation": {"summary": "smoke"}}',
            context={},
        )
    )
    assert isinstance(response.content, str)
