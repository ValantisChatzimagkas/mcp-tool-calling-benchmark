# MCP Tool-Calling Benchmark

Measures how reliably a model handles MCP-style tool calling: picking the right tool,
filling in the right arguments, chaining calls that depend on each other, and — just
as important — *not* calling a tool when it shouldn't.

Works against anything exposing an OpenAI-compatible `/chat/completions` endpoint.
Groq for fast iteration, llama.cpp / Ollama / vLLM for self-hosted runs. Same script,
one config value changes.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
uv sync                    # creates .venv and installs from uv.lock
cp .env.example .env       # then put your Groq key in it
uv run run_eval.py
```

`.env` is gitignored. Never commit a key.

```bash
uv run run_eval.py --models openai/gpt-oss-20b     # one model
uv run run_eval.py --cases cal_lookup_basic        # one case, all models
```

## Output

Progress prints live as it runs:

```
=== llama-3.3-70b-versatile ===
  [PASS] cal_lookup_basic         ok
  [FAIL] missing_required_info    called tool(s) when none expected: create_task
  [FAIL] chain_three_steps        get_document: wrong value for document_id: 'doc_id_from_search_results' != 'doc_114'
```

Two files are written at the end (both gitignored — they are regenerated every run):

- `results.csv` — one row per (model, case): `model, case_id, category, case_type, result, notes`
- `results.md` — run metadata, a pass-rate table broken down by category, the full
  result matrix grouped by category, and every failure note

The per-category breakdown is the point: one aggregate number tells you a model scored
14/17, which is not actionable. The breakdown tells you it went 5/5 on selection and
1/3 on refusal, which is.

## Layout

```
config.py     endpoint, key, model list, tuning — the only place these live
client.py     HTTP and response parsing — the only module that touches the network
scoring.py    pure pass/fail logic — no I/O, no config, fully unit-tested
run_eval.py   loads the suite, runs cases, writes results, CLI
cases.json    the benchmark suite (data, not code)
tests/        pytest — scoring logic, plus checks on cases.json itself
```

Two words that look alike here mean different things: a **case** is a benchmark case
in `cases.json`, and a **test** is pytest. `results.csv` uses `case_id` / `case_type`
for that reason.

## Configuration

Everything tunable lives in `config.py`; nothing else reads an env var or hardcodes a
URL. That is what lets the same script hit a local server unchanged.

| setting | purpose |
|---|---|
| `API_BASE_URL` | endpoint, overridable from `.env` |
| `MODELS` | model ids to sweep |
| `DELAY_BETWEEN_CALLS_SECONDS` | rate-limit spacing (see below) |
| `MAX_CHAIN_STEPS` | turn budget for a `multi_call_chain` case |

### Self-hosted endpoints

Only `.env` changes:

```bash
API_BASE_URL=http://localhost:8080/v1     # llama.cpp server
API_BASE_URL=http://localhost:11434/v1    # Ollama
```

Then set `MODELS` to the names your server serves. The API-key check is skipped
automatically for `localhost`.

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
| `selection` | picking the right tool, including over a near-miss distractor | 5 |
| `arguments` | filling args correctly — ISO dates, omitting optional ones | 2 |
| `chaining` | multi-step work that depends on reading tool output | 5 |
| `restraint` | *not* calling a tool when the answer is plain text | 2 |
| `refusal` | asking instead of fabricating a missing required value | 3 |

Cases are stored grouped by category, so `results.md` reads in a sensible order.

The two labels overlap today — `chaining` is exactly `multi_call_chain`, `refusal` is
exactly `should_refuse`, and `category` only genuinely subdivides `single_call` into
`selection` vs `arguments`. They are kept separate because the overlap is a fact about
the current 17 cases, not about the design: a chaining case that should refuse
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

## Where models actually diverge

`refusal` and `chaining` carry the signal. In the 3-model run this was built against,
every model went 5/5 on `selection` and 2/2 on `arguments`; the entire spread came from
refusal cases and from one model emitting literal placeholder strings
(`doc_id_from_search_results`) instead of reading tool output.

That is also the argument for the category split: an aggregate "14/17" hides this
completely.

## Adding to the suite

- **New case** — add an object to `cases`, in its category's block, named
  `<category>_<specific>`. If it needs a new tool, add the schema to `tools` first. No
  Python changes. `uv run pytest` will tell you if the id, category, or grouping is off.
- **New category** — add it to `CATEGORIES` in `tests/test_suite_integrity.py` and give
  it at least one case. Reporting picks it up automatically from the data.
- **New model** — append to `MODELS` in `config.py`, or pass `--models` at runtime.

Coverage is uneven by design-so-far, not by intent: `arguments` and `restraint` have
only two cases each. Those are the obvious places to add.

## How scoring works

Argument comparison is exact-match after lowercasing and stripping whitespace. No
fuzzy or semantic matching. This is deliberate: every failure note is then unambiguous
and cheap to verify by hand. The tradeoff is that a semantically-correct paraphrase can
fail — when a value is legitimately open-ended, add it to that case's `loose_args`
rather than loosening the comparison for everything.

`multi_call_chain` collapses consecutive repeat calls to the same tool before
comparing, so retrying one tool doesn't fail the case, but slipping in an extra
*different* tool does.

## Development

```bash
uv run pytest          # scoring tests, offline, <1s
uv run ruff check .
uv run ruff format .
```

The tests cover the scoring functions, whose dangerous failure mode is silent
leniency: an expectation that quietly stops checking anything still prints `PASS`, and
every number in `results.csv` would be wrong with no visible symptom. Some tests
validate `cases.json` itself — unique ids, tools that exist, chain steps with
canned results, and every `single_call` case constraining at least one argument (which
catches an `"args"` → `"arg"` typo that would otherwise make a case pass
unconditionally).

## Rate limits

Groq's free tier is roughly 30 requests/minute. `DELAY_BETWEEN_CALLS_SECONDS` spaces
calls out, and HTTP 429 is retried with backoff, honouring `Retry-After` when the
server sends it. A full 3-model sweep takes a few minutes. Set the delay to `0` for a
local server.

A failed API call is scored as a fail with the error in the notes column — it never
aborts the sweep, so one flaky model doesn't cost you the whole run.

## Known limitations

- Refusal content is checked by substring keywords, not meaning. A model could satisfy
  `must_mention` while asking something unhelpful, and an unusual-but-correct phrasing
  can fail until the list is widened. This is the deliberate v1 tradeoff — it stays
  deterministic and offline-testable. An LLM judge would read intent properly, at the
  cost of nondeterminism and an API call per case.
- Adding the refusal-content check changed no verdict on the models tested: every
  "didn't call a tool" was already a genuine clarifying question. It guards a real hole,
  but it has not yet caught anything.
- The `selection` and `arguments` categories are saturated — every model tested passes
  all 7. They cost API calls and contribute nothing to ranking.
- The self-hosted path is one config value and is not covered by tests; it has not been
  run against a live local server.
