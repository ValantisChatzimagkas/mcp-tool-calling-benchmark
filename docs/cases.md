# Case catalogue and adding cases

Reference for `cases.json`. Back to the [README](../README.md).

## Cases

`cases.json` has two top-level keys: `tools` (a registry of OpenAI-format
function schemas, keyed by name) and `cases` (which reference tools by name).

```json
{
  "id": "selection_calendar_lookup",
  "type": "single_call",
  "category": "selection",
  "prompt": "What's on maria's calendar on 2026-03-12?",
  "available_tools": ["calendar_lookup", "search_documents", "create_task"],
  "expected_tool_call": {
    "name": "calendar_lookup",
    "args": { "person": "maria", "date": "2026-03-12" }
  }
}
```

Every case carries two independent labels, and they answer different questions.

**`type` — how the case is scored.** This drives the code path in `run_eval.py`.

| type | what passes |
|---|---|
| `single_call` | exactly one call, right tool, every expected arg present and matching |
| `multi_call_chain` | tools called in the expected order, with the expected args, across turns |
| `no_call` | no tool called, and a non-empty text answer came back |
| `should_refuse` | no tool called, *and* the model asked about the specific value the prompt left out |

**`category` — what capability the case probes.** This drives reporting, and gives the
case its id: ids are `<category>_<specific>`, enforced by
`tests/test_suite_integrity.py` so the naming can't drift as cases get added.

| category | probes | cases |
|---|---|---|
| `selection` | picking the right tool, including over a near-miss distractor | 7 |
| `arguments` | filling args correctly — ISO dates, enum values, omitting optional ones | 4 |
| `chaining` | multi-step work that depends on reading tool output | 5 |
| `restraint` | *not* calling a tool when the answer is plain text | 2 |
| `refusal` | asking instead of fabricating a missing required value | 3 |

Cases are stored grouped by category, so `results.md` reads in a sensible order.

The two labels overlap today — `chaining` is exactly `multi_call_chain`, `refusal` is
exactly `should_refuse`, and `category` only genuinely subdivides `single_call` into
`selection` vs `arguments`. They are kept separate because the overlap is a fact about
the current 21 cases, not about the design: a chaining case that should refuse
mid-chain, or a refusal case where a tool is available and args are partial, both break
the correlation without needing a schema change.

Optional per-case keys:

- `loose_args` — arg names checked for presence only, not exact value. For genuinely
  open-ended values like a search `query`, where any reasonable phrasing is correct.
- `forbidden_args` — args that must *not* be sent, e.g. catching a model that invents
  a `due_date` nobody gave it.
- `tool_results` — `multi_call_chain` only: canned JSON fed back per tool name, so the
  model has something to act on for the next step.
- `expected_refusal` — `should_refuse` only, and required for it:

```json
"expected_refusal": {
  "must_mention": ["text", "content", "what", "say", "wording", "message"],
  "require_question": true
}
```

`must_mention` is **any-of**, matched case-insensitively as plain substrings. A refusal
case knows which value the prompt withheld, so the answer has to name it — otherwise
"I can't help with that" and a cheerful change of subject score the same as a real
clarifying question. List the plausible ways of naming the gap, not one exact phrasing.

**Calibrate these against real responses, not from imagination.** The first draft of
this list omitted "text", and a model that correctly answered *"I'll need the exact text
you'd like me to send"* was scored as a failure. Run the case, read what the model
actually says, then widen the list. A too-narrow list produces false failures exactly
as misleading as the false passes this key exists to prevent.

### Chain expectations

A `sequence` entry is either a bare tool name (order only) or an object that also pins
down arguments. The second form is how a chain tests whether the model actually *read*
the previous tool's output instead of guessing:

```json
"sequence": [
  { "name": "search_documents", "args": { "query": "onboarding checklist" }, "loose_args": ["query"] },
  { "name": "get_document", "args": { "document_id": "doc_114" } }
]
```

`doc_114` appears only in the canned `search_documents` result, so a model that skips
ahead or invents an id fails — even though it called both tools in the right order.

**This is stricter than how established benchmarks grade chains, deliberately.**
τ-bench doesn't score the tool-call sequence at all — it checks whether the final task
state ended up correct, regardless of path. BFCL's sequence check only requires the
calls to be "valid and minimally sufficient," not one exact sequence. This suite
requires exact name-and-argument matches at every step because there's no real
environment behind it to check an end state against — `tool_results` are canned JSON,
not a live system a chain could reach the same correct outcome through a different
route. A model that finds a legitimately different but equally correct path would fail
here; that's a real cost of the simulated-environment design, not an oversight.

Checked whether adopting BFCL's looser standard would actually change anything for
*these* 5 cases, rather than assuming it would: it mostly wouldn't. Most of this
suite's chains have genuine data dependency, not an order I imposed arbitrarily —
`chaining_id_from_output` needs the id `search_documents` returns before `get_document`
can be called at all; `chaining_arg_from_output` needs the organizer's name from the
calendar lookup before it can message them. Loosening the order check couldn't help
those cases pass differently; the order is forced by the task, not by the scorer. Only
`chaining_search_then_task`'s task-creation step doesn't strictly need the prior
output. Full tau-bench-style end-state checking isn't cheaply portable either — it
needs an actual stateful backend to check a final state against, which is the larger
"build a real MCP server with real state" investment discussed and deferred, not a
scoring tweak.


## Adding to the suite

- **New case** — add an object to `cases`, in its category's block, named
  `<category>_<specific>`. If it needs a new tool, add the schema to `tools` first. No
  Python changes. `uv run pytest` will tell you if the id, category, or grouping is off.
- **New category** — add it to `CATEGORIES` in `tests/test_suite_integrity.py` and give
  it at least one case. Reporting picks it up automatically from the data.
- **New model** — append to `MODELS` in `config.py`, or pass `--models` at runtime.

Coverage is uneven by design-so-far, not by intent: `arguments` and `restraint` have
only two cases each. Those are the obvious places to add.

