# Rate limits

Back to the [README](../README.md).


Checked directly against Groq's response headers rather than assumed: the binding
constraint is not a flat requests-per-minute number. For `qwen/qwen3.6-27b` on this
key, `x-ratelimit-limit-tokens` is 8000/minute and `x-ratelimit-limit-requests` is
1000/day. Every case sends the full tool schema (4–7 function definitions) plus the
response, so token spend per call is what exhausts the budget — `qwen` is the model
that visibly hits 429s in every sweep; `gpt-oss-20b` and `llama-3.3-70b-versatile`
never have, on the same schedule. Pacing by request count (`DELAY_BETWEEN_CALLS_SECONDS`)
does not fully prevent this, because the limit isn't request count.

What actually keeps a sweep working is `client.py` retrying on 429 with backoff,
honouring the `Retry-After` header Groq sends. That recovers the run; it doesn't avoid
the throttling. Expect qwen specifically to spend real wall-clock time sitting in
retries — that's most of why a full sweep takes longer than the raw call count implies.
The `ollama` profile sets the delay to `0` (and 429 retries to `0`) for a local server,
where this doesn't apply.

Repeats multiply the call count, not the pacing logic: 21 cases × 3 models × 3 runs is
roughly 190 base calls (more once chaining cases' internal turns are counted). Use
`--runs 1` with `--cases` for a quick check.

A failed API call is scored as a fail with the error in the notes column — it never
aborts the sweep, so one flaky model doesn't cost you the whole run.

