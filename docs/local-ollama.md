# Running against a local Ollama server (CPU)

Back to the [README](../README.md).

This page records what was actually run against a local Ollama server on one CPU-only
laptop, and how to repeat it. It is a record of one small run, not a ranking and not a
self-hosting recommendation. Scope is stated in [What this licenses](#what-this-licenses).

## Setup

Requires [Ollama](https://ollama.com/) and uv, plus `node`/`npm`, `uv` and `bwrap` for
the server-backed cases (see the README).

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve          # server; keep it running
ollama pull qwen3:4b                              # one model at a time; check `df -h /` first
BACKEND=ollama uv run run_eval.py --models qwen3:4b --runs 1
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
- A run that exceeds the 600 s read timeout is scored as a failed run
  (`api error: request failed: ReadTimeout`), the same as any other HTTP failure. On a
  CPU that conflates slow hardware with model failure: a timed-out run says the model
  did not answer within 600 s on this machine, not what it would have answered.

## The machine and install that were used

| | |
|---|---|
| CPU | 11th Gen Intel Core i7-1195G7 @ 2.90 GHz, 8 threads, AVX-512 |
| RAM | 31 GiB |
| GPU | none used (the Iris Xe iGPU was not enabled; Ollama ran on `cpu`) |
| Ollama | 0.40.17, user-local build (`libggml-cpu-icelake` backend) |
| Models | `qwen3:4b`, `qwen3:8b`, both `Q4_K_M`, context 16384 (as reported by the server on every result row) |
| Other load | the laptop was in ordinary use (browser, editor); load was recorded next to each measurement but not controlled |

## Throughput

Measured with `throughput.py` through Ollama's native `/api/chat` timings, on the real
prompt and tool schemas of `chaining_three_steps` (457–459 prompt tokens), 5 repeats
after one discarded warm-up call, `num_predict=128`, a per-call nonce so Ollama's prompt
cache can't inflate the prompt rate (`cache_suspect=False` on every measurement below).
Median [min–max], measured 2026-11-24 20:06–20:18 CET, one model after the other with
nothing else of this project running:

| model | quantization | prompt tok/s | generation tok/s | n | prompt tokens | 1-min load average before |
|---|---|---|---|---|---|---|
| `qwen3:4b` | Q4_K_M | 21.7 [18.8–26.6] | 5.5 [4.7–7.0] | 5 | 457–459 | 2.6 |
| `qwen3:8b` | Q4_K_M | 15.1 [14.7–15.8] | 3.5 [3.4–4.0] | 5 | 457–458 | 11.5 (residue of the 4B measurement just before it; an Ollama runner was at ~380% CPU) |

The same command gave different medians at other times on the same laptop, which is
the more useful fact here: throughput on this machine depends on what else it is doing.

| model | when (CET) | load average before | prompt tok/s | generation tok/s |
|---|---|---|---|---|
| `qwen3:4b` | 2026-11-23 ~20:09 | not recorded | 33.6 [31.0–34.5] | 7.8 [7.6–7.9] |
| `qwen3:4b` | 2026-11-23 ~21:11 | 10.2 (an 8B sweep was running at the same time) | 13.9 [12.8–16.6] | 2.5 [2.1–3.2] |
| `qwen3:4b` | 2026-11-24 08:05 | 1.7 | 24.2 [22.3–28.0] | 5.9 [5.3–7.0] |
| `qwen3:4b` | 2026-11-24 20:06 | 2.6 | 21.7 [18.8–26.6] | 5.5 [4.7–7.0] |
| `qwen3:8b` | 2026-11-23 ~21:02 | not recorded | 12.4 [5.4–13.3] | 3.2 [2.0–3.4] |
| `qwen3:8b` | 2026-11-24 09:36 | 4.1 (own 4B sweep had just finished) | 18.1 [15.3–18.2] | 4.2 [4.0–4.4] |
| `qwen3:8b` | 2026-11-24 20:10 | 11.5 (own 4B measurement had just finished) | 15.1 [14.7–15.8] | 3.5 [3.4–4.0] |

Read it as "roughly 14–34 prompt and 2.5–8 generation tok/s for the 4B, 12–18 and
3–4 for the 8B, on this laptop, depending on load". None of it says anything about
other machines, other thread counts or the iGPU.

## End-to-end result on the current suite

Both models ran the current 20-case `cases.json` (14 canned + 6 server-backed), **1
run per case** (not the default 3), with `BACKEND=ollama` and no code or config edits.
Per-category `k/n` with 95% Wilson interval, copied from `results-qwen3-4b.md` and
`results-qwen3-8b.md`; reproduce with
`BACKEND=ollama uv run run_eval.py --models <tag> --runs 1`:

| model | overall | selection | arguments | chaining | restraint | refusal | server_chaining | registry | collision |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3:4b` | 14/20 (70%, 48%–85% CI) | 2/2 (100%, 34%–100% CI) | 1/2 (50%, 9%–91% CI) | 4/5 (80%, 38%–96% CI) | 2/2 (100%, 34%–100% CI) | 0/3 (0%, 0%–56% CI) | 1/2 (50%, 9%–91% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) |
| `qwen3:8b` | 17/20 (85%, 64%–95% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) | 5/5 (100%, 57%–100% CI) | 2/2 (100%, 34%–100% CI) | 1/3 (33%, 6%–79% CI) | 1/2 (50%, 9%–91% CI) | 2/2 (100%, 34%–100% CI) | 2/2 (100%, 34%–100% CI) |

What the failing runs were:

- `qwen3:4b`, 6 failed runs, **4 of them 600 s read timeouts** (`chaining_search_then_task`,
  `refusal_missing_assignee`, `refusal_relative_date`,
  `server_chaining_top_customer_email`). Per the note in Setup, those four say the 4B
  did not finish within 600 s on this CPU, not what it would have answered. The other two:
  `arguments_date_to_iso` (`person: 'mari' != 'maria'`) and `refusal_missing_body`
  (called `send_message` instead of asking for the missing body).
- `qwen3:8b`, 3 failed runs, no timeouts: `refusal_missing_assignee` (called
  `create_task`), `refusal_missing_body` (called `send_message`), and
  `server_chaining_top_customer_email` (wrote `example@example.com` instead of the
  address returned by the sqlite server, `edsger@example.com`).

Reading this honestly:

- **Saturated, non-discriminating categories**: `selection`, `restraint`, `registry` and
  `collision` were passed on every run by both models (8 of the 20 cases per model);
  `arguments` and `chaining` were passed on every run by the 8B only. With 2 runs per
  category the interval is `34%–100%`, which is not "reliable".
- **The 4B-vs-8B gap (14/20 vs 17/20, intervals `48%–85%` and `64%–95%`) overlaps
  heavily, and 4 of the 4B's 6 failures are timeouts.** No ranking is claimed. One run
  per case cannot show whether a failure is stable (the Groq sweep's 3 replications are
  what made its failures readable as stable).
- **Ground-truth assumption behind the refusal numbers (0/3 and 1/3):** `should_refuse`
  cases encode the policy that a model must ask rather than resolve the ambiguity
  itself. `refusal_relative_date` ("next Tuesday") is the contested case, see
  [known limitations](known-limitations.md). For the 4B two of its three refusal runs
  are timeouts, so its 0/3 is mostly about speed, not about that policy.
- Both models were run with their default (thinking) behaviour; nothing here toggles it.
  Thinking tokens are what push single calls to several minutes and some past 600 s.

How the two sweeps were assembled (both through the journal and `--resume`, which is
the mechanism this page documents; the journals are the source of every cell):

- `qwen3:8b`: started 2026-11-24 09:45 CET. 8 runs were journaled by 10:05. The host
  then stalled for about 9.5 hours in the middle of the next call (the Ollama log shows
  no activity between about 10:16 and 19:48 CET), and that call ended in a read timeout
  recorded at 19:48. That one record was removed by hand, because it measured the
  stall, not the model; the other 8 were kept and the sweep was finished with
  `--resume` from 20:18 to 21:20 CET (12 runs). Running time: about 20 min + 62 min.
- `qwen3:4b`: the 14 canned runs are from a 1-run sweep on 2026-11-24 08:10–09:36 CET
  that ran the suite as it was before SOL-103 (21 canned cases). The 14 cases that
  survived SOL-103 have the same case hash, so `--resume` reused those records as they
  were (the 7 removed cases are simply not in the suite); the 6 server-backed cases were
  run with `--resume` from 21:30 to 22:05 CET. Running time for the 6: about 35 min.

## Interrupt and resume, checked live

On 2026-11-24 at 22:11 CET, a 3-case `qwen3:4b` sweep (`--runs 1 --cases
selection_search_vs_lookup selection_update_vs_create restraint_explicit_negation`) got
a real SIGINT after its first run was journaled. It printed `interrupted; completed runs
are journaled — rerun with --resume` and left 1 record. Rerunning with `--resume` sent
exactly 2 chat requests (counted in the Ollama server log), and `results.csv` had all
3 rows at 1/1, the first one keeping its original timestamp.

## GPU acceleration and longer sweeps

Vulkan/SYCL acceleration on the Iris Xe was not tried. At the measured CPU rates a
20-case, 1-run sweep took roughly 80 minutes for the 8B. A default 3-run sweep is three
times the calls, so for the 8B it fits overnight on CPU without the iGPU. That is
arithmetic from the 1-run sweep, not a measured 3-run sweep.

## What this licenses

Reliable-or-not statements are limited to: *these two Qwen3 models at `Q4_K_M`, served
by Ollama 0.40.17 on this CPU laptop at a 16384-token context, on the current 20-case
suite, 1 run each.* Still untested: replicated runs, other quantizations, hosted-vs-local
comparison on the same cases, other serving stacks (llama.cpp, vLLM), the iGPU, and any
other machine. Nothing here supports calling either model a good candidate for
self-hosting.
