"""Per-run sandboxes, server installs and OS confinement for server-backed cases.

Each run gets a fresh temp directory with one subdirectory per declared server, seeded
from the repo's `fixtures/` (copied or executed, never bound), so a run can neither see
another run's writes nor mutate a fixture. Servers are installed once, pinned, outside
the sandbox into `config.MCP_SERVERS_DIR`; at run time they execute directly (never via
`npx`/`uvx`, which need writable caches) under a strict bubblewrap profile. The install
step is the only network use in this module and goes through `_run_installer`.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import config
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


INSTALL_MARKER = ".installed"
_OUTPUT_TAIL_CHARS = 500


def _run_installer(argv: list[str]) -> None:
    """Run one install command. The only subprocess call used for installs (a test seam)."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise McpServerError(f"install failed: {' '.join(argv[:3])}: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-_OUTPUT_TAIL_CHARS:]
        raise McpServerError(
            f"install failed ({' '.join(argv[:3])}, exit {result.returncode}): {tail}"
        )


def _require(tool: str) -> str:
    found = shutil.which(tool)
    if not found:
        raise McpServerError(f"{tool!r} is not on PATH; it is needed to install MCP servers")
    return found


def ensure_installed(kind_name: str, kind: dict) -> Path | None:
    """Install a kind's pinned server into `<MCP_SERVERS_DIR>/<kind_name>` if needed.

    Returns the install dir, or None for a kind with no `install`. Skipped when the
    marker holds the kind's exact install spec; otherwise the dir is removed, reinstalled
    and the marker written last, so a partial install is redone and a changed pin
    reinstalls. Runs outside any sandbox, before any server is spawned.
    """
    install = kind.get("install")
    if not install:
        return None

    directory = Path(config.MCP_SERVERS_DIR).resolve() / kind_name
    wanted = json.dumps(install, sort_keys=True)
    marker = directory / INSTALL_MARKER
    if marker.exists() and marker.read_text() == wanted:
        return directory

    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    if "npm" in install:
        _require("npm")
        _run_installer(
            [
                "npm",
                "install",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--prefix",
                str(directory),
                install["npm"],
            ]
        )
    if "uv" in install:
        _require("uv")
        _run_installer(["uv", "venv", str(directory)])
        _run_installer(
            ["uv", "pip", "install", "--python", str(directory / "bin" / "python"), *install["uv"]]
        )
    marker.write_text(wanted)
    return directory


def runtime_prefix(kind: dict, install_dir: Path) -> Path:
    """The interpreter prefix a server needs read-only inside the sandbox."""
    runtime = kind["runtime"]
    if runtime == "node":
        node = shutil.which("node")
        if not node:
            raise McpServerError("'node' is not on PATH; it is needed to run this MCP server")
        return Path(os.path.realpath(node)).parent.parent
    if runtime == "venv":
        return Path(os.path.realpath(install_dir / "bin" / "python")).parent.parent
    raise McpServerError(f"unknown runtime {runtime!r}")


def confine(
    command: str, args: list[str], writable: Path, readonly: list[Path]
) -> tuple[str, list[str]]:
    """Wrap a server launch per `config.MCP_CONFINEMENT`; returns (command, args).

    "bwrap" (strict profile): no network, no session bus, `$HOME`, the temp dir and
    `/mnt` / `/media` masked with tmpfs, the whole tree otherwise read-only, the given
    `readonly` paths re-bound, and exactly one writable path. Every mask comes before
    every re-bind because `readonly` and `writable` live under masked paths. Never
    `--new-session`: that `setsid`s the server out of the process group the facade
    kills on teardown. What stays readable is the OS tree (`/usr`, `/etc`, `/var`, ...),
    which a process needs in order to run at all.
    """
    mode = config.MCP_CONFINEMENT
    if mode == "none":
        return command, args
    if mode != "bwrap":
        raise McpServerError(f'unknown MCP_CONFINEMENT {mode!r}; use "bwrap" or "none"')
    if not shutil.which("bwrap"):
        raise McpServerError(
            "confinement 'bwrap' is on but bwrap is not on PATH; install bubblewrap or "
            'set MCP_CONFINEMENT = "none" in config.py'
        )

    tempdir = Path(tempfile.gettempdir()).resolve()
    if tempdir not in writable.resolve().parents:
        raise McpServerError(f"sandbox dir {writable} is not under the masked temp dir {tempdir}")

    argv = [
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        str(Path.home()),
        "--tmpfs",
        "/run/user",
        "--tmpfs",
        str(tempdir),
    ]
    for extra in ("/mnt", "/media"):
        if os.path.isdir(extra):
            argv += ["--tmpfs", extra]
    for path in readonly:
        argv += ["--ro-bind", str(path), str(path)]
    argv += ["--bind", str(writable), str(writable), "--", command, *args]
    return "bwrap", argv


def fixture_digest(fixtures_dir: str, fixture: str | None) -> str | None:
    """Content digest of one seed fixture (a file or a directory tree), or None if absent.

    Pure file reads, no server and no network: this is what lets a case hash cover the
    data a server-backed case's tool results come from. Names and bytes both count, so a
    rename or an edit changes it.
    """
    if not fixture:
        return None
    root = Path(fixtures_dir) / fixture
    if not root.exists():
        return None
    digest = hashlib.sha256()
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() if path != root else b".")
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]
