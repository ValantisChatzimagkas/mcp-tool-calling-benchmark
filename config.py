"""Single source of truth for endpoint, credentials, model list and tuning.

`BACKEND` (env var, default `groq`) picks a named profile from PROFILES, and one switch
moves every tunable together: base URL, key requirement, timeout, call spacing, 429
retries and default models. That is what lets the same script hit a local Ollama server
unchanged — and why there are no per-tunable overrides, which would allow a
half-switched config (a CPU server paced like Groq's free tier).
"""

import os

from dotenv import load_dotenv

load_dotenv()

PROFILES = {
    # Hosted inference on Groq's free tier: short timeout, paced calls, 429 retries.
    "groq": {
        "api_base_url": "https://api.groq.com/openai/v1",
        "requires_api_key": True,
        "request_timeout_seconds": 60,
        # Groq's free tier is roughly 30 requests/minute; a small gap between calls
        # keeps a full sweep under that without needing token-bucket bookkeeping. 3s
        # caps the sweep at ~20 requests/minute, which holds however many runs
        # RUNS_PER_CASE asks for — repeats make the sweep longer, not denser.
        "delay_between_calls_seconds": 3.0,
        "max_retries_on_429": 3,
        "models": [
            "openai/gpt-oss-20b",
            "qwen/qwen3.6-27b",
            "llama-3.3-70b-versatile",
        ],
        "native_api": None,
    },
    # A local Ollama server on CPU: one call takes tens of seconds (a cold model load
    # alone can take minutes), there is no key and no rate limit to pace against.
    "ollama": {
        "api_base_url": "http://localhost:11434/v1",
        "requires_api_key": False,
        "request_timeout_seconds": 600,
        "delay_between_calls_seconds": 0.0,
        "max_retries_on_429": 0,
        "models": ["qwen3:4b"],
        # Which native (non-OpenAI) API the server also speaks, for the quantization,
        # tool-support and context-length metadata /v1 does not carry. None = hosted.
        "native_api": "ollama",
    },
}


def resolve_profile(name):
    """The profile dict for a backend name; a clear error for an unknown one."""
    try:
        return PROFILES[name]
    except KeyError:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown BACKEND {name!r}; valid backends: {valid}") from None


def resolve_api_key(profile, environ):
    """The bearer token to send: the Groq key only to a backend that requires one.

    A `.env` holding GROQ_API_KEY must never be sent to a server that doesn't ask for it.
    """
    return environ.get("GROQ_API_KEY", "") if profile["requires_api_key"] else ""


BACKEND = os.getenv("BACKEND", "groq")
_profile = resolve_profile(BACKEND)

API_BASE_URL = os.getenv("API_BASE_URL", _profile["api_base_url"])
REQUIRES_API_KEY = _profile["requires_api_key"]
API_KEY = resolve_api_key(_profile, os.environ)
MODELS = list(_profile["models"])
REQUEST_TIMEOUT_SECONDS = _profile["request_timeout_seconds"]
DELAY_BETWEEN_CALLS_SECONDS = _profile["delay_between_calls_seconds"]
MAX_RETRIES_ON_429 = _profile["max_retries_on_429"]
NATIVE_API = _profile["native_api"]

# Native endpoints (e.g. Ollama's /api/show) hang off the server root, not /v1.
API_ROOT = API_BASE_URL.rstrip("/").removesuffix("/v1")

CASES_PATH = "cases.json"
RESULTS_CSV_PATH = "results.csv"
RESULTS_MD_PATH = "results.md"
JOURNAL_PATH = "results.journal.jsonl"

TEMPERATURE = 0.0
MAX_CHAIN_STEPS = 6

# Tool calling is nondeterministic even at temperature 0, so every (model, case) pair
# runs this many times and reports k/n instead of a single pass/fail. Override per run
# with --runs.
RUNS_PER_CASE = 3

RETRY_BACKOFF_SECONDS = 20
