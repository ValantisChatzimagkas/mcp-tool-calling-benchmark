"""Server installs and bwrap argv construction. Offline: nothing is installed or spawned."""

import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

import config
import run_eval
import sandbox
from mcp_client import McpServerError

KINDS = config.MCP_SERVER_KINDS


@pytest.fixture
def servers_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_SERVERS_DIR", str(tmp_path / "servers"))
    return (tmp_path / "servers").resolve()


@pytest.fixture
def recorder(monkeypatch):
    argvs = []
    monkeypatch.setattr(sandbox, "_run_installer", argvs.append)
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    return argvs


def test_confine_builds_strict_bwrap_argv(monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "bwrap")
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    tempdir = Path(tempfile.gettempdir()).resolve()
    writable = tempdir / "root" / "docs"
    readonly = [Path("/opt/inst"), Path("/opt/runtime")]

    command, argv = sandbox.confine("node", ["server.js", str(writable)], writable, readonly)

    assert command == "bwrap"
    assert argv[0] == "--unshare-all" and "--die-with-parent" in argv
    for flag in ("--share-net", "--new-session", ".cache", ".npm"):
        assert not any(flag in a for a in argv), flag
    assert argv[argv.index("--ro-bind") : argv.index("--ro-bind") + 3] == ["--ro-bind", "/", "/"]

    masks = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert str(Path.home()) in masks and "/run/user" in masks and str(tempdir) in masks

    last_mask = max(i for i, a in enumerate(argv) if a == "--tmpfs")
    first_rebind = min(i for i, a in enumerate(argv) if i > 6 and a in ("--ro-bind", "--bind"))
    assert last_mask < first_rebind

    binds = [i for i, a in enumerate(argv) if a == "--bind"]
    assert len(binds) == 1 and argv[binds[0] + 1 : binds[0] + 3] == [str(writable)] * 2
    assert argv[-4:] == ["--", "node", "server.js", str(writable)]
    for path in readonly:
        assert ["--ro-bind", str(path), str(path)] == argv[
            argv.index(str(path)) - 1 : argv.index(str(path)) + 2
        ]


def test_confine_rejects_sandbox_outside_the_masked_tempdir(monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "bwrap")
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    with pytest.raises(McpServerError, match="temp dir"):
        sandbox.confine("x", [], Path("/var/not-under-tmp/docs"), [])


def test_install_argv_is_pinned_and_ignores_scripts(servers_dir, recorder):
    sandbox.ensure_installed("filesystem", KINDS["filesystem"])
    target = servers_dir / "filesystem"
    assert recorder == [
        [
            "npm",
            "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--prefix",
            str(target),
            "@modelcontextprotocol/server-filesystem@2.4.0",
        ]
    ]

    recorder.clear()
    sandbox.ensure_installed("sqlite", KINDS["sqlite"])
    target = servers_dir / "sqlite"
    assert recorder == [
        ["uv", "venv", str(target)],
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(target / "bin" / "python"),
            "mcp<2",
            "mcp-server-sqlite==2025.4.25",
        ],
    ]


def test_install_skipped_when_marker_matches_and_redone_when_pin_changes(servers_dir, recorder):
    kind = dict(KINDS["filesystem"])
    sandbox.ensure_installed("filesystem", kind)
    assert len(recorder) == 1

    sandbox.ensure_installed("filesystem", kind)
    assert len(recorder) == 1  # marker matches: skipped

    kind["install"] = {"npm": "@modelcontextprotocol/server-filesystem@9.9.9"}
    sandbox.ensure_installed("filesystem", kind)
    assert len(recorder) == 2 and recorder[-1][-1].endswith("@9.9.9")


def test_partial_install_without_marker_is_redone(servers_dir, recorder):
    partial = servers_dir / "filesystem"
    partial.mkdir(parents=True)
    (partial / "half-written").write_text("x")

    sandbox.ensure_installed("filesystem", KINDS["filesystem"])

    assert len(recorder) == 1
    assert not (partial / "half-written").exists()
    assert (partial / sandbox.INSTALL_MARKER).exists()


def test_kind_without_install_is_not_installed(servers_dir, recorder):
    assert sandbox.ensure_installed("fake", {"command": "x", "args": [], "seed": "dir"}) is None
    assert recorder == []


def test_install_failure_is_mcp_server_error(servers_dir, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda tool: None)
    with pytest.raises(McpServerError, match="npm"):
        sandbox.ensure_installed("filesystem", KINDS["filesystem"])

    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def failing(argv):
        raise McpServerError("install failed: boom")

    monkeypatch.setattr(sandbox, "_run_installer", failing)
    case = {
        "id": "selection_x",
        "type": "single_call",
        "category": "selection",
        "prompt": "p",
        "mcp_servers": {"docs": {"kind": "filesystem", "fixture": "docs"}},
        "expected_tool_call": {"name": "docs__x", "args": {"a": "b"}},
    }
    passed, total, note = run_eval.run_case_repeated("m", case, {}, runs=2)
    assert (passed, total) == (0, 2)
    assert note.startswith("mcp server error:") and "boom" in note


def test_install_guard_blocks_offline_tests(monkeypatch):
    """The autouse guard is what stops a stray test from running npm/uv for real."""
    with pytest.raises(pytest.fail.Exception, match="install attempted outside -m live"):
        sandbox._run_installer(["npm", "--version"])


def test_missing_bwrap_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "bwrap")
    monkeypatch.setattr(shutil, "which", lambda tool: None)
    with pytest.raises(McpServerError, match="MCP_CONFINEMENT"):
        sandbox.confine("x", [], Path(tempfile.gettempdir()) / "a" / "b", [])

    # Through the runner: failed run, note, and nothing was spawned.
    monkeypatch.setattr(config, "FIXTURES_DIR", str(tmp_path))
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(
        config,
        "MCP_SERVER_KINDS",
        {"fake": {"command": sys.executable, "args": ["-c", "pass"], "seed": "dir"}},
    )
    spawned = []
    monkeypatch.setattr("mcp_client.stdio_client", lambda *a, **k: spawned.append(a))
    case = {
        "id": "selection_x",
        "type": "single_call",
        "category": "selection",
        "prompt": "p",
        "mcp_servers": {"docs": {"kind": "fake", "fixture": "docs"}},
        "expected_tool_call": {"name": "docs__x", "args": {"a": "b"}},
    }
    passed, total, note = run_eval.run_case_repeated("m", case, {}, runs=2)
    assert (passed, total) == (0, 2)
    assert note.startswith("mcp server error:") and "MCP_CONFINEMENT" in note
    assert spawned == []


def test_confinement_none_is_explicit_opt_out(monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "none")
    monkeypatch.setattr(shutil, "which", lambda tool: None)  # bwrap absent: irrelevant
    assert sandbox.confine("node", ["a"], Path("/x"), [Path("/y")]) == ("node", ["a"])


def test_unknown_confinement_value_fails(monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "docker")
    with pytest.raises(McpServerError, match="docker"):
        sandbox.confine("node", [], Path("/x"), [])


def test_server_kinds_are_pinned():
    for name, kind in KINDS.items():
        install = kind["install"]
        if "npm" in install:
            assert re.search(r"@\d+\.\d+\.\d+$", install["npm"]), f"{name}: unpinned npm package"
        if "uv" in install:
            for req in install["uv"]:
                assert req == "mcp<2" or "==" in req, f"{name}: unpinned requirement {req!r}"
        assert kind["runtime"] in {"node", "venv"}
        assert kind["seed"] in {"dir", "sql"}
        assert sum(a.count("{path}") for a in [kind["command"], *kind["args"]]) == 1, name
