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

