# Running against a local Ollama server (CPU)

Back to the [README](../README.md).

This page records what was actually run against a local Ollama server on one CPU-only
laptop, and how to repeat it. It is a record of one small run, not a ranking and not a
self-hosting recommendation. Scope is stated in [What this licenses](#what-this-licenses).

## Setup

Requires [Ollama](https://ollama.com/) and uv.

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve          # server; keep it running
ollama pull qwen3:4b                              # one model at a time; check `df -h /` first
BACKEND=ollama uv run run_eval.py --models qwen3:4b --runs 1 --cases <case ids...>
BACKEND=ollama uv run throughput.py --model qwen3:4b --repeats 5
ollama rm qwen3:4b                                # free the disk before the next model
```

- `BACKEND=ollama` selects the `ollama` profile in `config.py` (600 s timeout, no delay,
  no key, no 429 retries). A `.env` that only sets `API_BASE_URL` keeps the Groq
  profile's tuning and is stamped `backend=groq`; set `BACKEND`.
- `OLLAMA_CONTEXT_LENGTH=16384` is set on the server, not the client. Every result row
  records the context window the server reported (`context_length`), so a server started
  without it shows a different value rather than silently truncating tool definitions.
  Nothing fails a run for truncation; check the column.
- A sweep writes `results.journal.jsonl` as it goes. After a crash or Ctrl-C, rerun the
  same command with `--resume` to skip the (model, case, run) rows already done. A
  finished sweep archives its journal as `results.journal.<UTC timestamp>.jsonl`.
- Each sweep overwrites `results.csv` / `results.md`; copy them aside (the
  `results-*.csv` / `results-*.md` names are gitignored) before the next model.

## The machine and install that were used

| | |
|---|---|
| CPU | 11th Gen Intel Core i7-1195G7 @ 2.90 GHz, 8 threads, AVX-512 |
| RAM | 31 GiB |
| GPU | none used (the Iris Xe iGPU was not enabled; Ollama ran on `cpu`) |
| Ollama | 0.40.17, user-local build (`libggml-cpu-icelake` backend) |
| Models | `qwen3:4b`, `qwen3:8b`, both `Q4_K_M`, context 16384 (as reported by the server) |
| Other load | the laptop was in ordinary use during the runs; load was not controlled |

## Throughput

Measured with `throughput.py` through Ollama's native `/api/chat` timings, on the real
prompt and tool schemas of `chaining_three_steps` (456–459 prompt tokens), 5 repeats
after one discarded warm-up call, `num_predict=128`, a per-call nonce so Ollama's prompt
cache can't inflate the prompt rate. Median [min–max]:

| model | quantization | prompt tok/s | generation tok/s | n | prompt tokens |
|---|---|---|---|---|---|
| `qwen3:4b` | Q4_K_M | 33.6 [31.0–34.5] | 7.8 [7.6–7.9] | 5 | 458–459 |
| `qwen3:8b` | Q4_K_M | 12.4 [5.4–13.3] | 3.2 [2.0–3.4] | 5 | 456–459 |

The 8B spread is wide (one slow repeat each side of the median). Other processes were
running on the laptop and were not controlled for, so treat the 8B minimum as "this
laptop under ordinary use", not as a stable property of the model. Neither number says
anything about other machines, other thread counts or the iGPU.

## End-to-end result on a subset of the suite

A full sweep was not run. Each model got **10 of the 21 cases** (`cases.json` has 7
selection, 5 chaining, 4 arguments, 3 refusal and 2 restraint), two per category, **1
run each**:

| category | cases run |
|---|---|
| selection | `selection_calendar_lookup`, `selection_search_vs_lookup` |
| arguments | `arguments_date_to_iso`, `arguments_enum_value` |
| chaining | `chaining_id_from_output`, `chaining_three_steps` |
| restraint | `restraint_explicit_negation`, `restraint_general_question` |
| refusal | `refusal_missing_body`, `refusal_relative_date` |

Per-category `k/n` with 95% Wilson interval (from `results-qwen3-4b.md` and
`results-qwen3-8b.md`; each reproducible by rerunning the commands above with
`--cases` set to these ids):

| model | overall | selection | arguments | chaining | restraint | refusal |
|---|---|---|---|---|---|---|
| `qwen3:4b` | 8/10 (80%, 49%–94% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 0/2 (0%, 0%–66% CI) |
| `qwen3:8b` | 9/10 (90%, 60%–98% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 1/2 (50%, 9%–91% CI) |

What the failing runs were:

- `refusal_missing_body`: both models called `send_message` instead of asking for the
  missing message body (0/1 each).
- `refusal_relative_date`: `qwen3:4b` hit the 600 s read timeout while still generating
  and is scored as a failed run (`api error: request failed: ReadTimeout`); that is a
  harness limit, not evidence about what the model would have answered. `qwen3:8b`
  passed it (1/1).

Reading this honestly:

- **The four non-refusal categories are saturated in this subset**: every run passed for
  both models, so they contribute no discriminating evidence. 8 of the 10 cases per
  model are in those categories, so most of each overall figure is ceiling. With 2 runs
  per category the interval is `34%–100%`, which is not "reliable".
- **The refusal figures carry the whole difference, from 2 runs per model**, one of which
  for `qwen3:4b` is a timeout. The 4B-vs-8B gap (8/10 vs 9/10, intervals `49%–94%` and
  `60%–98%`) overlaps heavily; **no ranking is claimed**, and one run per case cannot show
  whether a failure is stable (the Groq sweep's 3 replications are what made its failures
  readable as stable).
- **Ground-truth assumption behind the refusal numbers:** `should_refuse` cases encode the
  policy that a model must ask rather than resolve the ambiguity itself.
  `refusal_relative_date` ("next Tuesday") is the contested case; see
  [known limitations](known-limitations.md).
- Wall-clock for the 10-case, 1-run sweep: roughly 40 minutes for `qwen3:4b` (interrupted
  once and finished with `--resume`: 2 runs journaled before the interrupt, the other 8
  after, with the two earlier run timestamps preserved) and roughly 35 minutes for
  `qwen3:8b` (uninterrupted).
- Both models were run with their default (thinking) behaviour; nothing here toggles it.

## What this licenses

Reliable-or-not statements are limited to: *these two Qwen3 models at `Q4_K_M`, served
by Ollama 0.40.17 on this CPU laptop at a 16384-token context, on 10 of the 21 cases, 1
run each.* Still untested: the other 11 cases, replicated runs, other quantizations,
hosted-vs-local comparison on the same cases, other serving stacks (llama.cpp, vLLM), the
iGPU, and any other machine. Nothing here supports calling either model a good candidate
for self-hosting.
