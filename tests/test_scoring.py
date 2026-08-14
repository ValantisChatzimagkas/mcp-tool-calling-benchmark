"""Unit tests for the scoring logic. No API calls — these run offline in under a second.

The scoring functions decide every pass/fail in results.csv. Their dangerous failure
mode is silent leniency (an expectation that stops checking anything still prints
PASS), so most of these tests assert that a bad call is *rejected*.
"""

from client import parse_tool_calls
from scoring import (
    check_args,
    collapse_consecutive,
    score_chain,
    score_no_call,
    score_refusal,
    score_single_call,
)

# --- check_args -------------------------------------------------------------


def test_accepts_exact_match():
    ok, _ = check_args(
        {"person": "maria", "date": "2026-03-12"},
        {"args": {"person": "maria", "date": "2026-03-12"}},
    )
    assert ok


def test_ignores_case_and_surrounding_whitespace():
    ok, _ = check_args({"assignee": "  Elena "}, {"args": {"assignee": "elena"}})
    assert ok


def test_rejects_missing_arg():
    ok, note = check_args({"title": "x"}, {"args": {"title": "x", "assignee": "elena"}})
    assert not ok
    assert "missing required arg: assignee" in note


def test_rejects_empty_arg():
    ok, note = check_args({"assignee": ""}, {"args": {"assignee": "elena"}})
    assert not ok
    assert "missing required arg" in note


def test_rejects_wrong_value():
    ok, note = check_args({"date": "2026-03-13"}, {"args": {"date": "2026-03-12"}})
    assert not ok
    assert "wrong value for date" in note


def test_loose_arg_accepts_any_nonempty_value():
    spec = {"args": {"query": "audit log retention policy"}, "loose_args": ["query"]}
    ok, _ = check_args({"query": "how long do we keep audit logs"}, spec)
    assert ok


def test_loose_arg_still_requires_presence():
    spec = {"args": {"query": "anything"}, "loose_args": ["query"]}
    ok, note = check_args({}, spec)
    assert not ok
    assert "missing required arg: query" in note


def test_rejects_forbidden_arg():
    spec = {"args": {"title": "x"}, "forbidden_args": ["due_date"]}
    ok, note = check_args({"title": "x", "due_date": "2026-04-01"}, spec)
    assert not ok
    assert "should have omitted: due_date" in note


def test_forbidden_arg_absent_or_empty_is_fine():
    spec = {"args": {"title": "x"}, "forbidden_args": ["due_date"]}
    assert check_args({"title": "x"}, spec)[0]
    assert check_args({"title": "x", "due_date": None}, spec)[0]


def test_spec_with_no_args_key_checks_nothing():
    """Documents the sharp edge: an expectation with a typo'd `args` key passes
    everything. tests/test_suite_integrity.py is what stops that reaching cases.json."""
    ok, _ = check_args({"anything": "at all"}, {"name": "create_task", "arg": {"x": 1}})
    assert ok


# --- score_single_call ------------------------------------------------------


def test_single_call_happy_path():
    calls = [("calendar_lookup", {"person": "maria", "date": "2026-03-12"})]
    ok, _ = score_single_call(
        calls, {"name": "calendar_lookup", "args": {"person": "maria", "date": "2026-03-12"}}
    )
    assert ok


def test_single_call_rejects_no_call():
    ok, note = score_single_call([], {"name": "calendar_lookup", "args": {}})
    assert not ok
    assert note == "no tool call made"


def test_single_call_rejects_wrong_tool():
    calls = [("search_documents", {"query": "x"})]
    ok, note = score_single_call(calls, {"name": "calendar_lookup", "args": {}})
    assert not ok
    assert "wrong tool called: search_documents instead of calendar_lookup" in note


def test_single_call_rejects_extra_call():
    calls = [
        ("calendar_lookup", {"person": "maria", "date": "2026-03-12"}),
        ("send_message", {"recipient": "maria", "body": "hi"}),
    ]
    ok, note = score_single_call(calls, {"name": "calendar_lookup", "args": {"person": "maria"}})
    assert not ok
    assert "also called: send_message" in note


# --- score_no_call ----------------------------------------------------------


def test_no_call_happy_path():
    ok, _ = score_no_call(
        [], {"content": "A task queue distributes work; a broker routes messages."}
    )
    assert ok


def test_no_call_rejects_a_tool_call():
    ok, note = score_no_call([("create_task", {})], {"content": ""})
    assert not ok
    assert "create_task" in note


def test_no_call_rejects_empty_answer():
    ok, note = score_no_call([], {"content": "   "})
    assert not ok
    assert "no text answer" in note


def test_no_call_rejects_missing_content_key():
    ok, _ = score_no_call([], {})
    assert not ok


# --- score_refusal ----------------------------------------------------------

MISSING_ASSIGNEE = {"must_mention": ["assign", "who"], "require_question": True}


def test_refusal_happy_path():
    message = {"content": "Who should I assign that task to?"}
    ok, _ = score_refusal([], message, MISSING_ASSIGNEE)
    assert ok


def test_refusal_rejects_a_tool_call():
    ok, note = score_refusal([("create_task", {})], {"content": "Who?"}, MISSING_ASSIGNEE)
    assert not ok
    assert "create_task" in note


def test_refusal_rejects_an_assertion_with_no_question():
    """'I can't do that' is not a clarifying question — the old scorer passed it."""
    message = {"content": "I'm unable to create that task."}
    ok, note = score_refusal([], message, MISSING_ASSIGNEE)
    assert not ok
    assert "did not ask a question" in note


def test_refusal_rejects_a_question_about_the_wrong_thing():
    """Changing the subject shouldn't count as asking about the missing value."""
    message = {"content": "Would you like me to search the docs instead?"}
    ok, note = score_refusal([], message, MISSING_ASSIGNEE)
    assert not ok
    assert "never named the missing info" in note


def test_refusal_accepts_any_of_the_mentioned_terms():
    ok, _ = score_refusal([], {"content": "Who is this for?"}, MISSING_ASSIGNEE)
    assert ok
    ok, _ = score_refusal([], {"content": "Which user should I assign it to?"}, MISSING_ASSIGNEE)
    assert ok


def test_refusal_mention_matching_is_case_insensitive():
    ok, _ = score_refusal([], {"content": "WHO should own this?"}, MISSING_ASSIGNEE)
    assert ok


def test_refusal_rejects_empty_answer():
    ok, note = score_refusal([], {"content": "  "}, MISSING_ASSIGNEE)
    assert not ok
    assert "no text answer" in note


def test_refusal_question_requirement_can_be_waived():
    spec = {"must_mention": ["assign"], "require_question": False}
    ok, _ = score_refusal([], {"content": "I need an assignee first."}, spec)
    assert ok


# --- collapse_consecutive ---------------------------------------------------


def test_collapse_drops_consecutive_repeat_keeping_first():
    calls = [("search", {"q": "first"}), ("search", {"q": "second"}), ("create", {})]
    assert collapse_consecutive(calls) == [("search", {"q": "first"}), ("create", {})]


def test_collapse_keeps_non_consecutive_repeat():
    calls = [("search", {}), ("create", {}), ("search", {})]
    assert [n for n, _ in collapse_consecutive(calls)] == ["search", "create", "search"]


# --- score_chain ------------------------------------------------------------


def test_chain_accepts_bare_name_sequence():
    observed = [("search_documents", {"query": "x"}), ("create_task", {"title": "y"})]
    ok, _ = score_chain(observed, ["search_documents", "create_task"])
    assert ok


def test_chain_rejects_wrong_order():
    observed = [("create_task", {}), ("search_documents", {})]
    ok, note = score_chain(observed, ["search_documents", "create_task"])
    assert not ok
    assert "call order" in note


def test_chain_rejects_no_calls():
    ok, note = score_chain([], ["search_documents"])
    assert not ok
    assert note == "no tool calls made"


def test_chain_rejects_missing_step():
    observed = [("search_documents", {"query": "x"})]
    ok, note = score_chain(observed, ["search_documents", "create_task"])
    assert not ok
    assert "call order" in note


def test_chain_checks_args_per_step():
    """The llama-3.3 failure: right call order, but step 2 passes a placeholder
    instead of the id returned by step 1. Name-only scoring would call this a pass."""
    observed = [
        ("search_documents", {"query": "onboarding checklist"}),
        ("get_document", {"document_id": "doc_id_from_search_results"}),
    ]
    sequence = [
        {
            "name": "search_documents",
            "args": {"query": "onboarding checklist"},
            "loose_args": ["query"],
        },
        {"name": "get_document", "args": {"document_id": "doc_114"}},
    ]
    ok, note = score_chain(observed, sequence)
    assert not ok
    assert "get_document: wrong value for document_id" in note


def test_chain_accepts_correct_args_per_step():
    observed = [
        ("search_documents", {"query": "anything reasonable"}),
        ("get_document", {"document_id": "doc_114"}),
    ]
    sequence = [
        {
            "name": "search_documents",
            "args": {"query": "onboarding checklist"},
            "loose_args": ["query"],
        },
        {"name": "get_document", "args": {"document_id": "doc_114"}},
    ]
    ok, _ = score_chain(observed, sequence)
    assert ok


def test_chain_mixes_bare_names_and_arg_specs():
    observed = [("search_documents", {"query": "x"}), ("get_document", {"document_id": "doc_114"})]
    sequence = ["search_documents", {"name": "get_document", "args": {"document_id": "doc_114"}}]
    ok, _ = score_chain(observed, sequence)
    assert ok


# --- parse_tool_calls -------------------------------------------------------


def test_parse_handles_no_tool_calls():
    assert parse_tool_calls({"content": "hello"}) == []
    assert parse_tool_calls({"content": "hello", "tool_calls": None}) == []


def test_parse_reads_name_and_json_arguments():
    message = {
        "tool_calls": [
            {"id": "c1", "function": {"name": "create_task", "arguments": '{"title": "x"}'}}
        ]
    }
    assert parse_tool_calls(message) == [("create_task", {"title": "x"})]


def test_parse_survives_malformed_arguments():
    """A model emitting broken JSON must score as a fail, not crash the sweep."""
    message = {
        "tool_calls": [{"id": "c1", "function": {"name": "create_task", "arguments": "{not json"}}]
    }
    name, args = parse_tool_calls(message)[0]
    assert name == "create_task"
    assert "__unparseable__" in args


def test_parse_survives_non_object_arguments():
    message = {
        "tool_calls": [
            {"id": "c1", "function": {"name": "create_task", "arguments": '"just a string"'}}
        ]
    }
    _name, args = parse_tool_calls(message)[0]
    assert "__not_an_object__" in args


def test_missing_arguments_key_becomes_empty_dict():
    message = {"tool_calls": [{"id": "c1", "function": {"name": "list_all"}}]}
    assert parse_tool_calls(message) == [("list_all", {})]
