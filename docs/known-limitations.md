# Known limitations

Back to the [README](../README.md).


See **Scope** in the README for the three big ones (no MCP protocol, self-hosting only
run as a 10-case, 1-run subset on one machine under Ollama — see
[local-ollama.md](local-ollama.md) — and not a certification). The rest, specific to the
current 21-case suite:

- Refusal content is checked by substring keywords, not meaning. A model could satisfy
  `must_mention` while asking something unhelpful, and an unusual-but-correct phrasing
  can fail until the list is widened — this already happened once during development
  (see `must_mention` above). This is the general class of problem a 2026 validity audit
  of tool-calling benchmarks measured directly: automated evaluators disagreed with
  human experts on **18.5% of 496 reviewed tasks**
  ([arXiv 2607.02577](https://arxiv.org/html/2607.02577v1)). That paper's recommended
  fix is *not* getting a second human to independently re-label cases — it argues for
  decomposed, deterministic metrics plus reproducibility checks (repeated runs) over
  inter-annotator agreement, which is the direction this suite already went with
  Wilson-interval repeats. Deliberate v1 tradeoff either way: substring matching stays
  deterministic and offline-testable. An LLM judge would read intent properly, at the
  cost of nondeterminism and an API call per case.
- The `should_refuse` ground truth itself is a judgment call, not derived from a
  standard. `refusal_relative_date` scores a model as failing if it resolves "next
  Tuesday" itself rather than asking — a reasonable person could argue a
  model resolving it from context is being helpful, not wrong. This category carries
  all of the model-ranking signal in the suite, so a contestable call here has more
  leverage on the final numbers than any other single design decision in the project.
  Not yet independently reviewed.
- `selection`, `arguments`, and `restraint` are saturated — every model tested in the
  Groq sweep clears them completely across 3 replicated runs (the two local Qwen3
  models also passed every run of the 2 cases per category they were given, 1 run each,
  which is weaker evidence). `results.md` labels this explicitly per
  category now, but it also means 13 of 21 cases (7 selection, 4 arguments, 2 restraint) cost API
  budget and contribute nothing to ranking as currently written.
- Wilson intervals describe sampling uncertainty at fixed `temperature=0` output —
  they say nothing about whether a differently-phrased prompt or a different day's
  model snapshot would reproduce the same numbers. Repeats answer "is this noise
  within a run," not "is this stable across prompt variations or model updates."
