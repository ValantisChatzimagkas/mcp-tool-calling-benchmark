"""Turning a model's response into pass/fail.

Pure functions — no network, no config, no I/O. Every number in results.csv comes from
here, so this is the module that must not be wrong. See tests/test_scoring.py.

Every scorer returns (passed, note); the note is what lands in the CSV's notes column.
"""


def normalize(value):
    """Comparison policy for argument values: case- and whitespace-insensitive, nothing more."""
    return str(value).strip().lower()


def check_args(args, spec, label="") -> tuple[bool, str]:
    """Validate one call's arguments against an expectation.

    A spec with no `args` key checks nothing and passes — tests/test_scoring.py guards
    against that happening by accident via a typo in cases.json.
    """
    prefix = f"{label}: " if label else ""
    loose = set(spec.get("loose_args", []))

    for key, want in spec.get("args", {}).items():
        if key not in args or args[key] in (None, ""):
            return False, f"{prefix}missing required arg: {key}"
        if key in loose:
            continue
        if normalize(args[key]) != normalize(want):
            return False, f"{prefix}wrong value for {key}: {args[key]!r} != {want!r}"

    for key in spec.get("forbidden_args", []):
        if args.get(key) not in (None, ""):
            return False, f"{prefix}passed arg it should have omitted: {key}={args[key]!r}"

    return True, "ok"


def score_single_call(calls, expected) -> tuple[bool, str]:
    """Exactly one call, to the expected tool, with the expected args."""
    if not calls:
        return False, "no tool call made"

    name, args = calls[0]
    if name != expected["name"]:
        return False, f"wrong tool called: {name} instead of {expected['name']}"
    if len(calls) > 1:
        extra = ", ".join(c[0] for c in calls[1:])
        return False, f"expected 1 call, also called: {extra}"

    return check_args(args, expected)


def score_no_call(calls, message) -> tuple[bool, str]:
    """No tool called, and the model said something.

    Used for both `no_call` and `should_refuse` — this does not check *what* the model
    said, so it can't tell a clarifying question from an unrelated answer.
    """
    if calls:
        called = ", ".join(c[0] for c in calls)
        return False, f"called tool(s) when none expected: {called}"
    if not (message.get("content") or "").strip():
        return False, "no tool call and no text answer"
    return True, "ok"


def score_refusal(calls, message, spec) -> tuple[bool, str]:
    """No tool called, *and* the model actually asked about the missing information.

    Stricter than score_no_call: a refusal case knows which value the prompt left out,
    so the answer has to name it. Without this, "I can't help with that" and a cheerful
    change of subject both score the same as a real clarifying question.

    `must_mention` is any-of, matched case-insensitively as substrings, so a case lists
    the plausible ways of naming the gap ("assign", "who", "owner") rather than one
    exact phrasing. Deliberately not semantic matching — see README.
    """
    if calls:
        called = ", ".join(c[0] for c in calls)
        return False, f"called tool(s) when none expected: {called}"

    content = (message.get("content") or "").strip()
    if not content:
        return False, "no tool call and no text answer"

    if spec.get("require_question", True) and "?" not in content:
        return False, "did not ask a question, just asserted"

    wanted = spec.get("must_mention", [])
    lowered = content.lower()
    if wanted and not any(term.lower() in lowered for term in wanted):
        return False, f"never named the missing info (expected one of {wanted})"

    return True, "ok"


def collapse_consecutive(calls):
    """Drop a repeat call to the same tool as the previous one; keep the first."""
    out = []
    for name, args in calls:
        if not out or out[-1][0] != name:
            out.append((name, args))
    return out


def score_chain(observed, expected_sequence) -> tuple[bool, str]:
    """Expected call order, and per-step args where the sequence pins them down.

    A sequence entry is either a bare tool name (order only) or a dict with `name`
    plus the same arg keys `check_args` understands.
    """
    expected = [step if isinstance(step, dict) else {"name": step} for step in expected_sequence]
    want_names = [step["name"] for step in expected]

    got = collapse_consecutive(observed)
    got_names = [name for name, _ in got]

    if not got_names:
        return False, "no tool calls made"
    if got_names != want_names:
        return False, f"call order {got_names} != expected {want_names}"

    for (name, args), step in zip(got, expected, strict=True):
        ok, note = check_args(args, step, label=name)
        if not ok:
            return False, note

    return True, "ok"
