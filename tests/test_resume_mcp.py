"""Server-backed (`mcp_servers`) cases under the run journal and `--resume`.

A server-backed case carries no tool schemas of its own: the model is shown whatever
the live server's `tools/list` returns. The journal's case hash therefore has to cover
what determines that (the pinned server spec and seed fixture), without needing a live
server, or --resume would silently reuse runs made against other tools. Run through the
fake Ollama (conftest.py) with the fake stdio server from tests/fake_mcp_server.py.
"""

import concurrent.futures
import copy
import csv
import json
import sys
import threading
from pathlib import Path

import pytest

import config
import journal
import run_eval
from client import ServerUnreachable
from run_eval import main

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")
MODEL = "m4b"
FAKE_KIND = {"command": sys.executable, "args": [FAKE_SERVER, "{path}"], "seed": "dir"}


def server_case(**overrides):
    # no_call: the fake Ollama's default plain-text reply passes it.
    case = {
        "id": "restraint_fake_server",
        "type": "no_call",
        "category": "restraint",
        "prompt": "Say hello without using any tool.",
        "mcp_servers": {"docs": {"kind": "fake", "fixture": "docs"}},
    }
    case.update(overrides)
    return case


@pytest.fixture
def mcp_env(fake_ollama, tmp_path, monkeypatch):
    """The fake server kind, a seeded fixtures dir, and a suite file holding one case."""
    fixtures = tmp_path / "fixtures"
    (fixtures / "docs").mkdir(parents=True)
    (fixtures / "docs" / "hello.txt").write_text("hello from the fixture")
    monkeypatch.setattr(config, "FIXTURES_DIR", str(fixtures))
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "none")
    monkeypatch.setattr(config, "MCP_SERVER_KINDS", {"fake": copy.deepcopy(FAKE_KIND)})
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps({"cases": [server_case()]}))
    fake_ollama.suite_path = suite_path
    fake_ollama.fixtures = fixtures
    return fake_ollama


def argv(env, *extra):
    return ["--suite", str(env.suite_path), "--models", MODEL, "--runs", "1", *extra]


def read_rows(env):
    with open(env.tmp_path / "results.csv", newline="") as f:
        return list(csv.DictReader(f))


def seed_record(env, case, **overrides):
    """A journaled run of `case`, keyed to the pin and fixtures as they are right now."""
    record = {
        "backend": config.BACKEND,
        "endpoint": config.API_BASE_URL,
        "model": MODEL,
        "quantization": "Q4_K_M",
        "case_id": case["id"],
        "case_hash": journal.case_fingerprint(case, run_eval.case_tools(case, {})),
        "run_index": 0,
        "passed": True,
        "note": "ok",
        "context_length": 16384,
        "timestamp": "2026-11-23T10:00:00+00:00",
    }
    record.update(overrides)
    journal.append(str(env.tmp_path / "results.journal.jsonl"), record)


def fingerprint(case):
    return journal.case_fingerprint(case, run_eval.case_tools(case, {}))


# --- the case hash covers what the model is given -----------------------------


def test_hash_changes_when_a_server_pin_changes(mcp_env, monkeypatch):
    kinds = {"fake": {**FAKE_KIND, "install": {"npm": "some-server@1.0.0"}}}
    monkeypatch.setattr(config, "MCP_SERVER_KINDS", kinds)
    case = server_case()
    before = fingerprint(case)

    kinds["fake"]["install"] = {"npm": "some-server@1.1.0"}

    assert fingerprint(case) != before


def test_hash_changes_when_a_server_launch_line_changes(mcp_env):
    case = server_case()
    before = fingerprint(case)

    config.MCP_SERVER_KINDS["fake"]["args"] = [FAKE_SERVER, "--other", "{path}"]

    assert fingerprint(case) != before


def test_hash_changes_when_the_seed_fixture_changes(mcp_env):
    case = server_case()
    before = fingerprint(case)

    (mcp_env.fixtures / "docs" / "hello.txt").write_text("edited")

    assert fingerprint(case) != before


def test_hash_is_stable_across_calls_and_copies(mcp_env):
    case = server_case()

    assert fingerprint(case) == fingerprint(copy.deepcopy(case))


def test_hash_needs_no_server_install_or_spawn(mcp_env, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("hashing a server-backed case must not install or start a server")

    monkeypatch.setattr(run_eval, "ensure_installed", forbidden)
    monkeypatch.setattr(run_eval, "open_mcp_case", forbidden)
    monkeypatch.setattr(run_eval, "McpServers", forbidden)

    fingerprint(server_case())


def test_hash_of_a_handwritten_case_is_its_tool_schemas():
    registry, cases = run_eval.load_suite(config.CASES_PATH)
    case = next(c for c in cases if "available_tools" in c)

    assert run_eval.case_tools(case, registry) == [registry[n] for n in case["available_tools"]]


# --- --resume with server-backed cases ---------------------------------------


def test_resume_reuses_a_server_case_run_when_nothing_changed(mcp_env):
    seed_record(mcp_env, server_case())

    main(argv(mcp_env, "--resume"))

    assert mcp_env.chat_requests == []
    (row,) = read_rows(mcp_env)
    assert (row["runs_passed"], row["runs_total"]) == ("1", "1")


def test_resume_reruns_a_server_case_when_the_pinned_server_changed(mcp_env, monkeypatch):
    seed_record(mcp_env, server_case())
    config.MCP_SERVER_KINDS["fake"]["install"] = {"npm": "bumped-server@9.9.9"}
    # Nothing is installed offline: the fake kind runs as given, the pin only reaches the hash.
    monkeypatch.setattr(run_eval, "ensure_installed", lambda kind, spec: None)

    main(argv(mcp_env, "--resume"))

    assert len(mcp_env.chat_requests) == 1
    (row,) = read_rows(mcp_env)
    assert (row["runs_passed"], row["runs_total"]) == ("1", "1")  # stale record not counted too


# --- journaling, abort and unsupported-model semantics ------------------------


def test_server_case_on_a_model_without_tool_support_is_unsupported(mcp_env, monkeypatch):
    mcp_env.show[MODEL] = {
        "details": {"quantization_level": "Q4_K_M"},
        "capabilities": ["completion"],
    }

    def forbidden(*args, **kwargs):
        pytest.fail("an unsupported model must not touch a server")

    monkeypatch.setattr(run_eval, "open_mcp_case", forbidden)

    main(argv(mcp_env))

    assert mcp_env.chat_requests == []
    (row,) = read_rows(mcp_env)
    assert (row["status"], row["runs_passed"], row["runs_total"]) == ("unsupported", "0", "0")


def test_server_spawn_failure_is_recorded_with_mcp_server_error_note(mcp_env):
    config.MCP_SERVER_KINDS["fake"]["command"] = "no-such-binary-xyz"

    main(argv(mcp_env))

    (row,) = read_rows(mcp_env)
    assert (row["runs_passed"], row["runs_total"]) == ("0", "1")
    assert row["notes"].startswith("mcp server error:")
    assert mcp_env.chat_requests == []


def test_dead_model_server_mid_server_case_aborts_without_journaling_the_run(mcp_env, monkeypatch):
    def dead(model, messages, tools):
        raise ServerUnreachable("connection refused")

    monkeypatch.setattr(run_eval, "chat", dead)

    with pytest.raises(SystemExit) as exc:
        main(argv(mcp_env))

    assert "--resume" in str(exc.value) and "unreachable" in str(exc.value)
    journal_path = mcp_env.tmp_path / "results.journal.jsonl"
    assert journal.load(str(journal_path)) == []


def test_ctrl_c_during_a_server_tool_call_stops_the_sweep_without_journaling(mcp_env, monkeypatch):
    # Ctrl-C lands in the main thread while it waits on the server's `tools/call` result.
    # It must reach main's interrupt path, not be scored as a failed run that --resume
    # would then treat as done.
    case = server_case(
        id="chaining_fake_server",
        type="multi_call_chain",
        category="chaining",
        prompt="Echo x.",
        expected_tool_call={"sequence": [{"name": "docs__echo", "args": {"text": "x"}}]},
    )
    mcp_env.suite_path.write_text(json.dumps({"cases": [case]}))
    real_result = concurrent.futures.Future.result

    def interrupt_once(self, timeout=None):
        if threading.current_thread() is threading.main_thread():
            monkeypatch.setattr(concurrent.futures.Future, "result", real_result)
            raise KeyboardInterrupt
        return real_result(self, timeout)

    def tool_call(model, messages, tools):
        # The servers are up by now: interrupt the next wait, which is the tool call's.
        monkeypatch.setattr(concurrent.futures.Future, "result", interrupt_once)
        arguments = json.dumps({"text": "x"})
        function = {"name": "docs__echo", "arguments": arguments}
        return {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": function}],
        }

    monkeypatch.setattr(run_eval, "chat", tool_call)

    with pytest.raises(SystemExit) as exc:
        main(argv(mcp_env))

    assert "interrupted" in str(exc.value) and "--resume" in str(exc.value)
    assert journal.load(str(mcp_env.tmp_path / "results.journal.jsonl")) == []
    assert list(mcp_env.tmp_path.glob("results.journal.*.jsonl")) == []  # nothing archived
