"""Real pinned servers under real bwrap. Excluded by default: run `uv run pytest -m live`.

Needs node/npm, uv, bwrap, and network on the very first run (to install the pinned
servers into .mcp-servers/). Every escape test runs twice: with MCP_CONFINEMENT = "none"
as a control that the escape is real and detectable, and with "bwrap" asserting it is
blocked — so "fails closed" is demonstrated on every live run, not once by hand.
"""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

import config
import run_eval
import sandbox
from mcp_client import McpServerError

pytestmark = pytest.mark.live


@pytest.fixture(params=["none", "bwrap"])
def confinement(request, monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", request.param)
    return request.param


@pytest.fixture
def bwrap_only(monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "bwrap")


def server(kind, fixture, tools=None):
    entry = {"kind": kind, "fixture": fixture}
    if tools:
        entry["tools"] = tools
    return entry


def case_with(servers):
    return {"mcp_servers": servers}


def host_scratch_dir():
    """A unique dir under ~/.cache: host data the sandbox must not reach."""
    base = Path.home() / ".cache"
    base.mkdir(exist_ok=True)
    return Path(base / f"mcp-bench-live-{uuid.uuid4().hex}")


def run_in_profile(kind_name, code):
    """Run python code under the kind's real confine() argv instead of the server itself."""
    kind = config.MCP_SERVER_KINDS[kind_name]
    install = sandbox.ensure_installed(kind_name, kind)
    servers = {"db": server(kind_name, "crm.sql")}
    with sandbox.open_sandbox(servers, config.MCP_SERVER_KINDS, config.FIXTURES_DIR) as box:
        command, argv = sandbox.confine(
            str(install / "bin" / "python"),
            ["-c", code],
            box.dirs["db"],
            [install, sandbox.runtime_prefix(kind, install)],
        )
        # "ran" proves the interpreter started inside the profile, so a bwrap that failed
        # to launch at all can't make a "blocked" assertion pass vacuously.
        wrapped = f"print('ran'); {code}"
        argv[argv.index("-c") + 1] = wrapped
        return subprocess.run([command, *argv], capture_output=True, text=True, timeout=60)


def test_live_filesystem_tools_and_real_read(bwrap_only):
    case = case_with({"docs": server("filesystem", "docs", ["read_text_file", "list_directory"])})
    with run_eval.open_mcp_case(case) as (tools, execute, paths):
        names = [t["function"]["name"] for t in tools]
        assert names == ["docs__read_text_file", "docs__list_directory"]
        text = execute("docs__read_text_file", {"path": f"{paths['docs']}/readme.md"})
        assert "Project Mercury" in text


def test_live_sqlite_tools_and_real_query(bwrap_only):
    case = case_with({"db": server("sqlite", "crm.sql", ["read_query", "list_tables"])})
    with run_eval.open_mcp_case(case) as (tools, execute, _paths):
        assert [t["function"]["name"] for t in tools] == ["db__read_query", "db__list_tables"]
        result = execute(
            "db__read_query", {"query": "SELECT email FROM people WHERE name = 'Ada Lovelace'"}
        )
        assert "ada@example.com" in result


def test_live_two_filesystem_instances_collide_but_are_prefixed_and_isolated(bwrap_only):
    tools_allowed = ["read_text_file"]
    case = case_with(
        {
            "docs": server("filesystem", "docs", tools_allowed),
            "notes": server("filesystem", "notes", tools_allowed),
        }
    )
    with run_eval.open_mcp_case(case) as (tools, execute, paths):
        names = [t["function"]["name"] for t in tools]
        assert names == ["docs__read_text_file", "notes__read_text_file"]
        assert "Renew the staging TLS" in execute(
            "notes__read_text_file", {"path": f"{paths['notes']}/todo.txt"}
        )
        denied = execute("docs__read_text_file", {"path": f"{paths['notes']}/todo.txt"})
        assert "Renew the staging TLS" not in denied
        assert "denied" in denied.lower() or "outside" in denied.lower()


def test_live_sqlite_attach_cannot_write_into_package_cache(confinement):
    cache = Path.home() / ".cache" / "uv"
    if not cache.is_dir():
        pytest.skip("no ~/.cache/uv on this host to target")
    victim = cache / f"mcp-bench-live-{uuid.uuid4().hex}.db"
    case = case_with({"db": server("sqlite", "crm.sql", ["write_query"])})
    try:
        with run_eval.open_mcp_case(case) as (_tools, execute, _paths):
            execute("db__write_query", {"query": f"ATTACH DATABASE '{victim}' AS x"})
        if confinement == "none":
            assert victim.exists(), "control: the unconfined server should have created the file"
        else:
            assert not victim.exists(), "bwrap let ATTACH create a file on the host"
    finally:
        victim.unlink(missing_ok=True)


def test_live_sandbox_cannot_read_home_file(confinement):
    scratch = host_scratch_dir()
    scratch.mkdir()
    marker = scratch / "marker.txt"
    marker.write_text("host-secret-marker")
    try:
        result = run_in_profile("sqlite", f"print(open({str(marker)!r}).read())")
        if confinement == "none":
            assert "host-secret-marker" in result.stdout, result.stderr
        else:
            assert "ran" in result.stdout and result.returncode != 0, result.stderr
            assert "host-secret-marker" not in result.stdout + result.stderr
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_live_sandbox_has_no_network(confinement):
    code = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3); print('connected')"
    result = run_in_profile("sqlite", code)
    if confinement == "none":
        if "connected" not in result.stdout:
            pytest.skip(f"host itself has no network: {result.stderr.strip()[-120:]}")
    else:
        assert "ran" in result.stdout and "connected" not in result.stdout, result.stderr
        assert result.returncode != 0 and "OSError" in result.stderr


def test_live_sandbox_hides_session_bus(confinement):
    bus = f"/run/user/{os.getuid()}/bus"
    result = run_in_profile("sqlite", f"import os; print(os.path.exists({bus!r}))")
    if confinement == "none":
        if "True" not in result.stdout:
            pytest.skip("host has no session bus to hide")
    else:
        assert result.stdout.split() == ["ran", "False"], result.stderr


def test_live_filesystem_cannot_write_outside_sandbox(confinement):
    victim = host_scratch_dir()
    case = case_with({"docs": server("filesystem", "docs", ["write_file"])})
    try:
        with run_eval.open_mcp_case(case) as (_tools, execute, _paths):
            execute("docs__write_file", {"path": str(victim / "x.txt"), "content": "escaped"})
        # The server denies this natively, so the control also stays blocked; the OS
        # profile is what is proven by the ATTACH test above.
        assert not victim.exists()
    finally:
        shutil.rmtree(victim, ignore_errors=True)


def test_live_teardown_leaves_no_host_process(bwrap_only):
    case = case_with(
        {
            "docs": server("filesystem", "docs", ["read_text_file"]),
            "db": server("sqlite", "crm.sql", ["read_query"]),
        }
    )
    with run_eval.open_mcp_case(case) as (_tools, _execute, paths):
        root = str(paths["docs"].parent)
        assert _processes_mentioning(root), "expected the servers to be running"

    deadline = time.monotonic() + config.MCP_SHUTDOWN_TIMEOUT_SECONDS
    while time.monotonic() < deadline and _processes_mentioning(root):
        time.sleep(0.2)
    assert _processes_mentioning(root) == []


def _processes_mentioning(text):
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            cmdline = Path(f"/proc/{name}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if text in cmdline:
            found.append((int(name), cmdline[:120]))
    return found


def test_live_missing_server_name_is_reported_not_silently_dropped(bwrap_only):
    case = case_with({"docs": server("filesystem", "docs", ["no_such_tool"])})
    with pytest.raises(McpServerError, match="no_such_tool"), run_eval.open_mcp_case(case):
        pass
