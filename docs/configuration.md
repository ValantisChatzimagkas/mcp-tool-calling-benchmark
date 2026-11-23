# Layout and configuration

Back to the [README](../README.md).

## Layout

```
config.py     endpoint, key, model list, tuning — the only place these live
client.py     HTTP and response parsing — the only module that touches the network
scoring.py    pure pass/fail logic — no I/O, no config, fully unit-tested
run_eval.py   loads the suite, runs cases, writes results, CLI
journal.py    per-run append-only journal behind --resume — file I/O only, no network
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
| `BACKEND` | env var; picks a profile in `PROFILES` — `groq` (default) or `ollama` |
| `API_BASE_URL` | endpoint; defaults from the profile, overridable from `.env` |
| `MODELS` | default model ids to sweep; defaults from the profile, `--models` overrides |
| `RUNS_PER_CASE` | runs per (model, case); `--runs` overrides it per invocation |
| `REQUEST_TIMEOUT_SECONDS` | per-request timeout (from the profile) |
| `DELAY_BETWEEN_CALLS_SECONDS` | rate-limit spacing (from the profile; see below) |
| `MAX_RETRIES_ON_429` | rate-limit retries (from the profile) |
| `MAX_CHAIN_STEPS` | turn budget for a `multi_call_chain` case |

One `BACKEND` switch moves every profile value together — there are deliberately no
per-setting env overrides, so a CPU server can't end up paced like Groq's free tier.

| profile | base URL | key | timeout | delay | 429 retries | models |
|---|---|---|---|---|---|---|
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | 60 s | 3.0 s | 3 | three Groq ids |
| `ollama` | `http://localhost:11434/v1` | none (never sent) | 600 s | 0 s | 0 | `qwen3:4b` |

### Self-hosted endpoints

Set `BACKEND=ollama` in `.env` (or the environment) and start the server with
`OLLAMA_CONTEXT_LENGTH=16384`. The harness records the context window it observes on
every result row. A `.env` that only sets `API_BASE_URL` to a local server keeps the
`groq` profile's timeout and pacing and is stamped `backend=groq`, so set `BACKEND`.

