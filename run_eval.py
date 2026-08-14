#!/usr/bin/env python3
"""Benchmark MCP-style tool-calling reliability across OpenAI-compatible models.

Loads the case suite, runs every model against every case config.RUNS_PER_CASE times,
scores each run, and writes results.csv and results.md as pass rates (k/n).
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone

import config
from client import ApiError, chat, parse_tool_calls
from scoring import score_chain, score_no_call, score_refusal, score_single_call


def load_suite(path):
    """Read cases.json. Returns (tool_registry, cases)."""
    with open(path) as f:
        suite = json.load(f)
    return suite["tools"], suite["cases"]


def run_chain(model, case, tools):
    """Drive a multi-turn tool loop with canned tool results; return calls in order."""
    messages = [{"role": "user", "content": case["prompt"]}]
    observed = []
    canned = case.get("tool_results", {})

    for _ in range(config.MAX_CHAIN_STEPS):
        message = chat(model, messages, tools)
        calls = parse_tool_calls(message)
        if not calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": message["tool_calls"],
            }
        )
        for raw, (name, call_args) in zip(message["tool_calls"], calls, strict=True):
            observed.append((name, call_args))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": raw.get("id", ""),
                    "content": canned.get(name, "{}"),
                }
            )
        time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    return observed


def run_case(model, case, tool_registry) -> tuple[bool, str]:
    """Run one case against one model and score it."""
    tools = [tool_registry[name] for name in case["available_tools"]]

    if case["type"] == "multi_call_chain":
        observed = run_chain(model, case, tools)
        return score_chain(observed, case["expected_tool_call"]["sequence"])

    message = chat(model, [{"role": "user", "content": case["prompt"]}], tools)
    calls = parse_tool_calls(message)

    if case["type"] == "single_call":
        return score_single_call(calls, case["expected_tool_call"])
    if case["type"] == "should_refuse":
        return score_refusal(calls, message, case["expected_refusal"])
    if case["type"] == "no_call":
        return score_no_call(calls, message)
    return False, f"unknown case type: {case['type']}"


def summarize_runs(results) -> tuple[int, int, str]:
    """Roll repeated (passed, note) runs of one case up into (passed, total, notes).

    Notes come from the failing runs only, deduped in first-seen order: three runs
    failing the same way should read once, and a run that failed differently must not
    be overwritten by whichever run happened to go last.
    """
    passed = sum(1 for ok, _ in results if ok)
    failures = list(dict.fromkeys(note for ok, note in results if not ok))
    return passed, len(results), "; ".join(failures) if failures else "ok"


def run_case_repeated(model, case, tool_registry, runs) -> tuple[int, int, str]:
    """Run one case `runs` times. A failed run is scored as a fail, never fatal."""
    results = []
    for index in range(runs):
        try:
            results.append(run_case(model, case, tool_registry))
        except ApiError as exc:
            results.append((False, f"api error: {exc}"))
        except Exception as exc:  # a broken case must not kill the sweep
            results.append((False, f"{type(exc).__name__}: {exc}"))
        if index < runs - 1:
            time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)
    return summarize_runs(results)


CSV_COLUMNS = [
    "model",
    "case_id",
    "category",
    "case_type",
    "runs_passed",
    "runs_total",
    "notes",
]


def write_csv(rows, path):
    """One row per (model, case) — the machine-readable output."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _tally(rows):
    """(passed runs, total runs) for a group of result rows — runs, not cases."""
    return sum(r["runs_passed"] for r in rows), sum(r["runs_total"] for r in rows)


def _fraction(passed, total):
    return f"{passed}/{total} ({passed / total:.0%})" if total else "—"


def write_markdown(rows, models, cases, runs, path):
    """Run metadata, per-category pass rates, a result matrix, and every failure note.

    Every rate is passed runs over total runs, so a case that passes 2 of 3 counts as
    two thirds of a case rather than rounding to a pass or a fail.
    """
    categories = list(dict.fromkeys(c["category"] for c in cases))
    lookup = {(r["model"], r["case_id"]): r for r in rows}
    lines = []

    lines += [
        "# MCP tool-calling benchmark",
        "",
        f"- **Run:** {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        f"- **Endpoint:** `{config.API_BASE_URL}`",
        f"- **Cases:** {len(cases)} across {len(categories)} categories",
        f"- **Models:** {len(models)}",
        f"- **Runs per case:** {runs}",
        "",
        "## Pass rate",
        "",
        "| model | overall | " + " | ".join(categories) + " |",
        "|---|---|" + "---|" * len(categories),
    ]
    for model in models:
        model_rows = [r for r in rows if r["model"] == model]
        cells = [_fraction(*_tally(model_rows))]
        for category in categories:
            cells.append(_fraction(*_tally([r for r in model_rows if r["category"] == category])))
        lines.append(f"| `{model}` | " + " | ".join(cells) + " |")

    lines += ["", "## Results", ""]
    for category in categories:
        lines += [
            f"### {category}",
            "",
            "| case | type | " + " | ".join(models) + " |",
            "|---|---|" + "---|" * len(models),
        ]
        for case in (c for c in cases if c["category"] == category):
            cells = []
            for model in models:
                row = lookup.get((model, case["id"]))
                if row is None:
                    cells.append("—")
                    continue
                cell = f"{row['runs_passed']}/{row['runs_total']}"
                cells.append(cell if row["runs_passed"] == row["runs_total"] else f"**{cell}**")
            lines.append(f"| {case['id']} | {case['type']} | " + " | ".join(cells) + " |")
        lines.append("")

    lines += ["## Failure notes", ""]
    failures = [r for r in rows if r["runs_passed"] < r["runs_total"]]
    if not failures:
        lines.append("None.")
    for row in failures:
        lines.append(
            f"- `{row['model']}` / `{row['case_id']}` "
            f"({row['runs_passed']}/{row['runs_total']}): {row['notes']}"
        )

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", help="override the model list in config")
    parser.add_argument("--cases", nargs="+", help="only run these case ids")
    parser.add_argument(
        "--runs",
        type=int,
        help=f"runs per (model, case); overrides RUNS_PER_CASE={config.RUNS_PER_CASE}",
    )
    args = parser.parse_args()

    runs = args.runs if args.runs is not None else config.RUNS_PER_CASE
    if runs < 1:
        sys.exit("--runs must be at least 1")

    if not config.API_KEY and "localhost" not in config.API_BASE_URL:
        sys.exit("GROQ_API_KEY is not set — copy .env.example to .env and fill it in.")

    tool_registry, all_cases = load_suite(config.CASES_PATH)
    cases = all_cases
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in all_cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            sys.exit(f"unknown case id(s): {', '.join(sorted(missing))}")

    models = args.models or config.MODELS
    rows = []

    for model in models:
        print(f"\n=== {model} ({runs} run(s) per case) ===")
        for case in cases:
            passed, total, notes = run_case_repeated(model, case, tool_registry, runs)

            print(f"  [{passed}/{total}] {case['id']:<24} {notes}")
            rows.append(
                {
                    "model": model,
                    "case_id": case["id"],
                    "category": case["category"],
                    "case_type": case["type"],
                    "runs_passed": passed,
                    "runs_total": total,
                    "notes": notes,
                }
            )
            time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

    write_csv(rows, config.RESULTS_CSV_PATH)
    write_markdown(rows, models, cases, runs, config.RESULTS_MD_PATH)
    print(f"\nWrote {config.RESULTS_CSV_PATH} and {config.RESULTS_MD_PATH}")


if __name__ == "__main__":
    main()
