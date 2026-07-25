import subprocess

import pytest

from llm.claude_code import ClaudeCodeCallFailed, ClaudeCodeClient


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


ENVELOPE_OK = (
    '{"is_error": false, "total_cost_usd": 0.12, '
    '"structured_output": {"theses": []}}'
)


def _client(run):
    return ClaudeCodeClient(json_schema={}, run=run)


def test_create_returns_tool_use_shaped_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = lambda *a, **k: _FakeProc(stdout=ENVELOPE_OK)
    client = _client(run)
    message = client.messages.create(
        model="claude-sonnet-5", max_tokens=8192, system="sys",
        tools=[{"input_schema": {}}], tool_choice={}, messages=[{"role": "user", "content": "u"}],
    )
    assert message.content[0].type == "tool_use"
    assert message.content[0].input == {"theses": []}


def test_create_strips_anthropic_api_key_from_subprocess_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-never-be-used")
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc(stdout=ENVELOPE_OK)

    client = _client(fake_run)
    client.messages.create(model="m", max_tokens=1, system="s", tools=[], tool_choice={},
                           messages=[{"role": "user", "content": "u"}])
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_create_passes_model_system_and_stdin(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _FakeProc(stdout=ENVELOPE_OK)

    client = _client(fake_run)
    client.messages.create(model="claude-sonnet-5", max_tokens=1, system="be terse",
                           tools=[], tool_choice={},
                           messages=[{"role": "user", "content": "transcript body"}])
    assert "claude" in captured["argv"][0]
    assert "--model" in captured["argv"] and "claude-sonnet-5" in captured["argv"]
    assert "--system-prompt" in captured["argv"] and "be terse" in captured["argv"]
    assert captured["input"] == "transcript body"


def test_create_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = lambda *a, **k: _FakeProc(returncode=1, stderr="boom")
    with pytest.raises(ClaudeCodeCallFailed):
        _client(run).messages.create(model="m", max_tokens=1, system="s", tools=[],
                                     tool_choice={}, messages=[{"role": "user", "content": "u"}])


def test_create_raises_on_timeout(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    with pytest.raises(ClaudeCodeCallFailed):
        _client(fake_run).messages.create(model="m", max_tokens=1, system="s", tools=[],
                                          tool_choice={}, messages=[{"role": "user", "content": "u"}])


def test_create_raises_when_is_error_true(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = lambda *a, **k: _FakeProc(stdout='{"is_error": true, "result": "auth failed"}')
    with pytest.raises(ClaudeCodeCallFailed):
        _client(run).messages.create(model="m", max_tokens=1, system="s", tools=[],
                                     tool_choice={}, messages=[{"role": "user", "content": "u"}])


def test_create_raises_when_no_structured_output(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = lambda *a, **k: _FakeProc(stdout='{"is_error": false}')
    with pytest.raises(ClaudeCodeCallFailed):
        _client(run).messages.create(model="m", max_tokens=1, system="s", tools=[],
                                     tool_choice={}, messages=[{"role": "user", "content": "u"}])


def test_create_reports_usage_equivalent_cost_to_stderr(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = lambda *a, **k: _FakeProc(stdout=ENVELOPE_OK)
    _client(run).messages.create(model="m", max_tokens=1, system="s", tools=[],
                                 tool_choice={}, messages=[{"role": "user", "content": "u"}])
    assert "0.12" in capsys.readouterr().err


def test_constructing_without_json_schema_raises_type_error():
    # Pins the required-param decision: no default schema, ever.
    with pytest.raises(TypeError):
        ClaudeCodeClient()


def test_json_schema_passed_to_constructor_lands_in_argv(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sentinel_schema = {"type": "object", "properties": {"__sentinel_marker__": {"type": "string"}}}
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _FakeProc(stdout=ENVELOPE_OK)

    client = ClaudeCodeClient(json_schema=sentinel_schema, run=fake_run)
    client.messages.create(model="m", max_tokens=1, system="s", tools=[], tool_choice={},
                           messages=[{"role": "user", "content": "u"}])
    argv = captured["argv"]
    assert "--json-schema" in argv
    schema_arg = argv[argv.index("--json-schema") + 1]
    assert "__sentinel_marker__" in schema_arg
