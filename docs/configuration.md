# Layout and configuration

Back to the [README](../README.md).

## Layout

```
config.py     endpoint, key, model list, tuning — the only place these live
client.py     HTTP and response parsing — the only module that touches the network
scoring.py    pure pass/fail logic — no I/O, no config, fully unit-tested
mcp_client.py spawns local stdio MCP servers; tools/list, tools/call (opens no sockets)
sandbox.py    per-run temp sandboxes, pinned server installs, bwrap confinement argv
run_eval.py   loads the suite, runs cases, writes results, CLI
cases.json    the benchmark suite (data, not code): 14 canned + 6 server-backed cases
mcp_smoke.json 3 server-backed smoke cases, run with --suite
fixtures/     seed data copied into each run's sandbox
.mcp-servers/ pinned server installs (gitignored, created on first use)
tests/        pytest — scoring logic, suite integrity, MCP facade; `-m live` for real servers
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
| `MCP_SERVER_KINDS` | server-backed cases: pinned install spec, runtime and launch line per kind |
| `MCP_SERVERS_DIR` | where pinned servers are installed (`.mcp-servers`) |
| `FIXTURES_DIR` | seed data for server-backed cases (`fixtures`) |
| `MCP_CONFINEMENT` | `"bwrap"` (default; fails loudly if missing) or `"none"` (explicit opt-out) |
| `MCP_STARTUP_TIMEOUT_SECONDS` / `MCP_CALL_TIMEOUT_SECONDS` / `MCP_SHUTDOWN_TIMEOUT_SECONDS` | bounds on server startup, one `tools/call`, and teardown |

### Self-hosted endpoints

Only `.env` changes:

```bash
API_BASE_URL=http://localhost:8080/v1     # llama.cpp server
API_BASE_URL=http://localhost:11434/v1    # Ollama
```

Then set `MODELS` to the names your server serves. The API-key check is skipped
automatically for `localhost`.

