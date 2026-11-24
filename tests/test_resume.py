"""The per-run journal and --resume.

A sweep on a CPU laptop takes hours, so an interrupt must not cost the finished runs.
`main` goes through the localhost fake Ollama (conftest.py); journal and row-building
logic are tested as plain functions.
"""

import copy
import csv
import glob
import json
from datetime import datetime, timezone

import pytest

import config
import journal
import run_eval
from client import ModelInfo
from run_eval import build_rows, main

CASE = "restraint_general_question"
OTHER = "restraint_explicit_negation"


def read_rows(directory):
    with open(directory / "results.csv", newline="") as f:
        return list(csv.DictReader(f))


def live_journal(fake):
    return fake.tmp_path / "results.journal.jsonl"


def archives(fake):
    return sorted(glob.glob(str(fake.tmp_path / "results.journal.*.jsonl")))


def make_record(main_suite, case_id=CASE, run_index=0, model="m4b", **overrides):
    """A journal record as `main` would write it, keyed to the current config."""
    case = next(c for c in main_suite["cases"] if c["id"] == case_id)
    tools = [main_suite["tools"][name] for name in case["available_tools"]]
    record = {
        "backend": config.BACKEND,
        "endpoint": config.API_BASE_URL,
        "model": model,
        "quantization": "Q4_K_M",
        "case_id": case_id,
        "case_hash": journal.case_fingerprint(case, tools),
        "run_index": run_index,
        "passed": True,
        "note": "ok",
        "context_length": 16384,
        "timestamp": "2026-11-23T10:00:00+00:00",
    }
    record.update(overrides)
    return record


def seed(fake, main_suite, runs=3, **kwargs):
    for index in range(runs):
        journal.append(str(live_journal(fake)), make_record(main_suite, run_index=index, **kwargs))


# --- journal units -----------------------------------------------------------


def test_journal_ignores_a_torn_final_line(tmp_path):
    path = tmp_path / "j.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2}\n{"backend": "oll')

    assert journal.load(str(path)) == [{"a": 1}, {"a": 2}]


def test_journal_load_of_a_missing_file_is_empty(tmp_path):
    assert journal.load(str(tmp_path / "nope.jsonl")) == []


def test_fingerprint_changes_with_the_case_and_with_its_tools(main_suite):
    case = next(c for c in main_suite["cases"] if c["id"] == CASE)
    tools = [main_suite["tools"][n] for n in case["available_tools"]]
    base = journal.case_fingerprint(case, tools)

    assert base == journal.case_fingerprint(copy.deepcopy(case), copy.deepcopy(tools))
    assert base != journal.case_fingerprint({**case, "prompt": "something else"}, tools)
    assert base != journal.case_fingerprint(case, tools[:-1])


def test_archive_never_overwrites_an_earlier_archive(tmp_path):
    now = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)
    path = tmp_path / "results.journal.jsonl"
    names = []
    for _ in range(2):
        path.write_text("{}\n")
        names.append(journal.archive(str(path), now))

    assert len(set(names)) == 2 and all(n.endswith(".jsonl") for n in names)
    assert "20261123T120000Z" in names[0]
    assert not path.exists()
    assert journal.archive(str(path), now) == ""  # nothing to archive: no-op


# --- build_rows --------------------------------------------------------------


def rows_for(main_suite, records, runs=3, infos=None):
    cases = [c for c in main_suite["cases"] if c["id"] == CASE]
    return build_rows(
        records,
        ["m4b"],
        cases,
        main_suite["tools"],
        runs,
        infos or {"m4b": ModelInfo("Q4_K_M", True)},
    )


@pytest.mark.parametrize(
    ("contexts", "expected"),
    [
        ([8192, 16384, 16384], "mixed"),
        ([16384, None, 16384], 16384),
        ([None, None, None], ""),
    ],
)
def test_row_context_length_is_mixed_when_runs_disagree(main_suite, contexts, expected):
    records = [
        make_record(main_suite, run_index=i, context_length=c) for i, c in enumerate(contexts)
    ]

    (row,) = rows_for(main_suite, records)
    assert row["context_length"] == expected


def test_row_run_timestamp_is_earliest_run(main_suite):
    times = ["2026-11-23T10:05:00+00:00", "2026-11-23T10:01:00+00:00", "2026-11-23T10:09:00+00:00"]
    records = [make_record(main_suite, run_index=i, timestamp=t) for i, t in enumerate(times)]

    (row,) = rows_for(main_suite, records)
    assert row["run_timestamp"] == "2026-11-23T10:01:00+00:00"


def test_build_rows_ignores_records_beyond_the_requested_runs(main_suite):
    records = [make_record(main_suite, run_index=i) for i in range(3)]

    (row,) = rows_for(main_suite, records, runs=2)
    assert (row["runs_passed"], row["runs_total"]) == (2, 2)


@pytest.mark.parametrize(
    "overrides",
    [
        {"quantization": "Q8_0"},
        {"endpoint": "http://another-host:11434/v1"},
        {"backend": "some-other-backend"},
        {"case_hash": "0" * 16},
    ],
)
def test_build_rows_ignores_records_from_other_conditions(main_suite, overrides):
    records = [make_record(main_suite, run_index=i, **overrides) for i in range(3)]

    (row,) = rows_for(main_suite, records)
    assert row["runs_total"] == 0


# --- main: resume ------------------------------------------------------------


def test_interrupted_sweep_resumes_without_rerunning_completed_runs(
    fake_ollama, monkeypatch, tmp_path
):
    argv = ["--models", "m4b", "--cases", CASE, OTHER, "--runs", "3"]
    real_append = journal.append
    appended = []

    def interrupt_after_fourth(path, record):
        real_append(path, record)
        appended.append(record)
        if len(appended) == 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(journal, "append", interrupt_after_fourth)
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert "--resume" in str(exc.value)
    assert len(fake_ollama.chat_requests) == 4

    monkeypatch.setattr(journal, "append", real_append)
    main([*argv, "--resume"])
    assert len(fake_ollama.chat_requests) == 6  # 4 + the 2 that were missing
    resumed = read_rows(fake_ollama.tmp_path)
    assert [(r["runs_passed"], r["runs_total"]) for r in resumed] == [("3", "3"), ("3", "3")]

    # An uninterrupted control sweep, writing to its own directory, must agree on every
    # column but the timestamp.
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    monkeypatch.setattr(config, "RESULTS_CSV_PATH", str(control_dir / "results.csv"))
    monkeypatch.setattr(config, "RESULTS_MD_PATH", str(control_dir / "results.md"))
    monkeypatch.setattr(config, "JOURNAL_PATH", str(control_dir / "results.journal.jsonl"))
    main(argv)
    control = read_rows(control_dir)

    def comparable(rows):
        return [{k: v for k, v in r.items() if k != "run_timestamp"} for r in rows]

    assert comparable(resumed) == comparable(control)


def test_nonempty_journal_without_resume_aborts_and_leaves_it_untouched(fake_ollama, main_suite):
    seed(fake_ollama, main_suite, runs=2)
    before = live_journal(fake_ollama).read_bytes()

    with pytest.raises(SystemExit) as exc:
        main(["--models", "m4b", "--cases", CASE, "--runs", "2"])

    assert "--resume" in str(exc.value)
    assert live_journal(fake_ollama).read_bytes() == before
    assert fake_ollama.chat_requests == []


def test_resume_reruns_runs_whose_case_changed(fake_ollama, main_suite, monkeypatch):
    seed(fake_ollama, main_suite, runs=3)  # journaled against the original prompt
    changed = copy.deepcopy(main_suite)
    for case in changed["cases"]:
        if case["id"] == CASE:
            case["prompt"] = "A different question, in one sentence?"
    monkeypatch.setattr(run_eval, "load_suite", lambda path: (changed["tools"], changed["cases"]))

    main(["--models", "m4b", "--cases", CASE, "--runs", "3", "--resume"])

    assert len(fake_ollama.chat_requests) == 3
    (row,) = read_rows(fake_ollama.tmp_path)
    assert row["runs_total"] == "3"  # the three stale records were not counted on top


@pytest.mark.parametrize(
    "seeded",
    [{"quantization": "Q8_0"}, {"endpoint": "http://another-host:11434/v1"}],
    ids=["quantization", "endpoint"],
)
def test_resume_reruns_when_quantization_or_endpoint_differs(fake_ollama, main_suite, seeded):
    seed(fake_ollama, main_suite, runs=2, **seeded)

    main(["--models", "m4b", "--cases", CASE, "--runs", "2", "--resume"])

    assert len(fake_ollama.chat_requests) == 2
    (row,) = read_rows(fake_ollama.tmp_path)
    assert (row["runs_passed"], row["runs_total"], row["quantization"]) == ("2", "2", "Q4_K_M")


def test_resume_with_fewer_runs_ignores_extra_journaled_runs(fake_ollama, main_suite):
    seed(fake_ollama, main_suite, runs=3)

    main(["--models", "m4b", "--cases", CASE, "--runs", "2", "--resume"])

    assert fake_ollama.chat_requests == []
    (row,) = read_rows(fake_ollama.tmp_path)
    assert (row["runs_passed"], row["runs_total"]) == ("2", "2")


def test_two_consecutive_sweeps_without_resume_both_run(fake_ollama):
    argv = ["--models", "m4b", "--cases", CASE, "--runs", "2"]

    main(argv)
    main(argv)

    assert len(fake_ollama.chat_requests) == 4
    assert not live_journal(fake_ollama).exists()
    assert len(archives(fake_ollama)) == 2


def test_server_stop_mid_sweep_aborts_and_resume_finishes(fake_ollama, monkeypatch):
    argv = ["--models", "m4b", "--cases", CASE, "--runs", "4"]
    real_append = journal.append
    appended = []

    def stop_server_after_second(path, record):
        real_append(path, record)
        appended.append(record)
        if len(appended) == 2:
            fake_ollama.stop()

    monkeypatch.setattr(journal, "append", stop_server_after_second)
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert "--resume" in str(exc.value) and "unreachable" in str(exc.value)

    records = journal.load(str(live_journal(fake_ollama)))
    assert [r["run_index"] for r in records] == [
        0,
        1,
    ]  # the run that hit the dead server is not here
    assert not any(r["note"].startswith("api error") for r in records)

    fake_ollama.restart()
    monkeypatch.setattr(journal, "append", real_append)
    main([*argv, "--resume"])

    (row,) = read_rows(fake_ollama.tmp_path)
    assert (row["runs_passed"], row["runs_total"]) == ("4", "4")


def test_failed_run_is_journaled_as_a_failure_not_dropped(fake_ollama):
    fake_ollama.chat_status = 500

    main(["--models", "m4b", "--cases", CASE, "--runs", "2"])

    (row,) = read_rows(fake_ollama.tmp_path)
    assert (row["status"], row["runs_passed"], row["runs_total"]) == ("ok", "0", "2")
    (archive,) = archives(fake_ollama)
    with open(archive) as f:
        records = [json.loads(line) for line in f]
    assert len(records) == 2
    assert all(r["passed"] is False and r["note"].startswith("api error:") for r in records)


def test_crash_while_writing_results_leaves_the_journal_for_resume(fake_ollama, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(run_eval, "write_markdown", boom)
    with pytest.raises(RuntimeError):
        main(["--models", "m4b", "--cases", CASE, "--runs", "2"])

    assert len(journal.load(str(live_journal(fake_ollama)))) == 2
    assert archives(fake_ollama) == []
