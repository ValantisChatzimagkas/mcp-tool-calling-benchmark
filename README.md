# MCP Tool-Calling Benchmark

[![CI](https://github.com/ValantisChatzimagkas/mcp-tool-calling-benchmark/actions/workflows/ci.yml/badge.svg)](https://github.com/ValantisChatzimagkas/mcp-tool-calling-benchmark/actions/workflows/ci.yml)

Measures how reliably a model handles MCP-style tool calling: picking the right tool,
filling in the right arguments, chaining calls that depend on each other, and — just
as important — *not* calling a tool when it shouldn't. It works against anything
exposing an OpenAI-compatible `/chat/completions` endpoint (Groq for fast iteration;
llama.cpp / Ollama / vLLM for self-hosted runs) by changing one config value.

## Scope — read this before trusting a number in this repo

**What this tests:** OpenAI-format function-calling reliability, for tool schemas
shaped like the tools an MCP server would expose (a calendar lookup, a search, a
create-task call, and so on).

**What this does not test:**

- **The MCP protocol itself.** No JSON-RPC, no `tools/list` / `tools/call`, no running
  MCP server anywhere in this repo. Tool schemas are hand-authored dicts in
  `cases.json`, sent directly as OpenAI-format `tools`. A real MCP server's schemas are
  known to be messier than hand-written ones ([BFCL v4's own
  notes say so](https://gorilla.cs.berkeley.edu/leaderboard.html)), and multi-server
  tool-namespace collisions aren't modeled at all — this suite has one flat tool
  registry. Both are plausible sources of failure this benchmark cannot see.
- **The actual self-hosted stack.** Every result so far ran against Groq's hosted
  inference. `API_BASE_URL` can point at llama.cpp/Ollama with one `.env` line, but that
  path has not actually been exercised. Quantization and serving differences could shift
  tool-calling behavior in either direction — this is untested, not assumed fine.
- **A certification.** A model passing every case here is evidence it's a reasonable
  *candidate* for MCP-style tool use, not proof it will behave identically in a real
  MCP-based agent. Treat results as a screening signal, not a verdict. See
  [Known limitations](docs/known-limitations.md) for the specific gaps in the current
  21-case suite.

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

Each (model, case) pair runs 3 times by default and reports `k/n` with a 95% Wilson
interval. Results go to `results.csv` and `results.md` (both gitignored). For a
self-hosted server, set `API_BASE_URL` in `.env` — see
[configuration](docs/configuration.md).

## Key findings

From the last full sweep (3 models × 21 cases × 3 runs, Groq-hosted). Details and
caveats in [docs/findings.md](docs/findings.md).

- `refusal` and `chaining` carry the signal. `selection`, `arguments` and `restraint`
  were saturated at 100% for every model (33/33, 12/12, 18/18).
- Every failing cell landed at exactly 0/3 or 3/3 — stable failure modes at
  `temperature=0`, not sampling noise. One model went 0/9 on `refusal` and never asked a
  clarifying question; another fabricated identifiers instead of reading tool output on
  2 of 5 chaining cases; the other two split 3/9 vs 6/9 on refusal.
- "Zero split cells" with 3 runs is suggestive, not proof of stability.

## How scoring works

Argument comparison is exact-match after lowercasing and stripping whitespace. No
fuzzy or semantic matching. This is deliberate: every failure note is then unambiguous
and cheap to verify by hand. The tradeoff is that a semantically-correct paraphrase can
fail — when a value is legitimately open-ended, add it to that case's `loose_args`
rather than loosening the comparison for everything.

`multi_call_chain` collapses consecutive repeat calls to the same tool before
comparing, so retrying one tool doesn't fail the case, but slipping in an extra
*different* tool does.


Cases are scored by `type` (`single_call`, `multi_call_chain`, `no_call`,
`should_refuse`) and reported by `category` (`selection`, `arguments`, `chaining`,
`restraint`, `refusal`); the full catalogue is in [docs/cases.md](docs/cases.md).

## More documentation

- [docs/cases.md](docs/cases.md) — case format, types and categories, chain
  expectations, adding cases/categories/models
- [docs/configuration.md](docs/configuration.md) — layout, settings, self-hosted endpoints
- [docs/output.md](docs/results.md) — repeated runs, Wilson intervals, output files
- [docs/findings.md](docs/findings.md) — where models actually diverge
- [docs/rate-limits.md](docs/rate-limits.md) — Groq rate limits and retry behavior
- [docs/known-limitations.md](docs/known-limitations.md)

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


## License

[MIT](LICENSE) © 2026 Chrysovalantis Chatzimagkas.
