"""Server-backed cases through run_case / run_case_repeated / main, offline.

`run_eval.chat` is replaced by a scripted fake and the MCP "kind" is the fake server in
tests/fake_mcp_server.py, run unconfined with this interpreter. No network, no
node/npm/uv-launched servers.
"""

import copy
import csv
import json
import sys
from pathlib import Path

import pytest

import config
import run_eval
from mcp_client import McpServerError

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def call_msg(name, arguments, call_id="c1"):
    return {
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def text_msg(text="done"):
    return {"content": text}


class ScriptedChat:
    """Replays a fixed list of assistant messages and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, model, messages, tools):
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools})
        return self.replies.pop(0) if self.replies else text_msg()


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """A `fake` server kind seeded from tmp_path/fixtures/docs, confinement off."""
    fixtures = tmp_path / "fixtures"
    (fixtures / "docs").mkdir(parents=True)
    (fixtures / "docs" / "hello.txt").write_text("hello from the fixture")
    monkeypatch.setattr(config, "FIXTURES_DIR", str(fixtures))
    monkeypatch.setattr(config, "MCP_CONFINEMENT", "none")
    monkeypatch.setattr(config, "DELAY_BETWEEN_CALLS_SECONDS", 0)
    monkeypatch.setattr(
        config,
        "MCP_SERVER_KINDS",
        {"fake": {"command": sys.executable, "args": [FAKE_SERVER, "{path}"], "seed": "dir"}},
    )
    return fixtures


def single_call_case(**overrides):
    case = {
        "id": "selection_fake_read",
        "type": "single_call",
        "category": "selection",
        "prompt": "Read ${docs}/hello.txt",
        "mcp_servers": {"docs": {"kind": "fake", "fixture": "docs"}},
        "expected_tool_call": {"name": "docs__echo", "args": {"text": "${docs}/hello.txt"}},
    }
    case.update(overrides)
    return case


def chain_case(sequence, prompt="Use the tools"):
    return {
        "id": "chaining_fake",
        "type": "multi_call_chain",
        "category": "chaining",
        "prompt": prompt,
        "mcp_servers": {"docs": {"kind": "fake", "fixture": "docs"}},
        "expected_tool_call": {"sequence": sequence},
    }


def test_model_receives_server_tools_with_prefix(fake_env, monkeypatch):
    chat = ScriptedChat(call_msg("docs__echo", {"text": "x"}))
    monkeypatch.setattr(run_eval, "chat", chat)
    case = single_call_case(
        mcp_servers={"docs": {"kind": "fake", "fixture": "docs", "tools": ["echo", "read_note"]}}
    )

    run_eval.run_case("m", case, {})

    names = [t["function"]["name"] for t in chat.requests[0]["tools"]]
    assert names == ["docs__echo", "docs__read_note"]


def test_chain_executes_tools_call_and_model_sees_real_result(fake_env, monkeypatch):
    chat = ScriptedChat(
        call_msg("docs__read_note", {"name": "hello.txt"}),
        text_msg("all done"),
    )
    monkeypatch.setattr(run_eval, "chat", chat)
    case = chain_case([{"name": "docs__read_note", "args": {"name": "hello.txt"}}])

    ok, note = run_eval.run_case("m", case, {})

    assert ok, note
    tool_messages = [m for m in chat.requests[1]["messages"] if m["role"] == "tool"]
    assert [m["content"] for m in tool_messages] == ["hello from the fixture"]


def test_canned_chain_case_unchanged(monkeypatch):
    monkeypatch.setattr(config, "DELAY_BETWEEN_CALLS_SECONDS", 0)
    registry, cases = run_eval.load_suite(config.CASES_PATH)
    case = next(c for c in cases if c["id"] == "chaining_arg_from_output")
    chat = ScriptedChat(
        call_msg("calendar_lookup", {"person": "maria", "date": "2026-03-12"}, "a"),
        call_msg("send_message", {"recipient": "dimitris", "body": "confirm"}, "b"),
        call_msg("never_listed", {}, "c"),
        text_msg(),
    )
    monkeypatch.setattr(run_eval, "chat", chat)

    run_eval.run_case("m", case, registry)

    final = chat.requests[-1]["messages"]
    tool_contents = [m["content"] for m in final if m["role"] == "tool"]
    assert tool_contents == [
        case["tool_results"]["calendar_lookup"],
        case["tool_results"]["send_message"],
        "{}",
    ]


def test_each_run_gets_a_fresh_seeded_sandbox(fake_env):
    case = chain_case([])
    with run_eval.open_mcp_case(case) as (_tools, execute, paths):
        root1 = paths["docs"]
        assert execute("docs__write_note", {"name": "scratch.txt", "text": "run1"}) == (
            "wrote scratch.txt"
        )
        assert (root1 / "scratch.txt").read_text() == "run1"
    with run_eval.open_mcp_case(case) as (_tools, _execute, paths):
        root2 = paths["docs"]
        assert root2 != root1
        assert not (root2 / "scratch.txt").exists()
        assert (root2 / "hello.txt").read_text() == "hello from the fixture"

    assert not root1.exists() and not root2.exists()
    assert sorted(p.name for p in (fake_env / "docs").iterdir()) == ["hello.txt"]


def test_placeholders_substituted_in_prompt_and_expectation(fake_env, monkeypatch):
    prompts = []

    def echoing_chat(model, messages, tools):
        prompts.append(messages[0]["content"])
        return call_msg("docs__echo", {"text": messages[0]["content"].split()[-1]})

    monkeypatch.setattr(run_eval, "chat", echoing_chat)
    ok, note = run_eval.run_case("m", single_call_case(), {})

    # The prompt and the expectation resolved to the same real path.
    assert ok, note
    path = prompts[0].split()[-1]
    assert "${" not in path and path.startswith("/") and path.endswith("/docs/hello.txt")

    with pytest.raises(KeyError):
        run_eval.run_case("m", single_call_case(prompt="Read ${nope}/x"), {})


def test_substitute_paths_is_recursive_and_strict():
    paths = {"a": Path("/tmp/x/a")}
    value = {"p": "${a}/f", "l": ["${a}", 3], "n": None}
    assert run_eval.substitute_paths(value, paths) == {
        "p": "/tmp/x/a/f",
        "l": ["/tmp/x/a", 3],
        "n": None,
    }
    with pytest.raises(KeyError):
        run_eval.substitute_paths("${b}", paths)


def test_server_start_failure_scores_run_failed_and_sweep_continues(fake_env, monkeypatch):
    monkeypatch.setattr(
        config,
        "MCP_SERVER_KINDS",
        {"fake": {"command": "no-such-binary-xyz", "args": ["{path}"], "seed": "dir"}},
    )
    monkeypatch.setattr(run_eval, "chat", ScriptedChat())

    passed, total, note = run_eval.run_case_repeated("m", single_call_case(), {}, runs=2)

    assert (passed, total) == (0, 2)
    assert note.startswith("mcp server error:")

    # A following classic case in the same process still runs and passes.
    registry, cases = run_eval.load_suite(config.CASES_PATH)
    classic = next(c for c in cases if c["type"] == "no_call")
    monkeypatch.setattr(run_eval, "chat", ScriptedChat(text_msg("no tool needed")))
    passed, total, _ = run_eval.run_case_repeated("m", classic, registry, runs=1)
    assert (passed, total) == (1, 1)


def test_server_crash_mid_chain_scores_failed_with_note(fake_env, monkeypatch):
    monkeypatch.setattr(run_eval, "chat", ScriptedChat(call_msg("docs__boom", {})))
    case = chain_case([{"name": "docs__boom", "args": {}}])

    passed, total, note = run_eval.run_case_repeated("m", case, {}, runs=1)

    assert (passed, total) == (0, 1)
    assert note.startswith("mcp server error:")


def test_unknown_kind_is_mcp_server_error(fake_env):
    case = single_call_case(mcp_servers={"docs": {"kind": "nope", "fixture": "docs"}})
    with pytest.raises(McpServerError, match="nope"):
        run_eval.run_case("m", case, {})


def _write_suite(tmp_path, case):
    path = tmp_path / "alt_suite.json"
    path.write_text(json.dumps({"cases": [case]}))
    return path


def _run_main(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["run_eval.py", *argv])
    monkeypatch.setattr(config, "API_KEY", "test-key")
    monkeypatch.setattr(config, "RESULTS_CSV_PATH", str(tmp_path / "results.csv"))
    monkeypatch.setattr(config, "RESULTS_MD_PATH", str(tmp_path / "results.md"))
    run_eval.main()
    return (tmp_path / "results.csv"), (tmp_path / "results.md")


def test_suite_flag_loads_alternate_file(fake_env, tmp_path, monkeypatch):
    suite_path = _write_suite(tmp_path, single_call_case())
    monkeypatch.setattr(
        run_eval,
        "chat",
        lambda model, messages, tools: call_msg(
            "docs__echo", {"text": messages[0]["content"].split()[-1]}
        ),
    )

    csv_path, md_path = _run_main(
        monkeypatch, tmp_path, ["--suite", str(suite_path), "--models", "m", "--runs", "1"]
    )

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert reader.fieldnames == run_eval.CSV_COLUMNS
    assert [r["case_id"] for r in rows] == ["selection_fake_read"]
    assert f"- **Suite:** `{suite_path}`" in md_path.read_text()


def test_results_md_names_default_suite(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DELAY_BETWEEN_CALLS_SECONDS", 0)
    monkeypatch.setattr(run_eval, "chat", ScriptedChat())

    _, md_path = _run_main(
        monkeypatch,
        tmp_path,
        ["--models", "m", "--runs", "1", "--cases", "selection_calendar_lookup"],
    )

    assert "- **Suite:** `cases.json`" in md_path.read_text()
