"""Validates cases.json itself.

A malformed case doesn't crash the sweep — it quietly stops testing anything and still
prints PASS. These checks make that a red test instead of a wrong results table.

They also pin down the suite's conventions (category names, the `<category>_<specific>`
id scheme, grouping) so it can't drift back into ad-hoc naming as cases get added.
"""

CATEGORIES = {"selection", "arguments", "chaining", "restraint", "refusal"}


def test_case_ids_are_unique(suite):
    ids = [c["id"] for c in suite["cases"]]
    assert len(ids) == len(set(ids))


def test_every_case_references_defined_tools(suite):
    tools = set(suite["tools"])
    for case in suite["cases"]:
        unknown = [t for t in case["available_tools"] if t not in tools]
        assert not unknown, f"{case['id']} references undefined tool(s): {unknown}"


def test_every_single_call_case_constrains_at_least_one_arg(suite):
    """Catches an `args` -> `arg` typo that would make a case pass unconditionally."""
    for case in suite["cases"]:
        if case["type"] != "single_call":
            continue
        expected = case["expected_tool_call"]
        assert expected.get("args"), f"{case['id']}: expectation checks no args"


def test_every_chain_step_is_available_and_has_a_canned_result(suite):
    for case in suite["cases"]:
        if case["type"] != "multi_call_chain":
            continue
        for step in case["expected_tool_call"]["sequence"]:
            name = step if isinstance(step, str) else step["name"]
            assert name in case["available_tools"], f"{case['id']}: {name} not offered"
            assert name in case.get("tool_results", {}), (
                f"{case['id']}: no canned result for {name}"
            )


def test_every_case_type_is_known(suite):
    """An unrecognised type scores as a failure for every model — catch it offline."""
    known = {"single_call", "multi_call_chain", "no_call", "should_refuse"}
    for case in suite["cases"]:
        assert case["type"] in known, f"{case['id']}: unknown type {case['type']!r}"


def test_every_case_category_is_known(suite):
    for case in suite["cases"]:
        assert case["category"] in CATEGORIES, (
            f"{case['id']}: unknown category {case['category']!r}"
        )


def test_case_ids_are_prefixed_with_their_category(suite):
    """The naming scheme is `<category>_<specific>`. Enforced here so it can't drift."""
    for case in suite["cases"]:
        assert case["id"].startswith(f"{case['category']}_"), (
            f"{case['id']} is in category {case['category']!r} but isn't named for it"
        )


def test_cases_are_grouped_by_category(suite):
    """Cases are stored grouped, so results.md reads in a sensible order."""
    seen = []
    for case in suite["cases"]:
        if case["category"] not in seen:
            seen.append(case["category"])
    assert len(seen) == len(set(seen)), f"category blocks are interleaved: {seen}"


def test_every_category_has_at_least_one_case(suite):
    """A category that exists only in docs is a gap in the suite, not a category."""
    present = {case["category"] for case in suite["cases"]}
    assert present == CATEGORIES, f"unused categories: {sorted(CATEGORIES - present)}"
