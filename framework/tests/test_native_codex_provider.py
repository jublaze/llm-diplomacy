from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest

from llm_diplomacy.agents.baseline_llm_runtime import BaselineLLMAgentRuntime
from llm_diplomacy.agents.contracts import AgentContext
from llm_diplomacy.agents.llm_provider import (
    BudgetedLLMProvider,
    LLMRequest,
    build_provider_from_config,
    canonical_provider_config,
    parse_llm_provider_config,
    provider_config_fingerprint,
)
from llm_diplomacy.agents.native_codex_provider import (
    NativeCodexError, _attach_windows_job, _process_creation_options, _read_cli_version,
    resolve_codex_executable,
)
from llm_diplomacy.guardrails.budget import BudgetConfig, BudgetController
from llm_diplomacy.metrics.provider_usage import ProviderUsageTracker
from llm_diplomacy.runner import ScriptedExperimentRunner


CONFIG = {"kind": "codex_cli", "model": "gpt-6.1-sol", "reasoning_effort": "low", "timeout_seconds": 1}


class _FakeProcess:
    next_stdout = ""
    next_returncode = 0
    next_poll = 0
    command = None
    env = None
    cwd = None
    options = None

    class _Stdin:
        def write(self, value):
            _FakeProcess.stdin_value = value
        def close(self):
            pass

    def __init__(self, command, **kwargs):
        type(self).command = command
        type(self).env = kwargs["env"]
        type(self).cwd = kwargs["cwd"]
        type(self).options = kwargs
        type(self).stdin_value = b""
        self.stdin = type(self)._Stdin()
        self.stdout = io.BytesIO(type(self).next_stdout.encode())
        self.stderr = io.BytesIO(type(self).next_stderr.encode())
        self.returncode = type(self).next_returncode
        self.pid = 765432

    def poll(self):
        return type(self).next_poll if type(self).next_poll is not None else None

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class _FakeTreeOwner:
    def __init__(self, process):
        self.process = process
        self.terminate_calls = 0
        self.close_calls = 0
        self.terminate_ok = True
        self.close_ok = True

    def terminate(self):
        self.terminate_calls += 1
        return self.terminate_ok

    def close(self):
        self.close_calls += 1
        return self.close_ok


def _set_process(monkeypatch, *, events="", output='{"orders": []}', returncode=0, poll=0, stderr=""):
    lines = events.splitlines()
    if output:
        message = json.dumps({"type": "item.completed", "item": {
            "id": "item-final", "type": "agent_message", "phase": "final_answer", "text": output,
        }})
        completion = next((index for index, line in enumerate(lines) if '"type":"turn.completed"' in line or '"type": "turn.completed"' in line), len(lines))
        lines.insert(completion, message)
    events = "\n".join(lines)
    event_types = []
    for line in lines:
        try:
            event_types.append(json.loads(line).get("type"))
        except (json.JSONDecodeError, AttributeError):
            pass
    prefix = []
    if "thread.started" not in event_types:
        prefix.append('{"type":"thread.started","thread_id":"thread-1"}')
    if "turn.started" not in event_types:
        prefix.append('{"type":"turn.started"}')
    if "turn.completed" not in event_types:
        lines.append('{"type":"turn.completed","usage":{}}')
    events = "\n".join(prefix + lines)
    _FakeProcess.next_stdout = events
    _FakeProcess.next_stderr = stderr
    _FakeProcess.command = None
    _FakeProcess.next_returncode = returncode
    _FakeProcess.next_poll = poll
    monkeypatch.setenv("PATH", "C:/safe/bin")
    monkeypatch.setenv("CODEX_HOME", "C:/Users/test/.codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross")
    monkeypatch.setenv("CODEX_THREAD_ID", "must-not-cross")


def _provider(monkeypatch):
    cfg = parse_llm_provider_config(CONFIG, require_selection=True)
    from llm_diplomacy.agents.native_codex_provider import NativeCodexCLIProvider
    owners = []
    def own(process):
        owner = _FakeTreeOwner(process)
        owners.append(owner)
        return owner
    provider = NativeCodexCLIProvider(config=cfg, executable="C:/codex.exe", popen=_FakeProcess,
                                      version_reader=lambda _path: "0.162.1", tree_owner_factory=own)
    provider._test_tree_owners = owners
    return cfg, provider


def test_native_config_is_narrow_canonical_and_fingerprinted():
    cfg = parse_llm_provider_config(CONFIG, require_selection=True)
    canonical = canonical_provider_config(CONFIG, require_selection=True)
    assert cfg.api_style == "codex_cli"
    assert canonical["kind"] == "codex_cli"
    assert canonical["model"] == "gpt-6.1-sol"
    assert canonical["reasoning_effort"] == "low"
    assert provider_config_fingerprint(CONFIG) == provider_config_fingerprint(dict(CONFIG))
    assert provider_config_fingerprint(CONFIG) != provider_config_fingerprint({**CONFIG, "reasoning_effort": "high"})
    for field in ({"temperature": 0}, {"max_output_tokens": 20}, {"base_url": "https://invalid"}, {"pricing": {}}):
        with pytest.raises(ValueError, match="does not support"):
            parse_llm_provider_config({**CONFIG, **field})
    with pytest.raises(ValueError, match="reasoning_effort"):
        parse_llm_provider_config({"kind": "codex_cli", "model": "x", "reasoning_effort": "none"})
    with pytest.raises(ValueError, match="timeout_seconds"):
        parse_llm_provider_config({**CONFIG, "timeout_seconds": 1.5})


def test_bounded_example_selects_native_provider_but_is_not_marked_dry_run():
    from llm_diplomacy.config.loader import load_experiment_config

    cfg = load_experiment_config(Path(__file__).parents[1] / "config/examples/native_codex_cli_bounded_example.yaml")
    assert cfg.raw["llm_provider"]["kind"] == "codex_cli"
    assert cfg.raw["budget"]["max_provider_calls"] == 1
    assert "dry_run" not in cfg.raw


def test_windows_npm_layout_resolves_nested_vendor_bin_and_outer_version(tmp_path, monkeypatch):
    import llm_diplomacy.agents.native_codex_provider as native

    shim = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    shim.parent.mkdir(parents=True)
    shim.write_text("// wrapper", encoding="utf-8")
    root = shim.parents[1]
    (root / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": "0.162.1"}), encoding="utf-8")
    vendor = root / "node_modules" / "@openai" / "codex-win32-x64"
    binary = vendor / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"offline fixture")
    (vendor / "package.json").write_text(json.dumps({"name": "@openai/codex-win32-x64", "version": "0.162.1-win32-x64"}), encoding="utf-8")
    monkeypatch.setattr(native.shutil, "which", lambda name: str(shim) if name == "codex" else None)
    assert Path(resolve_codex_executable(platform_name="win32", machine_name="AMD64")) == binary
    assert _read_cli_version(str(binary)) == "0.162.1"
    command_shim = tmp_path / "npm" / "codex.cmd"
    command_shim.parent.mkdir()
    command_shim.write_text("@echo off", encoding="utf-8")
    npm_root = command_shim.parent
    command_package = npm_root / "node_modules" / "@openai" / "codex"
    command_binary = (command_package / "node_modules" / "@openai" / "codex-win32-x64"
                      / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe")
    command_binary.parent.mkdir(parents=True)
    command_binary.write_bytes(b"offline fixture")
    monkeypatch.setattr(native.shutil, "which", lambda name: str(command_shim) if name == "codex" else None)
    assert Path(resolve_codex_executable(platform_name="win32", machine_name="AMD64")) == command_binary


def test_javascript_launcher_keeps_only_resolved_node_directory(monkeypatch):
    import llm_diplomacy.agents.native_codex_provider as native

    monkeypatch.delenv("SYSTEMROOT", raising=False)
    monkeypatch.setenv("PATH", "C:/host/path")
    monkeypatch.setenv("SYSTEMROOT", "C:/windows-fixture")
    monkeypatch.setattr(native.shutil, "which", lambda name: "C:/trusted/node/node.exe" if name == "node" else None)
    windows = native._child_environment(node_required=True, platform_name="nt")
    with monkeypatch.context() as posix_fixture:
        posix_fixture.delenv("SYSTEMROOT", raising=False)
        posix = native._child_environment(node_required=True, platform_name="posix")
    node_dir = str(Path("C:/trusted/node").resolve())
    assert windows["PATH"].startswith(node_dir + ";")
    assert windows["PATH"].endswith("System32")
    assert posix["PATH"] == node_dir + ":/usr/bin:/bin"
    assert _process_creation_options(platform_name="nt")["creationflags"] == 0x204
    assert _process_creation_options(platform_name="nt")["start_new_session"] is False
    assert _process_creation_options(platform_name="posix")["start_new_session"] is True


def _events_for_cli_content(content, attempt):
    usage = {"input_tokens": 8, "output_tokens": 3}
    rows = [
        {"type": "thread.started", "thread_id": f"thread-{attempt}"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {
            "id": f"item-{attempt}", "type": "agent_message", "phase": "final_answer",
            "text": json.dumps({"content": content}),
        }},
        {"type": "turn.completed", "usage": usage},
    ]
    return "\n".join(json.dumps(row) for row in rows)


@pytest.mark.parametrize(
    ("first_content", "first_category"),
    [("malformed JSON", "json_decode_failed"), ("[]", "top_level_not_object")],
)
def test_native_invalid_model_output_uses_runtime_repair_and_keeps_usage(
    monkeypatch, tmp_path, first_content, first_category,
):
    _set_process(monkeypatch)
    cfg, inner = _provider(monkeypatch)
    good_content = json.dumps({
        "orders": [],
        "order_rationale": {"goal_supported": "hold", "why_this_advances_strategy": "safe"},
        "explanation": {"summary": "recovered"},
    })

    class SequencedProcess(_FakeProcess):
        outputs = [_events_for_cli_content(first_content, 1), _events_for_cli_content(good_content, 2)]
        invocations = 0
        def __init__(self, command, **kwargs):
            type(self).next_stdout = type(self).outputs.pop(0)
            type(self).next_stderr = ""
            type(self).next_returncode = 0
            type(self).next_poll = 0
            type(self).invocations += 1
            super().__init__(command, **kwargs)

    inner._popen = SequencedProcess
    tracker = ProviderUsageTracker(tmp_path / "usage")
    budget = BudgetController(
        schema_version="1.0", experiment_id="native_repair", artifact_root=tmp_path / "budget",
        config=BudgetConfig(max_provider_calls=2),
    )
    wrapped = BudgetedLLMProvider(
        inner=inner, budget=budget, usage_tracker=tracker, provider_metadata=cfg.metadata(),
    )
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0", runtime_root=tmp_path / "agents", provider=wrapped,
        provider_metadata=cfg.metadata(),
    )
    runtime.register_agent(agent_id="alice")
    result = runtime.submit_orders(AgentContext(
        schema_version="1.0", experiment_id="native_repair", game_id="g",
        phase_id="1901_spring_movement", agent_id="alice", country_assignment="france",
        visible_state={"schema_version": "1.0", "game_id": "g", "phase": "movement", "year": 1901},
    ))

    assert result.ok
    assert SequencedProcess.invocations == 2
    assert budget.total_provider_calls == 2
    assert len(tracker.rows) == 2
    assert [row["attempt_index"] for row in tracker.rows] == [0, 1]
    assert all(row["error_type"] is None for row in tracker.rows)
    assert all(row["input_tokens"] == 8 and row["output_tokens"] == 3 for row in tracker.rows)
    assert all(row["resolved_model"] is None and row["estimated_cost_usd"] is None for row in tracker.rows)
    totals = tracker.summary()["totals"]
    assert totals["provider_calls"] == 2 and totals["repair_calls"] == 1 and totals["provider_errors"] == 0
    assert totals["input_tokens"] == 16 and totals["output_tokens"] == 6
    assert totals["estimated_cost_usd"] is None and totals["observed_resolved_models"] == []
    output = next((tmp_path / "agents" / "alice" / "runtime" / "phase_outputs").glob("*.json"))
    artifact = json.loads(output.read_text(encoding="utf-8"))
    meta = artifact["llm_meta"]
    assert meta["retry_count"] == 1 and meta["retry_success"] is True
    assert meta["parse_error"] is False and meta["provider_error"] is None
    assert meta["retry_attempts"][0]["failure_category"] == "invalid_output"
    assert meta["retry_attempts"][0]["parse_error_category"] == first_category
    assert meta["retry_attempts"][1]["failure_category"] is None
    assert meta["provider_model"] is None


def test_success_uses_fresh_stdin_only_invocation_and_unknown_resolution(monkeypatch, tmp_path):
    _set_process(monkeypatch, events='{"type":"turn.completed","usage":{"input_tokens":8,"cached_input_tokens":2,"output_tokens":3,"reasoning_output_tokens":1}}\n',
                 output='{"content":"{\\"orders\\": []}"}')
    cfg, provider = _provider(monkeypatch)
    prompt_at_ownership = []
    owner_factory = provider._tree_owner_factory
    def observe_ownership(process):
        prompt_at_ownership.append(_FakeProcess.stdin_value)
        return owner_factory(process)
    provider._tree_owner_factory = observe_ownership
    request = LLMRequest("1.0", "alice", "submit_orders", "secret player context", {"game_id": "g"})
    response = provider.generate(request)
    command = _FakeProcess.command
    assert command[-1] == "-"
    assert "secret player context" not in command
    assert "--ignore-user-config" in command and "--ignore-rules" in command and "--ephemeral" in command
    assert "--sandbox" not in command and "dangerously-bypass" not in " ".join(command)
    assert "--disable" in command and "shell_tool" in command
    assert "--disable" in command and "apps" in command
    assert "--disable" in command and "browser_use" in command
    assert "--disable" in command and "memories" in command
    assert "--disable" in command and "multi_agent" in command
    assert "--disable" in command and "plugins" in command
    assert "--disable" in command and "in_app_local_automation" in command
    assert "-c" in command and "mcp_servers={}" in command
    assert command.count('web_search="disabled"') == 1
    assert "model_provider=\"openai\"" in command
    assert "forced_login_method=\"chatgpt\"" in command
    assert _FakeProcess.stdin_value == b"secret player context"
    assert prompt_at_ownership == [b""]
    assert "OPENAI_API_KEY" not in _FakeProcess.env and "CODEX_THREAD_ID" not in _FakeProcess.env
    assert _FakeProcess.env["CODEX_HOME"] == "C:/Users/test/.codex"
    assert response.model is None
    assert response.raw["requested_model"] == "gpt-6.1-sol"
    assert response.raw["resolved_model"] is None
    assert response.raw["usage"]["input_tokens"] == 8
    assert response.raw["usage"]["cached_input_tokens"] == 2
    assert response.raw["usage"]["reasoning_tokens"] == 1
    assert response.raw["usage"]["total_tokens"] is None
    assert not Path(_FakeProcess.cwd).exists()
    first_cwd = _FakeProcess.cwd
    provider.generate(request)
    assert prompt_at_ownership == [b"", b""]
    assert _FakeProcess.cwd != first_cwd
    assert not Path(_FakeProcess.cwd).exists()


@pytest.mark.parametrize("output,events,error", [
    ("", "", "without one final agent message"),
    ("not json", "", "envelope"),
    ("[]", "", "envelope"),
    ('{"content":"[]"}', '{"type":"item.started","item":{"type":"shell_command"}}\n', "unsupported item"),
    ('{"content":"[]"}', "{broken}\n", "malformed event"),
])
def test_rejects_empty_malformed_and_tool_events(monkeypatch, output, events, error):
    _set_process(monkeypatch, events=events, output=output)
    _, provider = _provider(monkeypatch)
    with pytest.raises(NativeCodexError, match=error):
        provider.generate(LLMRequest("1.0", "a", "submit_orders", "private", {}))


@pytest.mark.parametrize("events", [
    '{"type":"item.completed","item":{"id":"i","type":"agent_message","phase":"final_answer","text":"{}"}}\n'
    '{"type":"turn.completed","usage":{}}\n{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}',
    '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"i","type":"agent_message","phase":"final_answer","text":"{}"}}\n'
    '{"type":"turn.completed","usage":{}}\n'
    '{"type":"item.completed","item":{"id":"later","type":"agent_message","phase":"final_answer","text":"{}"}}',
    '{"type":"thread.started"}\n{"type":"turn.started"}\n{"type":"turn.completed"}',
    '{"type":"thread.started","thread_id":"t"}\n{"type":"thread.started","thread_id":"t2"}',
    '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"a","type":"agent_message","phase":"final_answer","text":"{}"}}\n'
    '{"type":"item.completed","item":{"id":"comment","type":"agent_message","phase":"commentary","text":"late"}}\n'
    '{"type":"turn.completed","usage":{}}',
    '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"a","type":"agent_message","phase":"final_answer","text":"{}"}}\n'
    '{"type":"item.completed","item":{"id":"b","type":"agent_message","phase":"final_answer","text":"{}"}}\n'
    '{"type":"turn.completed","usage":{}}',
    '{"type":"rate_limits.updated"}\n{"type":"thread.started","thread_id":"t"}\n'
    '{"type":"turn.started"}\n{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"{}"}}\n'
    '{"type":"turn.completed","usage":{}}',
])
def test_rejects_invalid_event_chronology_and_payloads(monkeypatch, events):
    _set_process(monkeypatch, events=events, output="")
    _, provider = _provider(monkeypatch)
    with pytest.raises(NativeCodexError):
        provider.generate(LLMRequest("1.0", "a", "submit_orders", "private", {}))


def test_accepts_commentary_reasoning_and_out_of_band_rate_limits(monkeypatch):
    events = "\n".join([
        '{"type":"rate_limits.updated","rate_limits":{}}',
        '{"type":"thread.started","thread_id":"t"}',
        '{"type":"turn.started"}',
        '{"type":"item.started","item":{"id":"r","type":"reasoning"}}',
        '{"type":"item.completed","item":{"id":"r","type":"reasoning","text":"thinking"}}',
        '{"type":"item.completed","item":{"id":"c","type":"agent_message","phase":"commentary","text":"working"}}',
        '{"type":"turn.completed","usage":{}}',
        '{"type":"rate_limits.updated","rate_limits":{}}',
    ])
    final = json.dumps({"type": "item.completed", "item": {
        "id": "f", "type": "agent_message", "phase": "final_answer",
        "text": json.dumps({"content": json.dumps({"orders": []})}),
    }})
    events = events.replace('{"type":"turn.completed","usage":{}}', final + '\n{"type":"turn.completed","usage":{}}')
    _set_process(monkeypatch, events=events, output="")
    _, provider = _provider(monkeypatch)
    response = provider.generate(LLMRequest("1.0", "a", "submit_orders", "private", {}))
    assert json.loads(response.content) == {"orders": []}


def test_nonzero_and_output_overflow_fail_safely_and_cleanup(monkeypatch):
    _set_process(monkeypatch, returncode=9, output='{"content":"{}"}')
    _, provider = _provider(monkeypatch)
    with pytest.raises(NativeCodexError, match="status 9"):
        provider.generate(LLMRequest("1.0", "a", "reflect", "private", {}))
    assert not Path(_FakeProcess.cwd).exists()

    _set_process(monkeypatch, output='{"content":"{}"}', poll=None)
    with pytest.raises(TimeoutError, match="timed out"):
        provider.generate(LLMRequest("1.0", "a", "reflect", "private", {}))
    assert provider._test_tree_owners[-1].terminate_calls == 1
    assert not Path(_FakeProcess.cwd).exists()

    _set_process(monkeypatch, output="x" * 2_000_001, poll=None)
    with pytest.raises(NativeCodexError, match="output exceeded"):
        provider.generate(LLMRequest("1.0", "a", "reflect", "private", {}))
    assert provider._test_tree_owners[-1].terminate_calls == 1
    assert not Path(_FakeProcess.cwd).exists()

    _set_process(monkeypatch, output='{"content":"{}"}', poll=None, stderr="x" * 32_001)
    with pytest.raises(NativeCodexError, match="output exceeded"):
        provider.generate(LLMRequest("1.0", "a", "reflect", "private", {}))
    assert provider._test_tree_owners[-1].terminate_calls == 1
    assert not Path(_FakeProcess.cwd).exists()


def test_process_tree_is_terminated_after_leader_exit_when_capture_stalls(monkeypatch):
    _set_process(monkeypatch, output='{"content":"{}"}', poll=0)
    _, provider = _provider(monkeypatch)
    import llm_diplomacy.agents.native_codex_provider as native
    released = threading.Event()
    owner = provider._test_tree_owners
    original = native._drain_bounded
    def stalled(stream, destination, limit, overflow):
        if stream is not None and destination.name == "stdout.jsonl":
            destination.touch()
            released.wait(1)
        else:
            original(stream, destination, limit, overflow)
    owner_factory = provider._tree_owner_factory
    def own(process):
        tree_owner = owner_factory(process)
        def terminate():
            tree_owner.terminate_calls += 1
            released.set()
            return True
        tree_owner.terminate = terminate
        return tree_owner
    provider._tree_owner_factory = own
    monkeypatch.setattr(native, "_drain_bounded", stalled)
    monkeypatch.setattr(native, "_CAPTURE_JOIN_TIMEOUT", 0.01)
    with pytest.raises(NativeCodexError, match="capture stalled"):
        provider.generate(LLMRequest("1.0", "a", "reflect", "private", {}))
    tree_owner = provider._test_tree_owners[-1]
    assert tree_owner.terminate_calls >= 1
    assert tree_owner.close_calls == 1


def test_process_tree_cleanup_failure_is_reported(monkeypatch):
    _set_process(monkeypatch)
    _, provider = _provider(monkeypatch)
    owners = provider._test_tree_owners
    def fail_cleanup(process):
        owner = _FakeTreeOwner(process)
        owner.terminate_ok = False
        owners.append(owner)
        return owner
    provider._tree_owner_factory = fail_cleanup
    with pytest.raises(NativeCodexError, match="cleanup was incomplete"):
        provider.generate(LLMRequest("1.0", "a", "reflect", "private", {}))
    assert owners[-1].terminate_calls == 2
    assert owners[-1].close_calls == 1


def test_windows_job_attachment_is_fail_closed_and_resumes_only_after_assignment(monkeypatch):
    import llm_diplomacy.agents.native_codex_provider as native
    monkeypatch.setattr(native, "_kill_unassigned_process", lambda process: True)
    class FakeKernel:
        def __init__(self, fail=None):
            self.calls = []
            self.fail = fail
        def CreateJobObjectW(self, *_): self.calls.append("create"); return 0 if self.fail == "create" else 41
        def SetInformationJobObject(self, *_): self.calls.append("limits"); return self.fail != "limits"
        def AssignProcessToJobObject(self, *_): self.calls.append("assign"); return self.fail != "assign"
        def TerminateJobObject(self, *_): self.calls.append("terminate"); return True
        def CloseHandle(self, *_): self.calls.append("close"); return True
    class FakeNtdll:
        def __init__(self, api, status=0): self.api = api; self.status = status
        def NtResumeProcess(self, *_): self.api.calls.append("resume"); return self.status
    process = type("Process", (), {"_handle": 55, "pid": 99, "returncode": None,
                                  "poll": lambda self: self.returncode,
                                  "kill": lambda self: setattr(self, "returncode", -9),
                                  "wait": lambda self, timeout=None: setattr(self, "returncode", -9)})()
    api = FakeKernel()
    owner = _attach_windows_job(process, api=api, ntdll=FakeNtdll(api))
    assert api.calls[:4] == ["create", "limits", "assign", "resume"]
    owner.close()
    assert api.calls[-1] == "close"

    for failure in ("create", "limits", "assign", "resume"):
        api = FakeKernel(fail=None if failure == "resume" else failure)
        ntdll = FakeNtdll(api, status=1 if failure == "resume" else 0)
        with pytest.raises(NativeCodexError):
            _attach_windows_job(process, api=api, ntdll=ntdll)
        assert "resume" not in api.calls if failure in {"create", "limits", "assign"} else "resume" in api.calls


def test_process_owner_setup_failure_kills_child_before_prompt(monkeypatch):
    _set_process(monkeypatch, output='{"content":"{}"}')
    _, provider = _provider(monkeypatch)
    import llm_diplomacy.agents.native_codex_provider as native
    killed = []
    monkeypatch.setattr(native, "_kill_unassigned_process", lambda process: (killed.append(process), True)[1])
    provider._tree_owner_factory = lambda process: (_ for _ in ()).throw(OSError("unavailable"))
    with pytest.raises(NativeCodexError, match="ownership could not be established"):
        provider.generate(LLMRequest("1.0", "a", "submit_orders", "private", {}))
    assert killed and _FakeProcess.stdin_value == b""
    expected_flags = 0x204 if __import__("os").name == "nt" else 0
    assert _FakeProcess.options["creationflags"] == expected_flags

def test_budget_ledger_and_runtime_do_not_invent_resolved_model_or_cost(monkeypatch, tmp_path):
    _set_process(monkeypatch, events='{"type":"turn.completed","usage":{"input_tokens":8,"cached_input_tokens":2,"output_tokens":3,"reasoning_output_tokens":1}}\n',
                 output=json.dumps({"content": json.dumps({
        "orders": [], "order_rationale": {"goal_supported": "hold", "why_this_advances_strategy": "safe"},
        "explanation": {"summary": "pass"},
    })}))
    cfg, inner = _provider(monkeypatch)
    tracker = ProviderUsageTracker(tmp_path / "usage")
    provider = BudgetedLLMProvider(inner=inner, budget=None, usage_tracker=tracker,
                                   provider_metadata=cfg.metadata())
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "agents",
                                      provider=provider, provider_metadata=cfg.metadata())
    runtime.register_agent(agent_id="alice")
    result = runtime.submit_orders(AgentContext(
        schema_version="1.0", experiment_id="e", game_id="g", phase_id="1901_spring_movement",
        agent_id="alice", country_assignment="france",
        visible_state={"schema_version": "1.0", "game_id": "g", "phase": "movement", "year": 1901},
    ))
    assert result.ok
    summary = tracker.write_summary()
    row = tracker.rows[0]
    assert row["provider"]["requested_model"] == "gpt-6.1-sol"
    assert row["resolved_model"] is None
    assert row["cached_input_tokens"] == 2
    assert row["reasoning_tokens"] == 1
    assert summary["totals"]["observed_resolved_models"] == []
    assert summary["totals"]["estimated_cost_usd"] is None
    output = next((tmp_path / "agents" / "alice" / "runtime" / "phase_outputs").glob("*.json"))
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["llm_meta"]["provider_model"] is None
    assert artifact["llm_meta"]["provider_metadata"]["requested_model"] == "gpt-6.1-sol"


def test_global_native_selection_is_strict_and_never_fake_substitutes(tmp_path, monkeypatch):
    from llm_diplomacy.config.loader import load_experiment_config
    config_path = tmp_path / "native.json"
    config_path.write_text(json.dumps({
        "schema_version": "1.0", "experiment_id": "native_boot_failure", "games": 1,
        "agents": [{"agent_id": "native", "runtime_kind": "llm"}],
        "rule_engine": {"name": "diplomacy"}, "negotiation": {"max_ticks": 1}, "seed": 1,
        "llm_provider": CONFIG,
    }), encoding="utf-8")
    runner = ScriptedExperimentRunner(config=load_experiment_config(config_path), base_dir=tmp_path)
    assert runner._strict_per_player_mode()
    fake_builds = []
    import llm_diplomacy.runner.scripted_experiment_runner as runner_module
    monkeypatch.setattr(runner, "_preflight_per_player_providers", lambda: None)
    monkeypatch.setattr(runner_module, "build_provider_from_config", lambda cfg: (_ for _ in ()).throw(RuntimeError("unavailable")))
    monkeypatch.setattr(runner_module, "FakeLLMProvider", lambda *args, **kwargs: fake_builds.append(True))
    with pytest.raises(RuntimeError, match="Provider boot failure"):
        runner.run()
    assert fake_builds == []


def test_native_provider_failure_is_one_unknown_cost_attempt(monkeypatch, tmp_path):
    _set_process(monkeypatch, output='{"content":"{}"}', returncode=7)
    cfg, inner = _provider(monkeypatch)
    tracker = ProviderUsageTracker(tmp_path / "usage")
    budget = BudgetController(schema_version="1.0", experiment_id="e", artifact_root=tmp_path / "budget",
                              config=BudgetConfig(max_provider_calls=2))
    wrapped = BudgetedLLMProvider(inner=inner, budget=budget, usage_tracker=tracker,
                                  provider_metadata=cfg.metadata())
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1", schema_version="1.0", runtime_root=tmp_path / "agents",
                                      provider=wrapped, provider_metadata=cfg.metadata())
    runtime.register_agent(agent_id="alice")
    result = runtime.submit_orders(AgentContext(
        schema_version="1.0", experiment_id="e", game_id="g", phase_id="1901_spring_movement",
        agent_id="alice", country_assignment="france",
        visible_state={"schema_version": "1.0", "game_id": "g", "phase": "movement", "year": 1901},
    ))
    assert result.ok
    assert len(tracker.rows) == 1
    assert tracker.rows[0]["error_type"] == "NativeCodexError"
    totals = tracker.summary()["totals"]
    assert totals["provider_calls"] == 1 and totals["unknown_input_tokens_calls"] == 1
    assert budget.total_provider_calls == 1
    assert totals["estimated_cost_usd"] is None
    assert totals["observed_resolved_models"] == []
    output = next((tmp_path / "agents" / "alice" / "runtime" / "phase_outputs").glob("*.json"))
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["llm_meta"]["retry_count"] == 0


def test_malformed_cli_envelope_remains_provider_error_without_runtime_repair(monkeypatch, tmp_path):
    _set_process(monkeypatch, output="[]")
    cfg, inner = _provider(monkeypatch)
    tracker = ProviderUsageTracker(tmp_path / "usage")
    budget = BudgetController(
        schema_version="1.0", experiment_id="bad_envelope", artifact_root=tmp_path / "budget",
        config=BudgetConfig(max_provider_calls=2),
    )
    wrapped = BudgetedLLMProvider(
        inner=inner, budget=budget, usage_tracker=tracker, provider_metadata=cfg.metadata(),
    )
    runtime = BaselineLLMAgentRuntime(reflection_protocol="legacy_json_v1",
        schema_version="1.0", runtime_root=tmp_path / "agents", provider=wrapped,
        provider_metadata=cfg.metadata(),
    )
    runtime.register_agent(agent_id="alice")
    result = runtime.submit_orders(AgentContext(
        schema_version="1.0", experiment_id="bad_envelope", game_id="g",
        phase_id="1901_spring_movement", agent_id="alice", country_assignment="france",
        visible_state={"schema_version": "1.0", "game_id": "g", "phase": "movement", "year": 1901},
    ))
    assert result.ok
    assert budget.total_provider_calls == 1 and len(tracker.rows) == 1
    assert tracker.rows[0]["error_type"] == "NativeCodexError"
    assert tracker.summary()["totals"]["provider_errors"] == 1
    output = next((tmp_path / "agents" / "alice" / "runtime" / "phase_outputs").glob("*.json"))
    metadata = json.loads(output.read_text(encoding="utf-8"))["llm_meta"]
    assert metadata["failure_category"] == "provider_error"
    assert metadata["retry_count"] == 0 and metadata["provider_error"]


def test_budget_block_does_not_launch_native_process(monkeypatch, tmp_path):
    _set_process(monkeypatch)
    cfg, inner = _provider(monkeypatch)
    budget = BudgetController(schema_version="1.0", experiment_id="e", artifact_root=tmp_path / "budget",
                              config=BudgetConfig(max_provider_calls=0))
    tracker = ProviderUsageTracker(tmp_path / "usage")
    wrapped = BudgetedLLMProvider(inner=inner, budget=budget, usage_tracker=tracker, provider_metadata=cfg.metadata())
    response = wrapped.generate(LLMRequest("1.0", "a", "submit_orders", "private", {"game_id": "g", "phase_id": "p"}))
    assert response.raw["budget_blocked"] is True
    assert _FakeProcess.command is None
    assert tracker.rows[0]["kind"] == "blocked"
