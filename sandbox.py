"""Per-run sandboxes for server-backed cases.

Each run gets a fresh temp directory with one subdirectory per declared server, seeded
from the repo's `fixtures/` (copied or executed, never bound), so a run can neither see
another run's writes nor mutate a fixture. No network, no config reads beyond what the
caller passes in; the OS-level confinement of the server processes is added on top.
"""

import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mcp_client import McpServerError

DB_FILENAME = "db.sqlite"


@dataclass
class Sandbox:
    root: Path
    # name -> the value `${name}` and `{path}` stand for: the directory (seed "dir") or
    # the database file (seed "sql").
    paths: dict[str, Path] = field(default_factory=dict)
    # name -> that server's own subdirectory (the only thing it may write to).
    dirs: dict[str, Path] = field(default_factory=dict)


def _seed(name: str, server: dict, kind: dict, directory: Path, fixtures_dir: str) -> Path:
    fixture = Path(fixtures_dir) / server["fixture"]
    seed = kind["seed"]
    if seed == "dir":
        shutil.copytree(fixture, directory)
        return directory
    if seed == "sql":
        directory.mkdir()
        db = directory / DB_FILENAME
        connection = sqlite3.connect(db)
        try:
            connection.executescript(fixture.read_text())
            connection.commit()
        finally:
            connection.close()
        return db
    raise McpServerError(f"server {name!r}: unknown seed type {seed!r}")


@contextmanager
def open_sandbox(servers: dict[str, dict], kinds: dict, fixtures_dir: str):
    """Create and seed a sandbox for the declared servers; remove it afterwards, even on error."""
    for name, server in servers.items():
        if server["kind"] not in kinds:
            raise McpServerError(f"server {name!r}: unknown kind {server['kind']!r}")

    root = Path(tempfile.mkdtemp(prefix="mcp-bench-")).resolve()
    try:
        sandbox = Sandbox(root=root)
        for name, server in servers.items():
            directory = root / name
            sandbox.dirs[name] = directory
            sandbox.paths[name] = _seed(
                name, server, kinds[server["kind"]], directory, fixtures_dir
            )
        yield sandbox
    finally:
        shutil.rmtree(root, ignore_errors=True)
