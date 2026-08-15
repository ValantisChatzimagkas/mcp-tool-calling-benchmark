# MCP Tool-Calling Benchmark

## Scope — read this before trusting a number in this repo

**What this tests:** OpenAI-format function-calling reliability, for tool schemas
shaped like the tools an MCP server would expose (a calendar lookup, a search, a
create-task call, and so on).

**What this does not test:**

- **The MCP protocol itself.** No JSON-RPC, no `tools/list` / `tools/call`, no running
  MCP server anywhere in this repo. Tool schemas are hand-authored dicts in
  `cases.json`, sent directly as OpenAI-format `tools` in the request body. A real MCP
  server's schemas are known to be messier than hand-written ones ([BFCL v4's own
  notes say so](https://gorilla.cs.berkeley.edu/leaderboard.html)), and multi-server
  tool-namespace collisions aren't modeled at all — this suite has one flat tool
  registry, never several servers' worth colliding. Both are plausible sources of
  failure this benchmark cannot see.
- **The actual self-hosted stack.** Every result so far ran against Groq's hosted
  inference. `API_BASE_URL` can point at llama.cpp/Ollama with one `.env` line (see
  below), but that path has not actually been exercised. Quantization and serving
  differences between Groq's hosting and whatever runs locally could shift tool-calling
  behavior in either direction — this is untested, not assumed fine.
- **A certification.** A model passing every case here is evidence it's a reasonable
  *candidate* for MCP-style tool use, not proof it will behave identically once wired
  into a real MCP-based agent. Treat results as a screening signal, not a verdict —
  see **Known limitations** at the bottom for the specific gaps in the current 21-case
  suite.

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
uv run run_eval.py --runs 1                        # one run per case, for a smoke test
```

## Repeated runs

Every (model, case) pair runs `RUNS_PER_CASE` times (3 by default, `--runs` to
override) and reports `k/n` rather than a single pass/fail. Tool calling is
nondeterministic even at `temperature=0`, and on one run per case a real capability gap
and a coin flip look identical. A case that scores 2/3 is telling you something a
`FAIL` would hide.

`results.md` shows a 95% Wilson score interval next to every `k/n` — `3/3 (44%–100%)`,
not `3/3 (100%)`. A perfect score on 3 runs is not proof of 100% reliability; the
interval says so explicitly instead of implying more certainty than 3 samples support.
Wilson, specifically, because it stays well-behaved at `p=0` or `p=1` (the naive normal
approximation doesn't) — which is exactly where most cells in this report sit.

The report also labels any category where every model scored a clean sweep as
*saturated, contributes no discriminating evidence* — a 100% column is not the same
claim as "this model is better here," and the report says which one it actually is.

## Output

Progress prints live as it runs, with the per-case run count in the left column:

```
=== llama-3.3-70b-versatile (3 run(s) per case) ===
  [3/3] selection_calendar_lookup ok
  [0/3] refusal_missing_body      called tool(s) when none expected: send_message
  [1/3] chaining_three_steps      get_document: wrong value for document_id: 'doc_id_from_search_results' != 'doc_114'
```

Two files are written at the end (both gitignored — they are regenerated every run):

- `results.csv` — one row per (model, case):
  `model, endpoint, run_timestamp, case_id, category, case_type, runs_passed,
  runs_total, notes`. Every row from one sweep carries the same `endpoint` and
  `run_timestamp` — the point is that if you save two CSVs from different sweeps
  (Groq today, a self-hosted server next week) and later merge or paste them together,
  the difference in conditions is in the data, not just in a header you might not have
  kept. The two counts are plain integers; `notes` holds the deduped notes from the
  failing runs.
- `results.md` — run metadata, a pass-rate table broken down by category, the full
  result matrix grouped by category (`passed/total` per cell), and every failure note

Every rate — per category and overall — is passed runs over total runs, so a case that
passes 2 of 3 counts as two thirds of a case instead of rounding to a pass or a fail.

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
| `RUNS_PER_CASE` | runs per (model, case); `--runs` overrides it per invocation |
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
| `selection` | picking the right tool, including over a near-miss distractor | 7 |
| `arguments` | filling args correctly — ISO dates, enum values, omitting optional ones | 4 |
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

## Where models actually diverge

`refusal` and `chaining` carry the signal — confirmed by 3 replicated runs per case,
not a single sample. In the last full sweep (3 models × 21 cases × 3 runs, 189
case-runs total), `selection` and `arguments` were saturated at 100% for every
model — 33/33 and 12/12 respectively, zero variation anywhere in either category
across all three runs. `restraint` was equally saturated at 18/18.

Every failing cell in that sweep landed at exactly 0/3 or 3/3 — no cell split its 3
runs. That matters: it means the observed differences are stable failure modes at
`temperature=0`, not sampling noise that a rerun would shuffle. Concretely: one model
went 0/9 on `refusal` and never once asked a clarifying question; another fabricated
identifiers instead of reading tool output on 2 of 5 chaining cases (`0/3` on each,
every run); the remaining two models split 3/9 vs 6/9 on refusal, consistently, run
over run.

**Don't read "zero split cells" as proof this suite has no run-to-run variance.**
LiveMCPBench — a benchmark in the same space — found 23 repeated evaluations of one
identical setup swing 57.9%–76.8%, a 19-point spread wide enough to reorder a
leaderboard ([validity audit, arXiv 2607.02577](https://arxiv.org/html/2607.02577v1)).
Our 3-run replication not splitting could mean these 21 cases are genuinely simpler and
lower-variance than that benchmark's tasks — plausible, since ours are single- and
few-step decisions, not long multi-turn agentic runs — or it could mean 3 runs isn't
enough to catch a rarer variance mode. Both are live possibilities; 3-for-3 is
suggestive, not a proof of stability.

That is also the argument for the category split: an aggregate "45/63" hides which of
those is true, and a bare `k/n` without the replication would leave "is this real or
noise" an open question instead of a settled one.

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

Checked directly against Groq's response headers rather than assumed: the binding
constraint is not a flat requests-per-minute number. For `qwen/qwen3.6-27b` on this
key, `x-ratelimit-limit-tokens` is 8000/minute and `x-ratelimit-limit-requests` is
1000/day. Every case sends the full tool schema (4–7 function definitions) plus the
response, so token spend per call is what exhausts the budget — `qwen` is the model
that visibly hits 429s in every sweep; `gpt-oss-20b` and `llama-3.3-70b-versatile`
never have, on the same schedule. Pacing by request count (`DELAY_BETWEEN_CALLS_SECONDS`)
does not fully prevent this, because the limit isn't request count.

What actually keeps a sweep working is `client.py` retrying on 429 with backoff,
honouring the `Retry-After` header Groq sends. That recovers the run; it doesn't avoid
the throttling. Expect qwen specifically to spend real wall-clock time sitting in
retries — that's most of why a full sweep takes longer than the raw call count implies.
Set the delay to `0` for a local server, where this doesn't apply.

Repeats multiply the call count, not the pacing logic: 21 cases × 3 models × 3 runs is
roughly 190 base calls (more once chaining cases' internal turns are counted). Use
`--runs 1` with `--cases` for a quick check.

A failed API call is scored as a fail with the error in the notes column — it never
aborts the sweep, so one flaky model doesn't cost you the whole run.

## Known limitations

See **Scope** at the top for the three big ones (no MCP protocol, no self-hosted run,
not a certification). The rest, specific to the current 21-case suite:

- Refusal content is checked by substring keywords, not meaning. A model could satisfy
  `must_mention` while asking something unhelpful, and an unusual-but-correct phrasing
  can fail until the list is widened — this already happened once during development
  (see `must_mention` above). This is the general class of problem a 2026 validity audit
  of tool-calling benchmarks measured directly: automated evaluators disagreed with
  human experts on **18.5% of 496 reviewed tasks**
  ([arXiv 2607.02577](https://arxiv.org/html/2607.02577v1)). That paper's recommended
  fix is *not* getting a second human to independently re-label cases — it argues for
  decomposed, deterministic metrics plus reproducibility checks (repeated runs) over
  inter-annotator agreement, which is the direction this suite already went with
  Wilson-interval repeats. Deliberate v1 tradeoff either way: substring matching stays
  deterministic and offline-testable. An LLM judge would read intent properly, at the
  cost of nondeterminism and an API call per case.
- The `should_refuse` ground truth itself is a judgment call, not derived from a
  standard. `refusal_relative_date` scores a model as failing if it resolves "next
  Tuesday" itself rather than asking — a reasonable person could argue a
  model resolving it from context is being helpful, not wrong. This category carries
  all of the model-ranking signal in the suite, so a contestable call here has more
  leverage on the final numbers than any other single design decision in the project.
  Not yet independently reviewed.
- `selection`, `arguments`, and `restraint` are saturated — every model tested clears
  them completely (33/33, 12/12, 18/18 across 3 replicated runs each). `results.md`
  labels this explicitly per category now, but it also means 11 of 21 cases cost API
  budget and contribute nothing to ranking as currently written.
- Wilson intervals describe sampling uncertainty at fixed `temperature=0` output —
  they say nothing about whether a differently-phrased prompt or a different day's
  model snapshot would reproduce the same numbers. Repeats answer "is this noise
  within a run," not "is this stable across prompt variations or model updates."
