# MCP Tool-Calling Benchmark

Scores models on MCP-style tool calling via any OpenAI-compatible
`/chat/completions` endpoint (Groq now, self-hosted later).

## Commands

Managed with uv. Prefix everything with `uv run`; don't call `.venv/bin/python` or pip.

- Run: `uv run run_eval.py` (`--models <id>` / `--cases <case_id>` to narrow, `--runs <n>`
  to override `RUNS_PER_CASE`)
- Test: `uv run pytest` (offline, no API calls)
- Lint: `uv run ruff check .` and `uv run ruff format .`
- Deps: `uv add <pkg>` / `uv add --dev <pkg>`, then commit `uv.lock`

## Layout

| file | holds |
|---|---|
| `config.py` | endpoint, key, model list, tuning — the only place these live |
| `client.py` | HTTP and response parsing — the only module that touches the network |
| `scoring.py` | pure pass/fail logic — no I/O, no config |
| `run_eval.py` | loads the suite, runs cases, writes results, CLI |
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

## Gotchas

- `.env` holds a real Groq key. It is gitignored — never print it, commit it, or echo
  it into a shell command.
- Groq free tier is ~30 requests/minute; `DELAY_BETWEEN_CALLS_SECONDS` exists for that
  reason. Don't remove it when testing against Groq. Repeats make the full sweep ~190
  calls and ~10 minutes long; use `--runs 1` with `--cases` when smoke-testing.
- A failing benchmark case usually means the *model* got it wrong. Confirm the harness
  is actually wrong before changing scoring logic to make a case pass.
- `must_mention` on a `should_refuse` case must be calibrated against what models
  actually say, not guessed. A too-narrow list fails correct refusals — it already
  happened once ("exact text" missed a list without "text"). Print the response before
  trusting the verdict.
- Scoring's dangerous failure is silent leniency, not a crash — an expectation that
  stops checking anything still prints PASS. Any change to `scoring.py` needs a
  `tests/test_scoring.py` case that fails before the change.
