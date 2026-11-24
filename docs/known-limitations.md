# Known limitations

Back to the [README](../README.md).


See **Scope** in the README for the three big ones (no MCP protocol beyond a stdio slice,
self-hosting only run as one 1-run sweep of the 20-case suite per model on one machine
under Ollama — see [local-ollama.md](local-ollama.md) — and not a certification). They
still hold for the 20-case sweep. Its 6 server-backed cases (plus the 3 in
`mcp_smoke.json`) cover stdio `tools/list` / `tools/call` against two pinned reference
servers (filesystem, sqlite) and nothing else of the protocol (no HTTP/SSE, resources,
prompts or auth); the other 14 cases are canned. The rest, specific to the current
20-case suite:

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
- `selection`, `arguments`, and `restraint` were saturated — every model tested cleared
  them completely across 3 replicated runs in the last full sweep, which ran the old
  21-case suite (13 of 21 cases). SOL-103 cut them to 6 sanity cases (2 each) and added
  `server_chaining`, `registry` and `collision`. `results.md` labels a saturated
  category explicitly. Whether the new categories discriminate is recorded per category
  in [docs/cases.md](cases.md#groq-results-for-the-new-categories) once a Groq sweep of
  the 20-case suite has run; none has yet. The only data so far is the
  local Ollama CPU run ([local-ollama.md](local-ollama.md), 1 run per case): both Qwen3
  models passed every `registry` and `collision` run (2/2 each, `34%–100%`) and went 1/2
  on `server_chaining` (`9%–91%`), which is too little to call either way.
- Wilson intervals describe sampling uncertainty at fixed `temperature=0` output —
  they say nothing about whether a differently-phrased prompt or a different day's
  model snapshot would reproduce the same numbers. Repeats answer "is this noise
  within a run," not "is this stable across prompt variations or model updates."
