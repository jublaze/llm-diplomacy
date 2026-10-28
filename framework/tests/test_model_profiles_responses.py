from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from llm_diplomacy.agents.llm_provider import (
    FakeLLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderHTTPError,
    ResponsesAPIProvider,
    build_provider_from_config,
    parse_llm_provider_config,
)
from llm_diplomacy.agents.model_profiles import load_model_profile
from llm_diplomacy.config.models import ExperimentConfig
from llm_diplomacy.population import PopulationExperimentRunner, load_population_experiment_config
from llm_diplomacy.runner import scripted_experiment_runner as runner_module
from llm_diplomacy.runner.scripted_experiment_runner import ScriptedExperimentRunner


class _HTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _HTTPResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _request() -> LLMRequest:
    return LLMRequest(
        schema_version="1.0",
        agent_id="cassandra",
        method="negotiate_tick",
        prompt="Return JSON only.",
        context={"game_id": "game_001", "phase_id": "1901_spring_negotiation"},
    )


def test_model_profile_loads_openai_gpt_5_5() -> None:
    profile = load_model_profile("openai_gpt_5_5")
    assert profile.provider_name == "openai"
    assert profile.api_style == "responses"
    assert profile.reasoning_effort_param_style == "nested_reasoning"


def test_advanced_solo_proposal_resolves_offline_without_temperature() -> None:
    profile = load_model_profile("openai_gpt_6_1_sol")
    assert profile.model == "gpt-6.1-sol"
    assert profile.api_style == "responses"
    assert profile.default_reasoning_effort == "low"
    assert profile.reasoning_effort_values == ("low", "medium", "high", "xhigh", "max")
    assert profile.supports_temperature is False
    provider_config = parse_llm_provider_config({"model_profile": profile.id, "reasoning_effort": "low", "max_output_tokens": 1600})
    assert provider_config.api_style == "responses"
    assert provider_config.temperature is None
    assert provider_config.reasoning_effort == "low"
    config = load_population_experiment_config("config/examples/advanced_solo_1912_unlaunched.yaml")
    assert config.experiment_id == "advanced_solo_1912_unlaunched"
    assert config.dry_run is True
    assert config.game_count == 1
    assert config.max_year == 1912
    assert config.raw["runtime"]["max_ticks"] == 3
    assert len(config.players) == 7
    assert all(player.provider_config["max_output_tokens"] == 2000 for player in config.players)
    assert config.raw["budget"] == {
        "max_provider_calls": 3500,
        "max_input_tokens": 20000000,
        "max_output_tokens": 5000000,
        "max_estimated_cost_usd": 75.0,
        "stop_policy": "stop_experiment",
    }


def test_model_profile_loads_xai_grok_4_5() -> None:
    profile = load_model_profile("xai_grok_4_5")
    assert profile.provider_name == "xai"
    assert profile.model == "grok-4.5"
    assert profile.reasoning_effort_param_style == "top_level"


def test_model_profile_overrides_reasoning_effort() -> None:
    cfg = parse_llm_provider_config({"model_profile": "openai_gpt_5_5", "reasoning_effort": "low"})
    assert cfg.reasoning_effort == "low"
    assert cfg.model_profile == "openai_gpt_5_5"


def test_model_profile_preserves_chat_completions_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_DIPLOMACY_OPENAI_API_KEY", "test-key")
    cfg = parse_llm_provider_config({"model_profile": "openai_gpt_4_1_mini", "max_output_tokens": 200})
    provider = build_provider_from_config(cfg)
    assert cfg.api_style == "chat_completions"
    assert provider.__class__.__name__ == "OpenAICompatibleProvider"
    assert cfg.temperature == 0.2


@pytest.mark.parametrize(
    ("profile_id", "expected_reasoning_key"),
    [("openai_gpt_5_5", "reasoning"), ("openai_gpt_6_luna", "reasoning"), ("xai_grok_4_5", "reasoning_effort")],
)
def test_responses_provider_builds_profile_reasoning_payload(
    monkeypatch: pytest.MonkeyPatch, profile_id: str, expected_reasoning_key: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "other-project-key")
    monkeypatch.setenv("LLM_DIPLOMACY_OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("XAI_API_KEY", "test-xai")
    captured: dict[str, object] = {}

    def fake_urlopen(req: object, timeout: int) -> _HTTPResponse:
        captured["request"] = req
        captured["timeout"] = timeout
        return _HTTPResponse({"model": "resolved", "output_text": "{}", "usage": {}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    effort = "high" if profile_id == "openai_gpt_6_luna" else "low"
    cfg = parse_llm_provider_config(
        {"model_profile": profile_id, "reasoning_effort": effort, "max_output_tokens": 700}
    )
    ResponsesAPIProvider(config=cfg).generate(_request())
    request = captured["request"]
    expected_key = "test-openai" if profile_id.startswith("openai_") else "test-xai"
    assert request.get_header("Authorization") == "Bearer " + expected_key
    payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
    assert expected_reasoning_key in payload
    if profile_id == "openai_gpt_6_luna":
        assert payload["model"] == "gpt-6-luna"
        assert payload["reasoning"] == {"effort": "high"}
    assert payload["max_output_tokens"] == 700
    assert "max_tokens" not in payload
    assert "messages" not in payload
    assert "temperature" not in payload


def test_responses_provider_extracts_output_text_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_DIPLOMACY_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _HTTPResponse(
            {
                "model": "gpt-5.5-2026-04-23",
                "output": [{"content": [{"type": "output_text", "text": '{"done":true}'}]}],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            }
        ),
    )
    cfg = parse_llm_provider_config({"model_profile": "openai_gpt_5_5", "reasoning_effort": "low"})
    response = ResponsesAPIProvider(config=cfg).generate(_request())
    assert response.content == '{"done":true}'
    assert response.model == "gpt-5.5-2026-04-23"
    assert response.raw["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "reasoning_tokens": 5,
        "total_tokens": 18,
        "prompt_tokens": 11,
        "completion_tokens": 7,
    }


def test_responses_provider_records_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    error_body = '{"error":{"message":"bad model"}}'

    def fail(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError(
            url="https://api.x.ai/v1/responses",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(error_body.encode("utf-8")),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    cfg = parse_llm_provider_config({"model_profile": "xai_grok_4_5", "reasoning_effort": "low"})
    with pytest.raises(ProviderHTTPError) as caught:
        ResponsesAPIProvider(config=cfg).generate(_request())
    assert caught.value.status_code == 400
    assert caught.value.error_body == error_body
    assert caught.value.artifact_data()["provider_name"] == "xai"


@pytest.mark.parametrize("kind", ["responses", "openai_compatible"])
def test_http_errors_preserve_vendor_request_id(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    def fail(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError("https://api.invalid/v1", 429, "rate limited",
            {"x-request-id": "server-review-id"}, io.BytesIO(b'{"error":{"type":"rate_limit"}}'))
    monkeypatch.setattr("urllib.request.urlopen", fail)
    cfg = parse_llm_provider_config({"kind": kind, "model": "m", "max_output_tokens": 10})
    provider = build_provider_from_config(cfg)
    with pytest.raises(ProviderHTTPError) as caught:
        provider.generate(_request())
    assert caught.value.artifact_data()["provider_request_id"] == "server-review-id"


def test_runtime_accepts_model_profile_and_manifest_records_metadata(tmp_path: Path) -> None:
    cfg = load_population_experiment_config("experiments/configs/openai_gpt_5_5_smoke_1x_1901.yaml")
    result = PopulationExperimentRunner(
        config=cfg,
        base_dir=tmp_path,
        agents_root=tmp_path / "agents",
    ).run(dry_run_override=True)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    provider = manifest["model_provider"]
    assert provider["provider_name"] == "openai"
    assert provider["api_style"] == "responses"
    assert provider["requested_model_profile"] == "openai_gpt_5_5"
    assert provider["reasoning_effort"] == "low"


class _SuccessfulProfileProvider(FakeLLMProvider):
    def generate(self, request: LLMRequest) -> LLMResponse:
        response = super().generate(request)
        return LLMResponse(
            schema_version=response.schema_version,
            content=response.content,
            model="gpt-5.5-2026-04-23",
            raw={
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                "provider_name": "openai",
                "api_style": "responses",
                "requested_model_profile": "openai_gpt_5_5",
                "requested_model": "gpt-5.5",
                "resolved_model": "gpt-5.5-2026-04-23",
                "reasoning_effort": "low",
                "base_url": "https://api.openai.com/v1",
            },
        )


def test_reports_record_resolved_profile_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "build_provider_from_config", lambda _cfg: _SuccessfulProfileProvider())
    cfg = load_population_experiment_config("experiments/configs/openai_gpt_5_5_smoke_1x_1901.yaml")
    # This profile fixture has no configured prices. A bounded explicit reserve
    # keeps the metadata test exercising a real mocked request under admission.
    assert cfg.budget is not None
    cfg.budget["safe_reserve_usd_per_call"] = 0.01
    result = PopulationExperimentRunner(
        config=cfg,
        base_dir=tmp_path,
        agents_root=tmp_path / "agents",
    ).run()
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    pilot = json.loads(result.pilot_report_json_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    for artifact in (summary, pilot):
        assert artifact["provider_name"] == "openai"
        assert artifact["api_style"] == "responses"
        assert artifact["requested_model_profile"] == "openai_gpt_5_5"
        assert artifact["resolved_model"] == "gpt-5.5-2026-04-23"
        assert artifact["reasoning_effort"] == "low"


class _FailingProvider:
    def generate(self, _request: LLMRequest) -> object:
        raise RuntimeError("provider unavailable")


def test_systemic_provider_failure_marks_experiment_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "build_provider_from_config", lambda _cfg: _FailingProvider())
    cfg = ExperimentConfig(
        schema_version="1.0",
        experiment_id="systemic_provider_failure",
        games=1,
        agents=[{"agent_id": "agent_llm", "runtime_kind": "llm"}],
        rule_engine={"name": "diplomacy"},
        negotiation={"max_ticks": 1},
        seed=1,
        raw={"llm_provider": {"kind": "fake"}},
    )
    with pytest.raises(RuntimeError, match="Systemic provider failure"):
        ScriptedExperimentRunner(config=cfg, base_dir=tmp_path, max_years=1, negotiation_ticks=1).run()
    events = (
        tmp_path / "experiments" / "systemic_provider_failure" / "logs" / "experiment_events.jsonl"
    ).read_text(encoding="utf-8")
    assert "systemic_provider_failure" in events
