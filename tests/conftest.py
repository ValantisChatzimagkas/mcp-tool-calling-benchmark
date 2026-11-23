import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import config


@pytest.fixture(scope="session")
def suite():
    """The parsed cases.json, as run_eval.py sees it."""
    with open(config.CASES_PATH) as f:
        return json.load(f)


class FakeOllama:
    """A localhost stand-in for an Ollama server: OpenAI /v1 chat plus the native API.

    Tests set `chat_content` (a string, or a callable taking the request body and
    returning one), `chat_status`, `show` (model -> /api/show body, or an int status)
    and read back `chat_requests` / `requests` to see what the harness actually sent.
    """

    def __init__(self):
        self.chat_content = "Here is a plain text answer."
        self.chat_status = 200
        self.show = {}
        self.ps_context_length = 16384
        self.chat_stats = {
            "prompt_eval_count": 100,
            "prompt_eval_duration": 2_000_000_000,
            "eval_count": 50,
            "eval_duration": 10_000_000_000,
        }
        self.requests = []  # every request: {"method", "path", "headers", "body"}
        self._server = None
        self.port = 0
        self.start()

    @property
    def chat_requests(self):
        return [r for r in self.requests if r["path"] == "/v1/chat/completions"]

    def paths(self):
        return {r["path"] for r in self.requests}

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                fake.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                return body

            def do_POST(self):
                body = self._record("POST")
                if self.path == "/v1/chat/completions":
                    if fake.chat_status != 200:
                        return self._reply(fake.chat_status, {"error": "boom"})
                    content = fake.chat_content
                    if callable(content):
                        content = content(body)
                    message = {"role": "assistant", "content": content}
                    return self._reply(200, {"choices": [{"message": message}]})
                if self.path == "/api/show":
                    entry = fake.show.get(
                        body["model"],
                        {
                            "details": {"quantization_level": "Q4_K_M"},
                            "capabilities": ["completion", "tools"],
                        },
                    )
                    if isinstance(entry, int):
                        return self._reply(entry, {"error": "model not found"})
                    return self._reply(200, entry)
                if self.path == "/api/chat":
                    return self._reply(200, fake.chat_stats)
                return self._reply(404, {"error": "not found"})

            def do_GET(self):
                self._record("GET")
                if self.path == "/api/ps":
                    models = []
                    for r in fake.requests:
                        if r["path"] == "/v1/chat/completions":
                            models.append(
                                {
                                    "name": r["body"]["model"],
                                    "context_length": fake.ps_context_length,
                                }
                            )
                    return self._reply(200, {"models": models})
                return self._reply(404, {"error": "not found"})

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None

    def restart(self):
        """Come back on the same port, as a restarted Ollama would."""
        self.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"


@pytest.fixture
def fake_ollama(monkeypatch, tmp_path):
    """A running FakeOllama with config pointed at it and outputs sent to tmp_path."""
    fake = FakeOllama()
    monkeypatch.setattr(config, "BACKEND", "ollama")
    monkeypatch.setattr(config, "API_BASE_URL", fake.base_url)
    monkeypatch.setattr(config, "API_ROOT", f"http://127.0.0.1:{fake.port}")
    monkeypatch.setattr(config, "NATIVE_API", "ollama")
    monkeypatch.setattr(config, "REQUIRES_API_KEY", False)
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "DELAY_BETWEEN_CALLS_SECONDS", 0)
    monkeypatch.setattr(config, "MAX_RETRIES_ON_429", 0)
    monkeypatch.setattr(config, "REQUEST_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(config, "RESULTS_CSV_PATH", str(tmp_path / "results.csv"))
    monkeypatch.setattr(config, "RESULTS_MD_PATH", str(tmp_path / "results.md"))
    monkeypatch.setattr(config, "JOURNAL_PATH", str(tmp_path / "results.journal.jsonl"))
    fake.tmp_path = tmp_path
    yield fake
    fake.stop()  # idempotent: a test may have stopped it already
