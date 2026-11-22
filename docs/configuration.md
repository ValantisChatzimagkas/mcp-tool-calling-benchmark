# Layout and configuration

Back to the [README](../README.md).

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

