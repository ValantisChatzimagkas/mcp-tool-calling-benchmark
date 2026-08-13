"""Talking to the model endpoint. Everything network-facing lives here."""

import json
import time

import requests

import config


class ApiError(Exception):
    """Any failure talking to the endpoint. Scored as a failed case, never fatal."""


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
            return body["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ApiError(f"unexpected response shape: {json.dumps(body)[:200]}") from exc

    raise ApiError("rate limited, retries exhausted")


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
