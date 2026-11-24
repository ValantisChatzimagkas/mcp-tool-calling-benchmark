"""A tiny stdio MCP server used only by the tests.

Run as `python tests/fake_mcp_server.py <root dir>`. The tools exist to exercise the
facade's failure paths (crash, hang, orphaned grandchild, env leakage) and to give the
run tests a file-backed tool that reads and writes inside the sandbox directory.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

server = MCPServer("fake")


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def pid() -> str:
    """Return this server's process id."""
    return str(os.getpid())


@server.tool()
def env_keys() -> str:
    """Return the names of the environment variables this server can see."""
    return "\n".join(sorted(os.environ))


@server.tool()
def boom() -> str:
    """Exit the process before replying."""
    os._exit(7)


@server.tool()
def hang() -> str:
    """Sleep far longer than any call timeout."""
    time.sleep(60)
    return "unreachable"


@server.tool()
def spawn_child() -> str:
    """Start a long-lived grandchild process and return its pid."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    return str(child.pid)


@server.tool()
def read_note(name: str) -> str:
    """Read a note from the server's root directory."""
    return (ROOT / name).read_text()


@server.tool()
def write_note(name: str, text: str) -> str:
    """Write a note into the server's root directory."""
    (ROOT / name).write_text(text)
    return f"wrote {name}"


if __name__ == "__main__":
    server.run("stdio")
