# Where models actually diverge

Back to the [README](../README.md).


`refusal` and `chaining` carry the signal — confirmed by 3 replicated runs per case,
not a single sample. In the last full sweep (3 models × 21 cases × 3 runs, 189
case-runs total), `selection`, `arguments` and `restraint` passed every run for every
model: zero variation anywhere in those categories, so they contribute no
discriminating evidence.

Every failing cell in that sweep landed at exactly 0/3 or 3/3 — no cell split its 3
runs. That matters: it means the observed differences are stable failure modes at
`temperature=0`, not sampling noise that a rerun would shuffle. Concretely: one model
went 0/9 on `refusal` and never once asked a clarifying question; another fabricated
identifiers instead of reading tool output on 2 of 5 chaining cases (`0/3` on each,
every run); the two models other than the first split 3/9 vs 6/9 on refusal, consistently, run
over run.

**Don't read "zero split cells" as proof this suite has no run-to-run variance.**
LiveMCPBench — a benchmark in the same space — found 23 repeated evaluations of one
identical setup swing 57.9%–76.8%, a 19-point spread wide enough to reorder a
leaderboard ([validity audit, arXiv 2607.02577](https://arxiv.org/html/2607.02577v1)).
Our 3-run replication not splitting could mean these 21 cases are genuinely simpler and
lower-variance than that benchmark's tasks — plausible, since ours are single- and
few-step decisions, not long multi-turn agentic runs — or it could mean 3 runs isn't
enough to catch a rarer variance mode. Both are live possibilities; 3-for-3 is
suggestive, not a proof of stability.

That is also the argument for the category split: an aggregate "45/63" hides which of
those is true, and a bare `k/n` without the replication would leave "is this real or
noise" an open question instead of a settled one.

