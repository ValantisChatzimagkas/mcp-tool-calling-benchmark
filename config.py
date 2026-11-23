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

# Tool calling is nondeterministic even at temperature 0, so every (model, case) pair
# runs this many times and reports k/n instead of a single pass/fail. Override per run
# with --runs.
RUNS_PER_CASE = 3

# Groq's free tier is roughly 30 requests/minute; a small gap between calls
# keeps a full sweep under that without needing token-bucket bookkeeping. 3s caps the
# sweep at ~20 requests/minute, which holds however many runs RUNS_PER_CASE asks for —
# repeats make the sweep longer, not denser.
DELAY_BETWEEN_CALLS_SECONDS = 3.0
MAX_RETRIES_ON_429 = 3
RETRY_BACKOFF_SECONDS = 20

# Local stdio MCP servers (server-backed cases). A server that can't start in time, or a
# call that doesn't return in time, fails that run with an `mcp server error:` note.
MCP_STARTUP_TIMEOUT_SECONDS = 60
MCP_CALL_TIMEOUT_SECONDS = 30
MCP_SHUTDOWN_TIMEOUT_SECONDS = 10

# Server-backed cases: where their seed data lives, where the pinned servers are
# installed (gitignored; filled on first use), and how each kind of server is launched.
#
# MCP_CONFINEMENT: "bwrap" runs every server under bubblewrap (the default; a run fails
# loudly if bwrap is missing). "none" is the explicit, unconfined opt-out; any other
# value is an error. There is no silent fallback.
#
# Each kind: `install` is the pinned package spec ({"npm": "<pkg>@<ver>"} or
# {"uv": [<requirements>]}), installed outside the sandbox into MCP_SERVERS_DIR/<kind>;
# `runtime` ("node" | "venv") names the interpreter prefix to re-bind read-only;
# `command`/`args` launch it, with "{install}" the install dir and "{path}" the server's
# sandbox path (the dir for seed "dir", the db file for seed "sql"). A kind without
# `install` runs as given (the tests' fake server).
FIXTURES_DIR = "fixtures"
MCP_SERVERS_DIR = ".mcp-servers"
MCP_CONFINEMENT = "bwrap"
MCP_SERVER_KINDS = {
    "filesystem": {
        "install": {"npm": "@modelcontextprotocol/server-filesystem@2.4.0"},
        "runtime": "node",
        "command": "node",
        "args": [
            "{install}/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js",
            "{path}",
        ],
        "seed": "dir",
    },
    "sqlite": {
        # `mcp<2`: mcp-server-sqlite leaves its `mcp` dependency unpinned and crashes at
        # startup on mcp 2.x ('Server' object has no attribute 'list_resources').
        "install": {"uv": ["mcp<2", "mcp-server-sqlite==2025.4.25"]},
        "runtime": "venv",
        "command": "{install}/bin/mcp-server-sqlite",
        "args": ["--db-path", "{path}"],
        "seed": "sql",
    },
}
