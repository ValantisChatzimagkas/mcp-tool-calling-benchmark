"""throughput.py and client.chat_stats: tokens/second from Ollama's own timings.

`chat_stats` goes through the localhost fake Ollama; the statistics are plain functions.
"""

import statistics

import pytest

import config
import throughput
from client import ApiError, GenerationStats, chat_stats


def stats(prompt_tokens=100, prompt_seconds=10.0, generated_tokens=50, generation_seconds=5.0):
    return GenerationStats(prompt_tokens, prompt_seconds, generated_tokens, generation_seconds)


def test_chat_stats_converts_nanosecond_durations(fake_ollama):
    fake_ollama.chat_stats = {
        "prompt_eval_count": 100,
        "prompt_eval_duration": 2_000_000_000,
        "eval_count": 50,
        "eval_duration": 10_000_000_000,
    }

    result = chat_stats("m4b", [{"role": "user", "content": "hi"}], [], num_predict=32)

    assert result == GenerationStats(100, 2.0, 50, 10.0)
    (request,) = [r for r in fake_ollama.requests if r["path"] == "/api/chat"]
    assert request["body"]["stream"] is False
    assert request["body"]["options"] == {"temperature": config.TEMPERATURE, "num_predict": 32}


def test_chat_stats_refuses_a_hosted_backend(monkeypatch):
    monkeypatch.setattr(config, "NATIVE_API", None)
    with pytest.raises(ApiError):
        chat_stats("m", [], [], num_predict=8)


def test_summarize_throughput_reports_median_not_mean():
    # generation tok/s of 10, 12 and 100 over 5 seconds each
    samples = [stats(generated_tokens=n * 5) for n in (10, 12, 100)]

    summary = throughput.summarize_throughput(samples)

    gen = summary["generation_tok_s"]
    assert (gen["median"], gen["min"], gen["max"]) == (12, 10, 100)
    assert gen["median"] != pytest.approx(statistics.mean([10, 12, 100]))
    assert summary["n"] == 3 and summary["dropped"] == 0


def test_summarize_throughput_reports_prompt_rate():
    summary = throughput.summarize_throughput([stats(prompt_tokens=100, prompt_seconds=4.0)])

    assert summary["prompt_tok_s"]["median"] == 25
    assert summary["prompt_tokens"] == {"min": 100, "max": 100}


def test_summarize_throughput_flags_cache_hits():
    """Ollama reports only the tokens it evaluated: a prefix-cache hit shows up as a
    smaller prompt count and would inflate prompt tok/s if it went unnoticed."""
    summary = throughput.summarize_throughput([stats(prompt_tokens=100), stats(prompt_tokens=40)])
    assert summary["cache_suspect"] is True

    summary = throughput.summarize_throughput([stats(prompt_tokens=100), stats(prompt_tokens=98)])
    assert summary["cache_suspect"] is False


def test_summarize_throughput_drops_zero_duration_samples_and_counts_them():
    samples = [stats(), stats(prompt_seconds=0.0), stats(generation_seconds=0.0)]

    summary = throughput.summarize_throughput(samples)

    assert summary["n"] == 1 and summary["dropped"] == 2


def test_summarize_throughput_all_dropped_raises():
    with pytest.raises(ValueError):
        throughput.summarize_throughput([stats(prompt_seconds=0.0)])


def test_with_nonce_differs_per_repeat_and_keeps_original_messages():
    messages = [{"role": "user", "content": "hello"}]

    first = throughput.with_nonce(messages, "a1")
    second = throughput.with_nonce(messages, "b2")

    assert first[0]["role"] == "system" and first[0] != second[0]
    assert first[1:] == second[1:] == messages
    assert messages == [{"role": "user", "content": "hello"}]  # input not mutated


def test_throughput_refuses_non_ollama_backend(fake_ollama, monkeypatch):
    monkeypatch.setattr(config, "NATIVE_API", None)

    with pytest.raises(SystemExit) as exc:
        throughput.main(["--model", "m4b"])

    assert "BACKEND=ollama" in str(exc.value)
    assert fake_ollama.requests == []


def test_throughput_runs_a_warmup_then_the_repeats(fake_ollama, capsys):
    throughput.main(["--model", "m4b", "--repeats", "3", "--num-predict", "16"])

    calls = [r for r in fake_ollama.requests if r["path"] == "/api/chat"]
    assert len(calls) == 4  # 1 discarded warm-up + 3 measured
    systems = {c["body"]["messages"][0]["content"] for c in calls}
    assert len(systems) == 4  # a distinct nonce on every call
    out = capsys.readouterr().out
    assert "n=3" in out and "num_predict=16" in out and "chaining_three_steps" in out
