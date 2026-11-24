# Repeated runs and output files

Back to the [README](../README.md).

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
  [3/3] selection_search_vs_lookup ok
  [0/3] refusal_missing_body      called tool(s) when none expected: send_message
  [1/3] chaining_three_steps      get_document: wrong value for document_id: 'doc_id_from_search_results' != 'doc_114'
```

Two files are written at the end (both gitignored — they are regenerated every run),
built from the run journal described below:

- `results.csv` — one row per (model, case):
  `model, backend, endpoint, quantization, context_length, run_timestamp, case_id,
  category, case_type, status, runs_passed, runs_total, notes`. Every row from one
  sweep carries the same `backend`, `endpoint` and `run_timestamp` — the point is that
  if you save two CSVs from different sweeps (Groq today, a self-hosted server next
  week) and later merge or paste them together, the difference in conditions is in the
  data, not just in a header you might not have kept. `model` is the tag as the server
  knows it; `quantization` is what the server reported it loaded (`qwen3:4b` says
  nothing about Q4 vs Q8 on its own; `unknown (hosted)` for Groq, which doesn't expose
  it) and `context_length` is the context window the server actually loaded the model
  with, observed rather than configured (empty when unknown). The two counts are plain
  integers; `notes` holds the deduped notes from the failing runs.
  `status` is `ok` for a model that was run, or `unsupported` for one whose chat
  template has no tool support (the server's `/api/show` doesn't list `tools` among its
  capabilities). An `unsupported` row has `runs_total=0` and `runs_passed=0`, and the
  model is never sent a request — it is reported as untestable, not as `0/n`. Because
  its `runs_total` is zero, such a row sits outside every category and overall rollup,
  and `results.md` shows `unsupported` in its place. (Only Ollama reports capabilities;
  Groq models are always treated as supported.)
- `results.md` — run metadata (including the suite file it was produced from, so a
  `--suite` run can't be mistaken for the main sweep), a pass-rate table broken down by
  category, the full
  result matrix grouped by category (`passed/total` per cell), and every failure note

Every rate — per category and overall — is passed runs over total runs, so a case that
passes 2 of 3 counts as two thirds of a case instead of rounding to a pass or a fail.

The per-category breakdown is the point: one aggregate number tells you a model scored
14/17, which is not actionable. The breakdown tells you it went 5/5 on selection and
1/3 on refusal, which is.

## Interrupted sweeps and `--resume`

A sweep against a CPU-hosted model can run for hours, so every finished run is appended
to `results.journal.jsonl` (gitignored) as it completes: one JSON line carrying the
backend, endpoint, model, quantization, case id, a hash of the case and its tool
schemas, the run index, pass/fail, the note and the observed context length. `results.csv`
and `results.md` are rebuilt from the journal at the end, so a resumed sweep and an
uninterrupted one produce the same rows (except `run_timestamp`, which is the earliest
run in each row, since a resumed sweep spans sessions).

- **Interrupt** (Ctrl-C, or the server dropping the connection): the runs already
  recorded are kept. Rerun the same command with `--resume` and only the missing
  (model, case, run) rows execute.
- **Without `--resume`**, a non-empty live journal makes the run stop immediately
  rather than fold old runs into what was meant to be a fresh sweep.
- **A record is reused only if its whole key matches**: backend, endpoint, model,
  quantization, case id, case hash and run index. Edit a case's prompt or tools, switch
  quantization, or point at another host, and those runs execute again. A different
  `--runs` just ignores journaled runs beyond the new count. For a server-backed case
  (`mcp_servers`) the tool schemas come from the live server at run time, so the hash
  covers what determines them instead: each server's pinned kind (package spec and
  launch line from `config.MCP_SERVER_KINDS`) and a digest of its seed fixture under
  `fixtures/`. Bumping a pin or editing a fixture reruns those cases; computing the hash
  never starts a server or touches the network. It does not see a drift inside an
  unpinned range (e.g. the `mcp<2` bound of the sqlite server).
- **Server-backed failures.** A server that fails to start, or crashes mid-run, is
  recorded as a failed run with an `mcp server error: ...` note (as for a sweep without
  `--resume`); a model without tool support is still `unsupported` and never starts a
  server.
- **Server unreachable.** If the connection itself fails (refused, reset — a crashed or
  OOM-killed server), the sweep stops with `server unreachable (...); rerun with
  --resume` and that run is *not* journaled. Read timeouts and HTTP errors are still
  recorded as failed runs, as before. This applies to every backend: a network outage
  now aborts a sweep (resumably) instead of scoring `0/n`.
- **Finished sweeps archive their journal** to `results.journal.<UTC timestamp>.jsonl`
  once both result files are written, so the next plain `uv run run_eval.py` starts
  fresh. Archives accumulate until you delete them by hand. A crash while writing the
  results leaves the live journal in place for `--resume`.
