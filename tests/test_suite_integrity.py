"""Validates cases.json itself.

A malformed case doesn't crash the sweep — it quietly stops testing anything and still
prints PASS. These checks make that a red test instead of a wrong results table.

They also pin down the suite's conventions (category names, the `<category>_<specific>`
id scheme, grouping) so it can't drift back into ad-hoc naming as cases get added.
"""

from run_eval import CSV_COLUMNS, _fraction, _tally

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


def test_every_should_refuse_case_declares_what_is_missing(suite):
    """Without a non-empty `must_mention`, a refusal case degrades to "didn't call a
    tool" — which any off-topic answer satisfies."""
    for case in suite["cases"]:
        if case["type"] != "should_refuse":
            continue
        spec = case.get("expected_refusal")
        assert spec, f"{case['id']}: should_refuse case has no expected_refusal"
        assert spec.get("must_mention"), f"{case['id']}: expected_refusal names nothing"


def test_every_category_has_at_least_one_case(suite):
    """A category that exists only in docs is a gap in the suite, not a category."""
    present = {case["category"] for case in suite["cases"]}
    assert present == CATEGORIES, f"unused categories: {sorted(CATEGORIES - present)}"


# --- the results rollup -----------------------------------------------------
# Not about cases.json, but the same failure mode: a rollup that quietly counts the
# wrong thing still prints a plausible-looking table.


def _row(passed, total, category="selection"):
    return {"category": category, "runs_passed": passed, "runs_total": total}


def test_tally_sums_runs_not_cases():
    """Two cases at 2/3 is 4/6, not 0/2 or 2/2."""
    assert _tally([_row(2, 3), _row(2, 3)]) == (4, 6)


def test_tally_of_no_rows_is_zero():
    assert _tally([]) == (0, 0)


def test_tally_counts_a_partially_passing_case_partially():
    assert _tally([_row(3, 3), _row(1, 3)]) == (4, 6)


def test_fraction_reports_the_run_level_rate():
    assert _fraction(*_tally([_row(3, 3), _row(1, 3)])) == "4/6 (67%)"


def test_fraction_of_nothing_is_a_dash():
    assert _fraction(0, 0) == "—"


def test_csv_columns_carry_both_run_counts():
    """Dropping either one turns k/n back into an unreadable single number."""
    assert "runs_passed" in CSV_COLUMNS and "runs_total" in CSV_COLUMNS
    assert "result" not in CSV_COLUMNS
