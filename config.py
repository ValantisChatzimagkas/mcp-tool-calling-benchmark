"""Single source of truth for endpoint, credentials and model list.

Swap API_BASE_URL to point the whole benchmark at a local server
(llama.cpp / Ollama / vLLM) without touching any other file.
"""

import os

from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "https://api.groq.com/openai/v1")
API_KEY = os.getenv("GROQ_API_KEY", "")

MODELS = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "llama-3.3-70b-versatile",
]

CASES_PATH = "cases.json"
RESULTS_CSV_PATH = "results.csv"
RESULTS_MD_PATH = "results.md"

REQUEST_TIMEOUT_SECONDS = 60
TEMPERATURE = 0.0
MAX_CHAIN_STEPS = 6

# Groq's free tier is roughly 30 requests/minute; a small gap between calls
# keeps a full sweep under that without needing token-bucket bookkeeping.
DELAY_BETWEEN_CALLS_SECONDS = 2.0
MAX_RETRIES_ON_429 = 3
RETRY_BACKOFF_SECONDS = 20
