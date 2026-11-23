"""run_eval.main end to end against a localhost fake Ollama server.

The seam is the HTTP boundary: main -> client -> fake server. No outside network, so
this still runs offline in CI. `fake_ollama` (conftest.py) points config at the fake and
sends every output file into tmp_path.
"""

import csv

import pytest

import config
from client import ModelInfo, describe_model
from run_eval import main


def read_rows(fake):
    with open(fake.tmp_path / "results.csv", newline="") as f:
        return list(csv.DictReader(f))


# --- profiles ----------------------------------------------------------------


def test_ollama_profile_needs_no_api_key_and_zero_delay():
    ollama = config.resolve_profile("ollama")
    assert ollama["requires_api_key"] is False
    assert ollama["delay_between_calls_seconds"] == 0
    assert ollama["request_timeout_seconds"] == 600

    groq = config.resolve_profile("groq")
    assert groq["requires_api_key"] is True
    assert groq["delay_between_calls_seconds"] == 3.0
    assert groq["request_timeout_seconds"] == 60
    assert groq["native_api"] is None


def test_unknown_backend_is_a_clear_error():
    with pytest.raises(ValueError) as exc:
        config.resolve_profile("nope")
    assert "groq" in str(exc.value) and "ollama" in str(exc.value)


# --- the sweep ---------------------------------------------------------------


def test_ollama_sweep_stamps_backend_model_quantization_context(fake_ollama):
    main(["--models", "m4b", "--cases", "restraint_general_question", "--runs", "2"])

    (row,) = read_rows(fake_ollama)
    assert row["backend"] == "ollama"
    assert row["model"] == "m4b"
    assert row["quantization"] == "Q4_K_M"
    assert row["context_length"] == "16384"
    assert row["status"] == "ok"
    assert (row["runs_passed"], row["runs_total"]) == ("2", "2")
    assert "Q4_K_M" in (fake_ollama.tmp_path / "results.md").read_text()


def test_groq_backend_makes_no_native_api_calls(fake_ollama, monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "groq")
    monkeypatch.setattr(config, "NATIVE_API", None)
    monkeypatch.setattr(config, "REQUIRES_API_KEY", True)
    monkeypatch.setattr(config, "API_KEY", "gsk_test")

    main(["--models", "hosted", "--cases", "restraint_general_question", "--runs", "1"])

    assert fake_ollama.paths() == {"/v1/chat/completions"}
    (row,) = read_rows(fake_ollama)
    assert row["quantization"] == "unknown (hosted)"
    assert row["backend"] == "groq"


def test_ollama_backend_sends_no_authorization_header(fake_ollama, monkeypatch):
    # A developer's .env holds a Groq key; the ollama profile must not leak it.
    env = {"GROQ_API_KEY": "gsk_secret"}
    monkeypatch.setattr(
        config, "API_KEY", config.resolve_api_key(config.resolve_profile("ollama"), env)
    )
    assert config.resolve_api_key(config.resolve_profile("groq"), env) == "gsk_secret"

    main(["--models", "m4b", "--cases", "restraint_general_question", "--runs", "1"])

    (request,) = fake_ollama.chat_requests
    assert "authorization" not in request["headers"]


@pytest.mark.parametrize(
    ("content", "passes", "note"),
    [
        # The "?" and "recipient" live only inside the reasoning block. Unstripped, the
        # refusal scorer would accept this assertion as a clarifying question.
        (
            "<think>who is the recipient?</think>Sure, sending now.",
            "0",
            "did not ask a question, just asserted",
        ),
        ("\n<think>\nhmm\n</think>\n\nSure, sending now.", "0", "did not ask a question"),
        ("What should the message say?", "1", "ok"),
    ],
)
def test_leading_think_block_never_reaches_refusal_scoring(fake_ollama, content, passes, note):
    fake_ollama.chat_content = content
    main(["--models", "m4b", "--cases", "refusal_missing_body", "--runs", "1"])

    (row,) = read_rows(fake_ollama)
    assert row["runs_passed"] == passes
    assert note in row["notes"]


# --- preflight ---------------------------------------------------------------


def test_missing_capabilities_means_supported_quantization_unknown(fake_ollama):
    fake_ollama.show["old"] = {}
    assert describe_model("old") == ModelInfo("unknown", True)

    main(["--models", "old", "--cases", "restraint_general_question", "--runs", "1"])
    (row,) = read_rows(fake_ollama)
    assert row["quantization"] == "unknown"
    assert (row["status"], row["runs_total"]) == ("ok", "1")


def test_unpulled_model_aborts_before_any_case_runs(fake_ollama):
    fake_ollama.show["ghost"] = 404

    with pytest.raises(SystemExit) as exc:
        main(["--models", "ghost", "--cases", "restraint_general_question", "--runs", "1"])

    assert "ghost" in str(exc.value)
    assert fake_ollama.chat_requests == []


def test_key_check_names_ollama_backend_for_local_servers(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "groq")
    monkeypatch.setattr(config, "REQUIRES_API_KEY", True)
    monkeypatch.setattr(config, "API_KEY", "")

    with pytest.raises(SystemExit) as exc:
        main(["--runs", "1"])

    assert "GROQ_API_KEY" in str(exc.value) and "BACKEND=ollama" in str(exc.value)


# --- unsupported models ------------------------------------------------------

NO_TOOLS = {"details": {"quantization_level": "Q4_0"}, "capabilities": ["completion"]}
TWO_RESTRAINT = ["restraint_general_question", "restraint_explicit_negation"]


def test_model_without_tools_capability_is_unsupported_not_zero_over_n(fake_ollama):
    fake_ollama.show["notools"] = NO_TOOLS

    main(["--models", "notools", "m4b", "--cases", *TWO_RESTRAINT, "--runs", "2"])

    rows = [r for r in read_rows(fake_ollama) if r["model"] == "notools"]
    assert len(rows) == 2
    assert {(r["status"], r["runs_total"], r["runs_passed"]) for r in rows} == {
        ("unsupported", "0", "0")
    }
    assert {r["quantization"] for r in rows} == {"Q4_0"}
    sent_to = {r["body"]["model"] for r in fake_ollama.chat_requests}
    assert sent_to == {"m4b"}

    report = (fake_ollama.tmp_path / "results.md").read_text()
    assert "| `notools` | unsupported | unsupported |" in report  # pass-rate table
    matrix = [line for line in report.splitlines() if line.startswith("| restraint_")]
    assert len(matrix) == 2 and all("| unsupported |" in line for line in matrix)
    assert "0/0" not in report and "**0/" not in report


def test_unsupported_model_does_not_break_saturation_flag(fake_ollama):
    fake_ollama.show["notools"] = NO_TOOLS

    main(["--models", "notools", "m4b", "--cases", *TWO_RESTRAINT, "--runs", "1"])
    assert "saturated" in (fake_ollama.tmp_path / "results.md").read_text()


def test_only_unsupported_models_flag_nothing_saturated(fake_ollama):
    fake_ollama.show["notools"] = NO_TOOLS

    main(["--models", "notools", "--cases", *TWO_RESTRAINT, "--runs", "1"])
    report = (fake_ollama.tmp_path / "results.md").read_text()
    assert "saturated" not in report
    assert "(1 unsupported)" in report
    assert "None." in report  # no failure notes: unsupported is not a failure
