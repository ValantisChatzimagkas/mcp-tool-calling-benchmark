"""Talking to the model endpoint. Everything network-facing lives here."""

import json
import re
import time
from typing import NamedTuple

import requests

import config


class ApiError(Exception):
    """Any failure talking to the endpoint. Scored as a failed case, never fatal."""


class ServerUnreachable(ApiError):
    """The connection itself failed (refused, reset, dropped): the server is down.

    Unlike an HTTP error or a read timeout, this says nothing about the model — a
    crashed or OOM-killed server would otherwise turn every remaining run into a recorded
    model failure in seconds. The sweep stops instead, and --resume picks it up again.
    """


class ModelInfo(NamedTuple):
    """What the server says about a model that the model tag alone doesn't."""

    quantization: str
    supports_tools: bool


# Reasoning models (Qwen3 under Ollama) may inline their chain of thought in `content`.
# Scoring reads `content` for question marks and keywords, so a leading reasoning block
# would let "who is the recipient?" inside <think> pass a refusal the answer never made.
_LEADING_THINK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)


def strip_leading_think(content):
    """Drop one leading <think>…</think> block (and the whitespace after it)."""
    if not isinstance(content, str):
        return content
    return _LEADING_THINK.sub("", content, count=1)


def chat(model, messages, tools):
    """One chat-completions call. Returns the assistant message dict, or raises ApiError."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.TEMPERATURE,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json"}
    if config.API_KEY:
        headers["Authorization"] = f"Bearer {config.API_KEY}"

    for attempt in range(config.MAX_RETRIES_ON_429 + 1):
        try:
            response = requests.post(
                f"{config.API_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
        except requests.ConnectionError as exc:  # includes ConnectTimeout
            raise ServerUnreachable(f"{type(exc).__name__}: {exc}") from exc
        except requests.RequestException as exc:
            raise ApiError(f"request failed: {type(exc).__name__}: {exc}") from exc

        if response.status_code == 429 and attempt < config.MAX_RETRIES_ON_429:
            wait = float(response.headers.get("retry-after", config.RETRY_BACKOFF_SECONDS))
            print(f"    rate limited, retrying in {wait:.0f}s")
            time.sleep(wait)
            continue

        if response.status_code != 200:
            raise ApiError(f"HTTP {response.status_code}: {response.text[:200]}")

        body = response.json()
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ApiError(f"unexpected response shape: {json.dumps(body)[:200]}") from exc
        if "content" in message:
            message["content"] = strip_leading_think(message["content"])
        return message

    raise ApiError("rate limited, retries exhausted")


class GenerationStats(NamedTuple):
    """One call's token counts and wall-clock seconds, as the server itself measured."""

    prompt_tokens: int
    prompt_seconds: float
    generated_tokens: int
    generation_seconds: float


def chat_stats(model, messages, tools, num_predict) -> GenerationStats:
    """One native /api/chat call, for throughput: prompt and generation token timings.

    The OpenAI-compatible /v1 endpoint carries no timings, so this is the one place the
    harness talks to Ollama's native chat API. Durations come back in nanoseconds.
    """
    if config.NATIVE_API is None:
        raise ApiError("throughput needs a backend with a native API (BACKEND=ollama)")

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": config.TEMPERATURE, "num_predict": num_predict},
    }
    if tools:
        payload["tools"] = tools

    try:
        response = requests.post(
            f"{config.API_ROOT}/api/chat", json=payload, timeout=config.REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise ApiError(f"request failed: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise ApiError(f"HTTP {response.status_code}: {response.text[:200]}")

    body = response.json()
    try:
        return GenerationStats(
            prompt_tokens=body["prompt_eval_count"],
            prompt_seconds=body["prompt_eval_duration"] / 1e9,
            generated_tokens=body["eval_count"],
            generation_seconds=body["eval_duration"] / 1e9,
        )
    except KeyError as exc:
        raise ApiError(f"response lacks timing field {exc}: {json.dumps(body)[:200]}") from exc


def describe_model(model) -> ModelInfo:
    """Quantization and tool support, as the server reports them.

    Read from the server rather than declared in config: `qwen3:4b` carries no
    quantization in its tag, so only what the server actually loaded can label a run.
    A server that omits `capabilities` is treated as supporting tools (an old Ollama
    can't say otherwise) and one that omits the quantization reports "unknown".
    """
    if config.NATIVE_API is None:
        return ModelInfo("unknown (hosted)", True)

    try:
        response = requests.post(
            f"{config.API_ROOT}/api/show",
            json={"model": model},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ApiError(f"request failed: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise ApiError(f"HTTP {response.status_code}: {response.text[:200]}")

    body = response.json()
    quantization = (body.get("details") or {}).get("quantization_level") or "unknown"
    capabilities = body.get("capabilities")
    supports_tools = True if capabilities is None else "tools" in capabilities
    return ModelInfo(quantization, supports_tools)


def loaded_context_length(model):
    """The context window the server actually loaded `model` with, or None.

    The server's OLLAMA_CONTEXT_LENGTH is invisible to the harness, so each result row
    records what was observed. Never raises: a missing value is data, not a failure.
    """
    if config.NATIVE_API is None:
        return None
    try:
        response = requests.get(f"{config.API_ROOT}/api/ps", timeout=config.REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            return None
        for entry in response.json().get("models", []):
            if model in (entry.get("name"), entry.get("model")):
                value = entry.get("context_length")
                return value if isinstance(value, int) else None
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return None


def parse_tool_calls(message):
    """Return [(name, args_dict), ...] for the assistant message.

    Malformed arguments are surfaced as a sentinel key rather than an exception, so a
    model emitting broken JSON scores as a failed case instead of killing the sweep.
    """
    calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {})
        name = function.get("name", "")
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"__unparseable__": function.get("arguments")}
        if not isinstance(args, dict):
            args = {"__not_an_object__": args}
        calls.append((name, args))
    return calls
