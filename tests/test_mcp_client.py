"""The MCP facade, exercised over a real stdio subprocess (tests/fake_mcp_server.py).

No network, no node/npm/uv-launched servers, no bwrap: the fake server is run with this
interpreter, so these tests hold on a machine with none of those installed.
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import mcp_client
from mcp_client import McpServerError, McpServers, ServerSpec, _list_all_tools

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def fake_spec(name, root, allow_tools=None):
    return ServerSpec(
        name=name,
        command=sys.executable,
        args=[FAKE_SERVER, str(root)],
        allow_tools=allow_tools,
    )


@pytest.fixture
def servers(tmp_path):
    """One running fake server named `a`."""
    root = tmp_path / "a"
    root.mkdir()
    with McpServers([fake_spec("a", root)]) as running:
        yield running


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); read its state instead.
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


def wait_until_gone(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def test_tools_are_prefixed_and_schema_passes_through(tmp_path):
    for name in "ab":
        (tmp_path / name).mkdir()
    with McpServers([fake_spec("a", tmp_path / "a"), fake_spec("b", tmp_path / "b")]) as running:
        names = [t["function"]["name"] for t in running.tools]
        assert "a__echo" in names and "b__echo" in names
        assert len(names) == len(set(names))
        echo = next(t for t in running.tools if t["function"]["name"] == "a__echo")
        assert echo["type"] == "function"
        assert echo["function"]["parameters"]["properties"]["text"]["type"] == "string"
        assert echo["function"]["parameters"]["required"] == ["text"]


def test_allow_list_filters_and_unknown_name_fails(tmp_path):
    with McpServers([fake_spec("a", tmp_path, allow_tools=["echo"])]) as running:
        assert [t["function"]["name"] for t in running.tools] == ["a__echo"]

    with (
        pytest.raises(McpServerError, match="nope"),
        McpServers([fake_spec("a", tmp_path, allow_tools=["nope"])]),
    ):
        pass


def test_list_all_tools_follows_cursor():
    pages = {
        None: SimpleNamespace(tools=["t1", "t2"], next_cursor="page2"),
        "page2": SimpleNamespace(tools=["t3"], next_cursor=None),
    }

    class StubSession:
        async def list_tools(self, params=None):
            return pages[params.cursor if params else None]

    assert asyncio.run(_list_all_tools(StubSession())) == ["t1", "t2", "t3"]


def test_call_executes_and_returns_text(servers):
    assert servers.call("a__echo", {"text": "hi"}) == "hi"


def test_error_result_passes_through_as_text(servers):
    """A tool that raises comes back as an is_error result; the model should see it."""
    text = servers.call("a__read_note", {"name": "does-not-exist"})
    assert text.startswith("Error executing tool read_note")


def test_unknown_tool_returns_error_text_not_exception(servers):
    assert servers.call("a__nope", {}) == "Error: unknown tool 'a__nope'"
    assert servers.call("echo", {"text": "x"}) == "Error: unknown tool 'echo'"


def test_child_env_has_no_api_key(servers, monkeypatch):
    # The env is fixed when the child is spawned, so check against a fresh server.
    monkeypatch.setenv("GROQ_API_KEY", "sentinel-secret")
    with McpServers([fake_spec("a", Path("."))]) as running:
        assert "GROQ_API_KEY" not in running.call("a__env_keys", {})


def test_missing_binary_is_mcp_server_error():
    spec = ServerSpec(name="a", command="no-such-binary-xyz", args=[])
    with pytest.raises(McpServerError, match="no-such-binary-xyz"), McpServers([spec]):
        pass


def test_startup_exit_includes_stderr_tail():
    spec = ServerSpec(
        name="a",
        command=sys.executable,
        args=["-c", "import sys; print('boom-marker', file=sys.stderr); sys.exit(3)"],
    )
    with pytest.raises(McpServerError, match="boom-marker"), McpServers([spec]):
        pass


def test_crash_mid_call_raises_and_does_not_hang(servers):
    started = time.monotonic()
    with pytest.raises(McpServerError):
        servers.call("a__boom", {})
    assert time.monotonic() - started < config.MCP_CALL_TIMEOUT_SECONDS


def test_hang_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_CALL_TIMEOUT_SECONDS", 1)
    with (
        McpServers([fake_spec("a", tmp_path)]) as running,
        pytest.raises(McpServerError, match="timed out"),
    ):
        running.call("a__hang", {})


def test_exit_reaps_process_tree(tmp_path):
    with McpServers([fake_spec("a", tmp_path)]) as running:
        server_pid = int(running.call("a__pid", {}))
        child_pid = int(running.call("a__spawn_child", {}))
        assert pid_alive(server_pid) and pid_alive(child_pid)

    assert wait_until_gone(server_pid, config.MCP_SHUTDOWN_TIMEOUT_SECONDS)
    assert wait_until_gone(child_pid, config.MCP_SHUTDOWN_TIMEOUT_SECONDS)


def test_server_that_exits_on_stdin_close_still_has_grandchild_reaped(tmp_path):
    """The SDK skips its own killpg when the server exits by itself after stdin closes."""
    with McpServers([fake_spec("a", tmp_path)]) as running:
        child_pid = int(running.call("a__spawn_child", {}))
        assert pid_alive(child_pid)

    assert wait_until_gone(child_pid, config.MCP_SHUTDOWN_TIMEOUT_SECONDS)


def test_two_servers_each_have_their_own_group_reaped(tmp_path):
    for name in "ab":
        (tmp_path / name).mkdir()
    with McpServers([fake_spec("a", tmp_path / "a"), fake_spec("b", tmp_path / "b")]) as running:
        pids = {
            "a": int(running.call("a__spawn_child", {})),
            "b": int(running.call("b__spawn_child", {})),
        }
        assert len(running._pgids) == 2
        assert len(set(running._pgids)) == 2
        assert os.getpgrp() not in running._pgids

    for pid in pids.values():
        assert wait_until_gone(pid, config.MCP_SHUTDOWN_TIMEOUT_SECONDS)


def test_kill_group_never_signals_our_own_group(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "killpg", lambda *a: calls.append(a))
    mcp_client._kill_group(os.getpgrp())
    assert calls == []


def test_hang_teardown_is_bounded_and_reaps(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_CALL_TIMEOUT_SECONDS", 1)
    with McpServers([fake_spec("a", tmp_path)]) as running:
        server_pid = int(running.call("a__pid", {}))
        with pytest.raises(McpServerError, match="timed out"):
            running.call("a__hang", {})
        left = time.monotonic()

    assert time.monotonic() - left < config.MCP_SHUTDOWN_TIMEOUT_SECONDS
    assert wait_until_gone(server_pid, config.MCP_SHUTDOWN_TIMEOUT_SECONDS)
