#!/usr/bin/env python3
"""Measure prompt-processing and generation throughput (tokens/second) for a local model.

Uses Ollama's native /api/chat timings, so it needs BACKEND=ollama. A tool-calling
prompt is mostly tool schemas, so the case's real prompt and tools are sent, not a toy
string: prompt tok/s on a CPU is the number that decides how long a sweep takes.
"""

import argparse
import statistics
import sys
import uuid

import config
from client import ApiError, chat_stats
from run_eval import case_tools, load_suite


def with_nonce(messages, nonce):
    """Prepend a system turn unique to this call.

    Ollama caches the evaluated prefix of the previous prompt, so an identical repeat
    would report a tiny prompt-eval count and an inflated prompt tok/s. The nonce comes
    first (the tools block renders after the system turn) so the prefix differs on every
    repeat. The input list is not modified.
    """
    return [{"role": "system", "content": f"run {nonce}"}, *messages]


def _spread(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def summarize_throughput(samples):
    """Median, min and max tok/s over repeats, plus a cache-hit warning.

    Median, not mean: one slow repeat (the OS paging, another process) shouldn't move
    the headline. Samples with a zero duration can't give a rate and are dropped and
    counted; if none are left there is nothing to report.
    """
    usable = [s for s in samples if s.prompt_seconds > 0 and s.generation_seconds > 0]
    if not usable:
        raise ValueError("no usable samples: every repeat reported a zero duration")

    prompt_tokens = [s.prompt_tokens for s in usable]
    return {
        "n": len(usable),
        "dropped": len(samples) - len(usable),
        "prompt_tok_s": _spread([s.prompt_tokens / s.prompt_seconds for s in usable]),
        "generation_tok_s": _spread([s.generated_tokens / s.generation_seconds for s in usable]),
        "prompt_tokens": {"min": min(prompt_tokens), "max": max(prompt_tokens)},
        # A prefix-cache hit evaluates fewer prompt tokens; the nonce should prevent it.
        "cache_suspect": min(prompt_tokens) < 0.9 * max(prompt_tokens),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--case", default="chaining_three_steps", help="case whose prompt to use")
    parser.add_argument("--num-predict", type=int, default=128, help="max tokens to generate")
    args = parser.parse_args(argv)

    if config.NATIVE_API is None:
        sys.exit("throughput.py needs a backend with a native API: set BACKEND=ollama")
    if args.repeats < 1:
        sys.exit("--repeats must be at least 1")

    tool_registry, cases = load_suite(config.CASES_PATH)
    case = next((c for c in cases if c["id"] == args.case), None)
    if case is None:
        sys.exit(f"unknown case id: {args.case}")
    messages = [{"role": "user", "content": case["prompt"]}]
    tools = case_tools(case, tool_registry)

    def measure():
        return chat_stats(
            args.model, with_nonce(messages, uuid.uuid4().hex[:8]), tools, args.num_predict
        )

    try:
        print(f"warming up {args.model} (loads the model; this call is discarded)...")
        measure()
        samples = []
        for i in range(args.repeats):
            samples.append(measure())
            print(f"  repeat {i + 1}/{args.repeats} done")
    except ApiError as exc:
        sys.exit(f"throughput run failed: {exc}")

    summary = summarize_throughput(samples)
    prompt, gen, ptok = (
        summary["prompt_tok_s"],
        summary["generation_tok_s"],
        summary["prompt_tokens"],
    )
    print()
    print("| model | prompt tok/s median [min–max] | generation tok/s median [min–max] |")
    print("|---|---|---|")
    print(
        f"| `{args.model}` "
        f"| {prompt['median']:.1f} [{prompt['min']:.1f}–{prompt['max']:.1f}] "
        f"| {gen['median']:.1f} [{gen['min']:.1f}–{gen['max']:.1f}] |"
    )
    print(
        f"\nn={summary['n']} (dropped {summary['dropped']}), case={args.case}, "
        f"num_predict={args.num_predict}, prompt_tokens={ptok['min']}–{ptok['max']}, "
        f"cache_suspect={summary['cache_suspect']}"
    )


if __name__ == "__main__":
    main()
