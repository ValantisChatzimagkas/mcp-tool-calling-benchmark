#!/usr/bin/env python3
"""Benchmark MCP-style tool-calling reliability across OpenAI-compatible models.

Loads the case suite, runs every model against every case config.RUNS_PER_CASE times,
scores each run, and writes results.csv and results.md as pass rates (k/n).
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import config
import journal
from client import (
    ApiError,
    ServerUnreachable,
    chat,
    describe_model,
    loaded_context_length,
    parse_tool_calls,
)
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


def case_tools(case, tool_registry):
    """The tool schemas a case actually sends to the model."""
    return [tool_registry[name] for name in case["available_tools"]]


def run_case(model, case, tool_registry) -> tuple[bool, str]:
    """Run one case against one model and score it."""
    tools = case_tools(case, tool_registry)

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


def run_once(model, case, tool_registry) -> tuple[bool, str]:
    """One run of one case. A failed run is scored as a fail, never fatal.

    The exception is a dead server (ServerUnreachable): that says nothing about the
    model, so it propagates and stops the sweep instead of being recorded as a failure.
    """
    try:
        return run_case(model, case, tool_registry)
    except ServerUnreachable:
        raise
    except ApiError as exc:
        return False, f"api error: {exc}"
    except Exception as exc:  # a broken case must not kill the sweep
        return False, f"{type(exc).__name__}: {exc}"


CSV_COLUMNS = [
    "model",
    "backend",
    "endpoint",
    "quantization",
    "context_length",
    "run_timestamp",
    "case_id",
    "category",
    "case_type",
    "status",
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


UNSUPPORTED_NOTE = "chat template has no tool support"


def unsupported_rows(model, info, cases, run_timestamp=""):
    """One row per case for a model that can't be tested: zero runs, not zero passes.

    A model whose chat template has no tool support never reached the scorer, so
    `0/3` would blame its tool-calling for something the server refused up front.
    `runs_total=0` keeps these rows out of every rollup by construction.
    """
    return [
        {
            "model": model,
            "backend": config.BACKEND,
            "endpoint": config.API_BASE_URL,
            "quantization": info.quantization,
            "context_length": "",
            "run_timestamp": run_timestamp,
            "case_id": case["id"],
            "category": case["category"],
            "case_type": case["type"],
            "status": "unsupported",
            "runs_passed": 0,
            "runs_total": 0,
            "notes": UNSUPPORTED_NOTE,
        }
        for case in cases
    ]


def matching_records(records, model, quantization, case, tools, runs):
    """The journaled runs that count for one (model, case) under the current conditions.

    A record counts only if backend, endpoint, quantization and the case's fingerprint
    all match, and its run_index is within the requested runs — one record per index, in
    run order. Anything else (another host, another quantization, an edited case, a run
    beyond --runs) is stale and ignored rather than merged into this row.
    """
    case_hash = journal.case_fingerprint(case, tools)
    # Same key as the run being asked for, bar the run index.
    wanted = journal.run_key(
        config.BACKEND, config.API_BASE_URL, model, quantization, case["id"], case_hash, 0
    )[:-1]
    by_index = {}
    for r in records:
        if journal.record_key(r)[:-1] == wanted and r["run_index"] < runs:
            by_index.setdefault(r["run_index"], r)
    return [by_index[i] for i in sorted(by_index)]


def build_rows(records, models, cases, tool_registry, runs, infos, unsupported_timestamp=""):
    """Rebuild one result row per (model, case) from the journal. Pure.

    Rows come from the journal rather than from memory, so a resumed sweep and an
    uninterrupted one produce the same results.csv (bar run_timestamp, which is the
    earliest run in the row — a resumed sweep spans sessions).
    """
    rows = []
    for model in models:
        info = infos[model]
        if not info.supports_tools:
            rows += unsupported_rows(model, info, cases, unsupported_timestamp)
            continue
        for case in cases:
            tools = case_tools(case, tool_registry)
            mine = matching_records(records, model, info.quantization, case, tools, runs)
            passed, total, notes = summarize_runs([(r["passed"], r["note"]) for r in mine])
            contexts = {r["context_length"] for r in mine if r["context_length"] is not None}
            context = "mixed" if len(contexts) > 1 else next(iter(contexts), "")
            rows.append(
                {
                    "model": model,
                    "backend": config.BACKEND,
                    "endpoint": config.API_BASE_URL,
                    "quantization": info.quantization,
                    "context_length": context,
                    "run_timestamp": min((r["timestamp"] for r in mine), default=""),
                    "case_id": case["id"],
                    "category": case["category"],
                    "case_type": case["type"],
                    "status": "ok",
                    "runs_passed": passed,
                    "runs_total": total,
                    "notes": notes,
                }
            )
    return rows


def _is_unsupported(model_rows):
    return bool(model_rows) and all(r["status"] == "unsupported" for r in model_rows)


def _tally(rows):
    """(passed runs, total runs) for a group of result rows — runs, not cases."""
    return sum(r["runs_passed"] for r in rows), sum(r["runs_total"] for r in rows)


def wilson_interval(passed, total, z=1.96) -> tuple[float, float]:
    """95%-by-default Wilson score interval for a binomial proportion.

    Plain k/n overstates confidence at small n: 3/3 reads as "100% reliable", but
    the actual 95% interval on 3 runs is roughly 44-100%. Wilson (not the naive
    normal-approximation interval) stays well-behaved at p=0 or p=1, which is
    exactly where most cells in this report sit.
    """
    if total == 0:
        return 0.0, 0.0
    p = passed / total
    denom = 1 + z**2 / total
    center = p + z**2 / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    low = (center - margin) / denom
    high = (center + margin) / denom
    return max(0.0, low), min(1.0, high)


def _fraction(passed, total):
    if not total:
        return "—"
    lo, hi = wilson_interval(passed, total)
    return f"{passed}/{total} ({passed / total:.0%}, {lo:.0%}–{hi:.0%} CI)"


def _cell(passed, total):
    """One result-matrix cell: k/n plus its CI, bolded when not a clean pass."""
    lo, hi = wilson_interval(passed, total)
    text = f"{passed}/{total} ({lo:.0%}–{hi:.0%})"
    return text if passed == total else f"**{text}**"


# Ground-truth or scoring caveats worth surfacing next to a category's results,
# rather than leaving the reader to infer them from the numbers alone.
CATEGORY_NOTES = {
    "refusal": (
        "Scored by `must_mention` keyword lists in cases.json, calibrated against "
        "responses actually observed from the 3 Groq-hosted models in README's Key "
        "findings — not a systematic process. "
        "A correct refusal phrased unusually by an untested model could false-fail here."
    ),
}


def write_markdown(rows, models, cases, runs, run_started, path):
    """Run metadata, per-category pass rates, a result matrix, and every failure note.

    Every rate is passed runs over total runs, so a case that passes 2 of 3 counts as
    two thirds of a case rather than rounding to a pass or a fail.

    The `## Models` table says what each model tag was actually served as (quantization,
    context window), read from the rows so it can't disagree with results.csv.

    `run_started` must be the same timestamp written into results.csv's rows — one
    run, one timestamp, so the two files can't disagree about when this happened.
    """
    categories = list(dict.fromkeys(c["category"] for c in cases))
    lookup = {(r["model"], r["case_id"]): r for r in rows}
    unsupported = {m for m in models if _is_unsupported([r for r in rows if r["model"] == m])}
    lines = []

    lines += [
        "# MCP tool-calling benchmark",
        "",
        f"- **Run:** {run_started:%Y-%m-%d %H:%M UTC}",
        f"- **Backend:** `{config.BACKEND}`",
        f"- **Endpoint:** `{config.API_BASE_URL}`",
        f"- **Cases:** {len(cases)} across {len(categories)} categories",
        f"- **Models:** {len(models)}"
        + (f" ({len(unsupported)} unsupported)" if unsupported else ""),
        f"- **Runs per case:** {runs}",
        "- **Confidence:** 95% Wilson score intervals in parentheses. At "
        f"{runs} run(s) per case these are wide by nature — a clean k/n is not "
        "proof of reliability, it's a sample. See README's Scope section before "
        "treating any single number here as a verdict.",
        "",
        "## Models",
        "",
        "| model | backend | quantization | context_length |",
        "|---|---|---|---|",
    ]
    for model in models:
        model_rows = [r for r in rows if r["model"] == model]
        context = next((r["context_length"] for r in model_rows if r["context_length"]), "")
        quantization = model_rows[0]["quantization"] if model_rows else "unknown"
        lines.append(f"| `{model}` | {config.BACKEND} | {quantization} | {context or '—'} |")
    lines += [
        "",
        "## Pass rate",
        "",
        "| model | overall | " + " | ".join(categories) + " |",
        "|---|---|" + "---|" * len(categories),
    ]
    for model in models:
        model_rows = [r for r in rows if r["model"] == model]
        if model in unsupported:
            cells = ["unsupported"] * (len(categories) + 1)
        else:
            cells = [_fraction(*_tally(model_rows))]
            for category in categories:
                cells.append(
                    _fraction(*_tally([r for r in model_rows if r["category"] == category]))
                )
        lines.append(f"| `{model}` | " + " | ".join(cells) + " |")

    lines += ["", "## Results", ""]
    for category in categories:
        lines += [f"### {category}", ""]
        if category in CATEGORY_NOTES:
            lines += [CATEGORY_NOTES[category], ""]

        cat_rows = [r for r in rows if r["category"] == category]
        per_model = {
            m: _tally([r for r in cat_rows if r["model"] == m])
            for m in models
            if m not in unsupported
        }
        # Unsupported models were never tested, so they neither block nor earn saturation.
        saturated = bool(per_model) and all(p == t and t > 0 for p, t in per_model.values())
        if saturated:
            total_runs = next(iter(per_model.values()))[1]
            lines += [
                f"_Every model passed all {total_runs} runs in this category — "
                "saturated, contributes no discriminating evidence._",
                "",
            ]

        lines += [
            "| case | type | " + " | ".join(models) + " |",
            "|---|---|" + "---|" * len(models),
        ]
        for case in (c for c in cases if c["category"] == category):
            cells = []
            for model in models:
                row = lookup.get((model, case["id"]))
                if row is None:
                    cells.append("—")
                elif row["status"] == "unsupported":
                    cells.append("unsupported")
                else:
                    cells.append(_cell(row["runs_passed"], row["runs_total"]))
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", help="override the model list in config")
    parser.add_argument("--cases", nargs="+", help="only run these case ids")
    parser.add_argument(
        "--runs",
        type=int,
        help=f"runs per (model, case); overrides RUNS_PER_CASE={config.RUNS_PER_CASE}",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted sweep from its journal instead of refusing to start",
    )
    args = parser.parse_args(argv)

    runs = args.runs if args.runs is not None else config.RUNS_PER_CASE
    if runs < 1:
        sys.exit("--runs must be at least 1")

    if config.REQUIRES_API_KEY and not config.API_KEY:
        sys.exit(
            "GROQ_API_KEY is not set — copy .env.example to .env and fill it in. "
            "For a local Ollama server set BACKEND=ollama instead."
        )

    # A live journal only exists after an interrupted sweep (a finished one is archived).
    # Silently reusing it would fold old runs into a sweep that was meant to be fresh.
    journal_path = config.JOURNAL_PATH
    if not args.resume and os.path.exists(journal_path) and os.path.getsize(journal_path) > 0:
        sys.exit(f"journal exists; pass --resume to continue it, or delete {journal_path}")

    tool_registry, all_cases = load_suite(config.CASES_PATH)
    cases = all_cases
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in all_cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            sys.exit(f"unknown case id(s): {', '.join(sorted(missing))}")

    models = args.models or config.MODELS

    # Ask the server about every model before running any case: an unpulled model or a
    # server that is down should stop the sweep now, not after an hour of other work.
    infos = {}
    for model in models:
        try:
            infos[model] = describe_model(model)
        except ApiError as exc:
            sys.exit(f"cannot describe model {model}: {exc}")

    records = journal.load(journal_path) if args.resume else []
    done = {journal.record_key(r) for r in records}
    context_lengths = {}
    started = datetime.now(timezone.utc)

    try:
        for model in models:
            info = infos[model]
            if not info.supports_tools:
                print(f"\n=== {model}: unsupported ({UNSUPPORTED_NOTE}) — skipped ===")
                continue

            print(f"\n=== {model} ({runs} run(s) per case) ===")
            for case in cases:
                tools = case_tools(case, tool_registry)
                case_hash = journal.case_fingerprint(case, tools)
                for run_index in range(runs):
                    key = journal.run_key(
                        config.BACKEND,
                        config.API_BASE_URL,
                        model,
                        info.quantization,
                        case["id"],
                        case_hash,
                        run_index,
                    )
                    if key in done:
                        continue

                    passed, note = run_once(model, case, tool_registry)

                    # The model is loaded by now, so /api/ps can say what context it got.
                    if context_lengths.get(model) is None:
                        context_lengths[model] = loaded_context_length(model)

                    record = {
                        "backend": config.BACKEND,
                        "endpoint": config.API_BASE_URL,
                        "model": model,
                        "quantization": info.quantization,
                        "case_id": case["id"],
                        "case_hash": case_hash,
                        "run_index": run_index,
                        "passed": passed,
                        "note": note,
                        "context_length": context_lengths[model],
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    journal.append(journal_path, record)
                    records.append(record)
                    time.sleep(config.DELAY_BETWEEN_CALLS_SECONDS)

                mine = matching_records(records, model, info.quantization, case, tools, runs)
                p, t, notes = summarize_runs([(r["passed"], r["note"]) for r in mine])
                print(f"  [{p}/{t}] {case['id']:<24} {notes}")
    except ServerUnreachable as exc:
        sys.exit(f"server unreachable ({exc}); rerun with --resume")
    except KeyboardInterrupt:
        sys.exit("interrupted; completed runs are journaled — rerun with --resume")

    rows = build_rows(
        records,
        models,
        cases,
        tool_registry,
        runs,
        infos,
        unsupported_timestamp=started.isoformat(timespec="seconds"),
    )
    stamps = [r["run_timestamp"] for r in rows if r["run_timestamp"]]
    run_started = datetime.fromisoformat(min(stamps)) if stamps else started

    write_csv(rows, config.RESULTS_CSV_PATH)
    write_markdown(rows, models, cases, runs, run_started, config.RESULTS_MD_PATH)
    print(f"\nWrote {config.RESULTS_CSV_PATH} and {config.RESULTS_MD_PATH}")

    # Only after both result files exist: a crash above leaves the journal for --resume.
    archived = journal.archive(journal_path, datetime.now(timezone.utc))
    if archived:
        print(f"Archived the run journal to {archived}")


if __name__ == "__main__":
    main()
