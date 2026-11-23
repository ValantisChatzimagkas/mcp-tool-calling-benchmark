# Case catalogue and adding cases

Reference for `cases.json`. Back to the [README](../README.md).

## Cases

`cases.json` has two top-level keys: `tools` (a registry of OpenAI-format
function schemas, keyed by name) and `cases`. A canned case references the registry by
name; a server-backed case declares `mcp_servers` instead and never touches it.

```json
{
  "id": "selection_search_vs_lookup",
  "type": "single_call",
  "category": "selection",
  "prompt": "Maria has a design review booked sometime this year but I don't know which day. Find it.",
  "available_tools": ["search_calendar", "calendar_lookup", "search_documents"],
  "expected_tool_call": {
    "name": "search_calendar",
    "args": { "person": "maria", "query": "design review" },
    "loose_args": ["query"]
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
| `selection` | picking the right tool, including over a near-miss distractor | 2 |
| `arguments` | filling args correctly — ISO dates, omitting optional ones | 2 |
| `chaining` | multi-step work that depends on reading tool output | 5 |
| `restraint` | *not* calling a tool when the answer is plain text | 2 |
| `refusal` | asking instead of fabricating a missing required value | 3 |
| `server_chaining` | multi-step work on real server output (query a database, act on the result) | 2 |
| `registry` | picking the one right tool out of a 22-tool prompt from two servers | 2 |
| `collision` | two servers expose the same tool name and only one is correct | 2 |

Cases are stored grouped by category, so `results.md` reads in a sensible order.

The two labels overlap today — `chaining` and `server_chaining` are `multi_call_chain`,
`refusal` is exactly `should_refuse`, `restraint` is `no_call`, and `registry` and
`collision` are `single_call` like `selection` and `arguments`. They are kept separate
because the overlap is a fact about the current 20 cases, not about the design: a
chaining case that should refuse mid-chain, or a refusal case where a tool is
available and args are partial, both break the correlation without needing a schema
change.

Optional per-case keys:

- `loose_args` — arg names checked for presence only, not exact value. For genuinely
  open-ended values like a search `query`, where any reasonable phrasing is correct.
- `forbidden_args` — args that must *not* be sent, e.g. catching a model that invents
  a `due_date` nobody gave it.
- `tool_results` — `multi_call_chain` only: canned JSON fed back per tool name, so the
  model has something to act on for the next step.
- `mcp_servers` — instead of `available_tools` (a case has exactly one; see
  [Server-backed cases](#server-backed-cases)).
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


## Server-backed cases

A case may declare real stdio MCP servers instead of hand-written tools. Six live in
`cases.json` (the `server_chaining`, `registry` and `collision` categories, below) and
three in `mcp_smoke.json` (run with `--suite mcp_smoke.json`), a smoke of the path whose
top level has no `tools` registry:

```json
"mcp_servers": {
  "notes": {"kind": "filesystem", "fixture": "notes", "tools": ["read_text_file", "write_file"]}
}
```

- The key (`notes`) is the server name and tool prefix: the model sees the server's
  real `tools/list` schemas, unmodified, as `notes__read_text_file` etc., so two servers
  of the same kind don't collide. Expected tool names carry the prefix.
- `kind` names an entry in `config.MCP_SERVER_KINDS` (pinned `filesystem` and `sqlite`
  today). `fixture` is a path under `fixtures/`: a directory for `filesystem`, a `.sql`
  script for `sqlite`. Each run copies/executes it into a fresh temp sandbox, so runs
  can't see each other's writes and fixtures are never mutated.
- `tools` (optional) is an allow-list of the server's tool names; absent means all. A
  name the server doesn't offer fails the run rather than silently shrinking the set.
  Use it to keep the schema payload small (Groq's tokens-per-minute limit).
- `${server}` in `prompt`, `expected_tool_call` and `expected_refusal` is replaced by
  that server's sandbox path (the directory for `filesystem`, the database file for
  `sqlite`) before scoring. An unknown name fails the run.
- `available_tools` and `mcp_servers` are mutually exclusive, and a server-backed case
  has no `tool_results` — the real servers produce them. Scoring is unchanged.
- A server that can't start, crashes, hangs or can't be confined fails that run with a
  note starting `mcp server error:`; the sweep continues. Infrastructure failures and
  model failures are only distinguishable by that prefix.

### The server-backed categories

These three exist because the saturated canned categories could not separate models.
Each is `cases.json` data only; no scoring code is specific to them.

- **`server_chaining`** (`multi_call_chain`, 2 cases) — a chain whose later steps depend
  on real server output: query the shop database, then write the result to a file.
  `server_chaining_top_customer_email` is built so that the obvious query is wrong
  (the single largest order belongs to a different customer than the highest *total*).
  The last step pins `content`, so the model can only pass by reading the real result.
  The prompt names the tables and columns, so the exact-sequence scorer does not
  penalise a `list_tables` first call.
- **`registry`** (`single_call`, 2 cases) — one filesystem and one sqlite server with no
  allow-list: 22 tools, ~4k tokens of schema, in one prompt. The correct tool is unique
  (`move_file`, `write_query`). Single-call on purpose: a chain would cost 2–3 requests
  of that size per run and exhaust qwen's token budget (see
  [rate limits](rate-limits.md)).
- **`collision`** (`single_call`, 2 cases) — two servers expose the same tool name and
  only one holds the answer: two sqlite servers (`crm`, `billing`), and two filesystem
  roots with a same-named `todo.txt` of different content. Each server's allow-list is the
  single tool in question, so an exploratory first call cannot fail the case for a reason
  unrelated to the collision.

**Ground-truth assumptions.** The collision cases test role → server-name mapping, not
path discovery: the sqlite collision is path-free, and the file collision names both
roots in the prompt (each sandbox path ends in its server name). They assume the model
should pick the server by role instead of probing both; calling both fails
`single_call` ("expected 1 call"). Registry cases assume a unique correct tool. Every
sqlite prompt supplies its table and column names.

### Groq results for the new categories

Not yet recorded: no Groq sweep of the 20-case suite has run. When one does, each of
`server_chaining`, `registry` and `collision` gets one entry here: either the failing
runs observed, or a saturation flag with the reason the category is kept. A category
left inconclusive by `api error:` / `mcp server error:` runs gets neither.

## Adding to the suite

- **New case** — add an object to `cases`, in its category's block, named
  `<category>_<specific>`. If it needs a new tool, add the schema to `tools` first. No
  Python changes. `uv run pytest` will tell you if the id, category, or grouping is off.
- **New server-backed case** — add it to `cases.json` (in its category's block) or to
  `mcp_smoke.json` or a new suite file, with `mcp_servers` and an existing `kind`; add
  seed data under `fixtures/` if needed. A new kind of server needs an entry in
  `config.MCP_SERVER_KINDS` with a pinned version. Keep the suite within the 15–20 case
  sweep budget (`test_suite_size_within_sweep_budget`).
- **New category** — add it to `CATEGORIES` in `tests/test_suite_integrity.py` and give
  it at least one case. Reporting picks it up automatically from the data.
- **New model** — append to `MODELS` in `config.py`, or pass `--models` at runtime.

## Removed cases

SOL-103 cut `selection` from 7 cases to 2 and `arguments` from 4 to 2. All seven passed
3/3 for all three models in the last full sweep (see [findings](findings.md)), so they
contributed no discriminating evidence. Each is listed with what it tested and which
kept case still covers it.

| removed case | what it tested | still covered by |
|---|---|---|
| `selection_calendar_lookup` | direct single-tool pick among 3 | `selection_search_vs_lookup` (near-miss distractor) |
| `selection_create_task` | direct `create_task` pick among unrelated tools | `selection_update_vs_create` |
| `selection_search_before_fetch` | `search_documents` over `get_document` when only a title is given | `selection_search_vs_lookup` |
| `selection_search_documents` | direct `search_documents` pick among unrelated tools | `selection_search_vs_lookup` |
| `selection_task_vs_reminder` | `create_task` over `create_reminder` / `update_task` | `selection_update_vs_create` |
| `arguments_enum_value` | filling an enum value (`in_progress`) | `arguments_omit_optional` (`forbidden_args`) |
| `arguments_verbatim_recipient` | string passthrough to `recipient` | `arguments_date_to_iso` (exact-value checking) |
