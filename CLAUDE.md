# MCP Tool-Calling Benchmark

Scores models on MCP-style tool calling via any OpenAI-compatible
`/chat/completions` endpoint (Groq now, self-hosted later).

**Scope:** Tests OpenAI-format function-calling reliability for tools shaped like MCP
tools. Does not exercise the MCP protocol — no JSON-RPC, no `tools/list` / `tools/call`,
no running MCP server. The name is kept for the connection to why the tools are shaped
this way; don't let it license a protocol-level claim.

## Commands

Managed with uv. Prefix everything with `uv run`; don't call `.venv/bin/python` or pip.

- Run: `uv run run_eval.py` (`--models <id>` / `--cases <case_id>` to narrow, `--runs <n>`
  to override `RUNS_PER_CASE`, `--resume` to continue an interrupted sweep from its
  journal)
- Test: `uv run pytest` (offline, no API calls)
- Lint: `uv run ruff check .` and `uv run ruff format .`
- Deps: `uv add <pkg>` / `uv add --dev <pkg>`, then commit `uv.lock`

## Layout

| file | holds |
|---|---|
| `config.py` | backend profiles (`groq`, `ollama`): endpoint, key, model list, tuning — the only place these live |
| `client.py` | HTTP and response parsing — the only module that touches the network |
| `scoring.py` | pure pass/fail logic — no I/O, no config |
| `run_eval.py` | loads the suite, runs cases, writes results, CLI |
| `journal.py` | per-run append-only journal behind `--resume` — file I/O only, no network, no config |
| `throughput.py` | measures prompt/generation tok/s from Ollama's native `/api/chat` timings (`BACKEND=ollama` only) |
| `cases.json` | the benchmark suite (data, not code) |

## Vocabulary

"Case" means a benchmark case (`cases.json`, `case_id`/`case_type` in results.csv).
"Test" means pytest. Don't mix them — the two were conflated early on and it was
confusing. A "run" is one execution of one case: each (model, case) pair runs
`RUNS_PER_CASE` times, and a row is `runs_passed`/`runs_total`, never a single verdict.

Each case carries two labels: `type` drives scoring (the dispatch in `run_case`),
`category` drives reporting and naming. Case ids are `<category>_<specific>`, and
`tests/test_suite_integrity.py` enforces that plus category grouping. Add a case in its
category's block; don't append to the end of the file.

## Conventions

- Endpoint, key and model list live in `config.py` only. Nothing else reads env vars
  or hardcodes a URL — that single-config property is what lets the same script hit a
  local server unchanged.
- Cases are data (`cases.json`), never code. Adding a case or a tool schema must not
  require editing `run_eval.py`.
- Scoring is exact string/key comparison, not semantic. Loosen a specific case with
  `loose_args`, never by weakening the shared comparison.
- Results are rates, not verdicts. `results.csv` carries `runs_passed`/`runs_total`,
  `results.md` cells are `passed/total`, and every category and overall figure is summed
  runs over summed runs — not a count of fully-passing cases. A rollup that counts cases
  instead of runs prints a plausible table and is silently wrong; `_tally` is tested for
  exactly that in `tests/test_suite_integrity.py`.
- `scoring.py` stays pure. Anything needing network or config belongs elsewhere, or
  the tests stop being able to run offline.

## Evidence policy

Applies to every write-up of results — `results.md` commentary, READMEs, commit
messages, chat summaries. The failure mode this prevents is a confident headline that
the numbers don't support.

1. **No ranking claim without naming the specific cases that drove it.** "qwen scored
   higher" is not a claim. "qwen passed 6/9 refusal runs, gpt-oss-20b 3/9, with the same
   two cases failing all 3 replications each" is.
2. **State `k/n` and its 95% Wilson interval, never a bare percentage.** `wilson_interval`
   in `run_eval.py` computes it; `_fraction`/`_cell` render it. `3/3` is not "100%
   reliable" — it's `44%–100%` at 95% confidence, and the report says so. This is the
   one piece of statistical machinery in scope, deliberately: it's a closed-form formula
   with no dependency, chosen because it stays sane at `p=0`/`p=1` where most cells sit.
   Nothing past this — no significance tests, no multi-model comparison stats. That
   would be precision the 3-run sample doesn't support.
3. **Flag saturated categories as non-discriminating.** `write_markdown` already does
   this per category (`saturated, contributes no discriminating evidence`) when every
   model clears every run. Don't quote an overall figure without noting how much of it
   is ceiling categories.
4. **Name the ground-truth assumption behind any refusal-based claim.** `should_refuse`
   cases encode a debatable policy — that a model must ask rather than resolve ambiguity
   itself. `refusal_relative_date` ("next Tuesday") is the concrete contested case — see
   README's Known limitations. State the assumption alongside the number; don't present
   it as settled.
5. **Scope every capability claim to what was actually tested.** Results from
   Groq-hosted inference license "reliable under Groq-hosted inference"; the Ollama CPU
   run (`docs/local-ollama.md`: 10 of 21 cases, 1 run each, one laptop) licenses only
   that scoped sentence, and other serving stacks stay untested — never "good candidate
   for self-hosting". See README's Scope
   section; it exists because "MCP Tool-Calling Benchmark" oversold what this tests
   before that section was written — don't repeat that in a summary or a commit message.
6. **Ground methodology choices in established work, not ad-hoc invention.** The `k/n`
   framing follows τ-bench's `pass^k`. Any future scoring change (e.g. BFCL's
   AST-vs-executable distinction) cites the approach it borrows. `score_chain`'s exact
   sequence-and-args matching is stricter than τ-bench (end-state only) or BFCL
   ("minimally sufficient," not one fixed path) — documented as a deliberate tradeoff
   in README's Chain expectations section, not left implicit. If chain scoring changes,
   keep that comparison current.
7. **Case/category counts in prose must match `cases.json`, checked, not remembered.**
   README's category table once said 5 selection / 2 arguments after a hardening pass
   had already made it 7/4 — a stale claim, not a lie, but indistinguishable from one to
   a reader. Before writing a count into prose, `.venv/bin/python -c "import json;
   from collections import Counter; print(Counter(c['category'] for c in
   json.load(open('cases.json'))['cases']))"` or equivalent — don't recall it.

## Gotchas

- `.env` holds a real Groq key. It is gitignored — never print it, commit it, or echo
  it into a shell command.
- Groq's real constraint (checked via response headers, not assumed) is tokens/minute
  per model — 8000 TPM for qwen on this key — not a flat requests/minute number. Tool
  schemas are large, so `qwen` hits 429s that `gpt-oss-20b`/`llama-3.3-70b` don't, on
  the same schedule. `DELAY_BETWEEN_CALLS_SECONDS` paces by request count and does not
  prevent this by itself; the retry-with-backoff in `client.py` is what actually
  recovers the run. Don't "fix" qwen's retries by cranking the delay up further without
  checking `x-ratelimit-*` headers first — request-count pacing isn't the lever.
  Repeats make the full sweep ~190 base calls and ~10 minutes long; use `--runs 1` with
  `--cases` when smoke-testing.
- A failing benchmark case usually means the *model* got it wrong. Confirm the harness
  is actually wrong before changing scoring logic to make a case pass.
- `must_mention` on a `should_refuse` case must be calibrated against what models
  actually say, not guessed. A too-narrow list fails correct refusals — it already
  happened once ("exact text" missed a list without "text"). Print the response before
  trusting the verdict.
- Scoring's dangerous failure is silent leniency, not a crash — an expectation that
  stops checking anything still prints PASS. Any change to `scoring.py` needs a
  `tests/test_scoring.py` case that fails before the change.
