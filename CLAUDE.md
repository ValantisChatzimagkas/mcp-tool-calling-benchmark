# MCP Tool-Calling Benchmark

Scores models on MCP-style tool calling via any OpenAI-compatible
`/chat/completions` endpoint (Groq now, self-hosted later).

## Commands

Managed with uv. Prefix everything with `uv run`; don't call `.venv/bin/python` or pip.

- Run: `uv run run_eval.py` (`--models <id>` / `--cases <case_id>` to narrow)
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
confusing.

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
- `scoring.py` stays pure. Anything needing network or config belongs elsewhere, or
  the tests stop being able to run offline.

## Gotchas

- `.env` holds a real Groq key. It is gitignored — never print it, commit it, or echo
  it into a shell command.
- Groq free tier is ~30 requests/minute; `DELAY_BETWEEN_CALLS_SECONDS` exists for that
  reason. Don't remove it when testing against Groq.
- A failing benchmark case usually means the *model* got it wrong. Confirm the harness
  is actually wrong before changing scoring logic to make a case pass.
- Scoring's dangerous failure is silent leniency, not a crash — an expectation that
  stops checking anything still prints PASS. Any change to `scoring.py` needs a
  `tests/test_scoring.py` case that fails before the change.
