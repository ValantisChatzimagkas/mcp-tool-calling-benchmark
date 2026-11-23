"""Sync facade over local stdio MCP servers.

Spawns each server as a subprocess, lists its tools (`tools/list`) as OpenAI-format
function schemas, and executes the model's calls (`tools/call`). Only stdio transport
is spoken; this module opens no sockets itself. Every failure — a server that won't
start, crashes, hangs or can't be confined — is raised as `McpServerError`, which the
runner scores as a failed run rather than a fatal error.

The SDK is async and an `anyio` task group must be entered and exited in one task, so a
coordinator task on a background event loop owns all stdio sessions; `call()` bridges in
from synchronous code with `run_coroutine_threadsafe`.
"""

import asyncio
import concurrent.futures
import contextlib
import os
import re
import signal
import sys
import tempfile
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
from mcp.types import PaginatedRequestParams

import config

if sys.version_info < (3, 11):  # pragma: no cover - backport ships with anyio on 3.10
    from exceptiongroup import BaseExceptionGroup

# The OpenAI function-name rule; a prefixed tool name must satisfy it.
_FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STDERR_TAIL_CHARS = 500
_BACKSTOP_MARGIN_SECONDS = 5


# Held while a server is spawned and its pid identified, so two McpServers instances
# starting at once cannot attribute each other's child.
_SPAWN_LOCK = threading.Lock()


def _proc_stat(pid: int) -> tuple[int, int, int] | None:
    """(ppid, pgrp, session) of a process from /proc, or None if it is gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return None
    return int(fields[1]), int(fields[2]), int(fields[3])


def _all_pids() -> list[int]:
    return [int(name) for name in os.listdir("/proc") if name.isdigit()]


def _session_leader_children() -> set[int]:
    """Our direct children that lead their own process group and session.

    `stdio_client` spawns each server with `start_new_session=True`, so the server pid
    is also its pgid; nothing else in this process creates such children.
    """
    me = os.getpid()
    found = set()
    for pid in _all_pids():
        stat = _proc_stat(pid)
        if stat and stat[0] == me and stat[1] == pid and stat[2] == pid:
            found.add(pid)
    return found


def _group_members(pgid: int) -> list[int]:
    return [pid for pid in _all_pids() if (stat := _proc_stat(pid)) and stat[1] == pgid]


def _kill_group(pgid: int) -> None:
    """SIGKILL every process left in a server's process group.

    The SDK only runs its own `killpg` when the server is still alive after closing
    stdin, so a server that exits on its own leaves its descendants behind. Never
    touches our own group, and does nothing once the group is empty (so a recycled pid
    is not signalled).
    """
    if pgid == os.getpgrp() or pgid <= 1:
        return
    if not _group_members(pgid):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


class McpServerError(Exception):
    """Start failure, crash, timeout, protocol error or impossible confinement.

    Scored as a failed run, never fatal to the sweep.
    """


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: str
    args: list[str]
    allow_tools: list[str] | None = None


def _leaves(exc: BaseException) -> list[BaseException]:
    """Flatten (nested) exception groups into their leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaves(inner)]
    return [exc]


def _describe(exc: BaseException) -> str:
    if isinstance(exc, MCPError):
        return f"{exc.message} (MCP error {exc.code})"
    if isinstance(exc, TimeoutError):
        return f"timed out ({exc})" if str(exc) else "timed out"
    return f"{type(exc).__name__}: {exc}"


def _unwrap(exc: BaseException, stderr_tail: str = "") -> McpServerError:
    """Flatten an ExceptionGroup / SDK error into one McpServerError with the stderr tail."""
    if isinstance(exc, McpServerError):
        return exc
    message = "; ".join(dict.fromkeys(_describe(leaf) for leaf in _leaves(exc)))
    if stderr_tail:
        message += f" | server stderr: {stderr_tail}"
    return McpServerError(message)


async def _list_all_tools(session) -> list:
    """`tools/list`, following `next_cursor` until the server has no more pages."""
    tools = []
    cursor = None
    while True:
        params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        page = await session.list_tools(params=params)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return tools


def _to_openai_tool(server: str, tool) -> dict:
    """One MCP tool as an OpenAI function tool; the schema is passed through untouched.

    `title`, `annotations` and `outputSchema` have no OpenAI field and are dropped.
    """
    name = f"{server}__{tool.name}"
    if not _FUNCTION_NAME.match(name):
        raise McpServerError(
            f"tool name {name!r} is not a valid function name (^[A-Za-z0-9_-]{{1,64}}$)"
        )
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def _result_text(result) -> str:
    """Text blocks joined by newline; anything else becomes a placeholder.

    An `is_error` result passes through as text — the model should see `Access denied`.
    """
    parts = []
    for block in result.content:
        if block.type == "text":
            parts.append(block.text)
        else:
            parts.append(f"[{block.type} content omitted]")
    return "\n".join(parts)


class McpServers:
    """Context manager owning a set of stdio MCP servers for the length of one run."""

    def __init__(self, specs: list[ServerSpec]):
        self._specs = specs
        self.tools: list[dict] = []
        self._routes: dict[str, tuple[str, str]] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._errlogs: dict[str, object] = {}
        self._pgids: list[int] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._coordinator: concurrent.futures.Future | None = None
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        self._stop: asyncio.Event | None = None

    # --- lifecycle -------------------------------------------------------------

    def __enter__(self):
        names = [spec.name for spec in self._specs]
        if len(set(names)) != len(names):
            raise McpServerError(f"duplicate server names: {names}")

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._coordinator = asyncio.run_coroutine_threadsafe(self._coordinate(), self._loop)
        try:
            self._ready.result(timeout=config.MCP_STARTUP_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            tail = self._stderr_tail()
            self._teardown()
            raise McpServerError(
                f"timed out after {config.MCP_STARTUP_TIMEOUT_SECONDS}s starting servers"
                + (f" | server stderr: {tail}" if tail else "")
            ) from None
        except BaseException:
            self._teardown()
            raise
        return self

    def __exit__(self, *exc_info):
        self._teardown()
        return False

    def _teardown(self):
        """Stop the coordinator (which kills every server tree) within a bounded wait.

        Teardown errors are swallowed: a crash while closing must not flip a verdict
        that was already earned, and a stuck teardown must not hang the sweep.
        """
        loop, thread = self._loop, self._thread
        if loop is None:
            return
        try:
            if self._ready.done() and self._stop is not None:
                loop.call_soon_threadsafe(self._stop.set)
            elif self._coordinator is not None:
                self._coordinator.cancel()  # still starting: unwind whatever has started
            if self._coordinator is not None:
                with contextlib.suppress(BaseException):
                    self._coordinator.result(timeout=config.MCP_SHUTDOWN_TIMEOUT_SECONDS)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=1)
            for pgid in self._pgids:
                with contextlib.suppress(Exception):
                    _kill_group(pgid)
            self._pgids.clear()
            for log in self._errlogs.values():
                with contextlib.suppress(Exception):
                    log.close()
            self._loop = None

    # --- coordinator task ------------------------------------------------------

    async def _coordinate(self):
        self._stop = asyncio.Event()
        starting = None
        try:
            async with AsyncExitStack() as stack:
                try:
                    # The startup timeout is enforced by __enter__ (cancelling this
                    # task), not an anyio scope: stdio_client's task group outlives
                    # this block and cancel scopes must nest strictly.
                    for spec in self._specs:
                        starting = spec
                        await self._start(stack, spec)
                    starting = None
                    await self._collect_tools()
                except BaseException as exc:
                    if isinstance(exc, (McpServerError, asyncio.CancelledError)):
                        raise
                    where = f"server {starting.name!r} ({starting.command})" if starting else ""
                    err = _unwrap(exc, self._stderr_tail())
                    raise McpServerError(f"{where}: {err}" if where else str(err)) from exc
                self._ready.set_result(None)
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(
                    exc if isinstance(exc, McpServerError) else _unwrap(exc, self._stderr_tail())
                )
            # After readiness, errors here are teardown noise; the verdict stands.

    async def _start(self, stack: AsyncExitStack, spec: ServerSpec):
        errlog = tempfile.TemporaryFile("w+b")  # noqa: SIM115 - closed in _teardown
        self._errlogs[spec.name] = errlog
        params = StdioServerParameters(command=spec.command, args=list(spec.args))
        with _SPAWN_LOCK:
            before = _session_leader_children()
            read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
            spawned = _session_leader_children() - before
        pgid = spawned.pop() if len(spawned) == 1 else None
        if pgid is not None:
            self._pgids.append(pgid)
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        if pgid is None:
            # Without the pid we can't promise the server's descendants are reaped.
            raise McpServerError(
                f"could not identify the process of server {spec.name!r} "
                f"({len(spawned)} candidate children); refusing to run without reaping"
            )
        self._sessions[spec.name] = session

    async def _collect_tools(self):
        """List every server's tools, apply allow-lists, build the OpenAI tool list and routes."""
        for spec in self._specs:
            listed = await _list_all_tools(self._sessions[spec.name])
            if spec.allow_tools is not None:
                by_name = {tool.name: tool for tool in listed}
                missing = [n for n in spec.allow_tools if n not in by_name]
                if missing:
                    raise McpServerError(
                        f"server {spec.name!r} does not offer tool(s) {missing}; "
                        f"it offers {sorted(by_name)}"
                    )
                listed = [by_name[n] for n in spec.allow_tools]
            for tool in listed:
                openai_tool = _to_openai_tool(spec.name, tool)
                prefixed = openai_tool["function"]["name"]
                if prefixed in self._routes:
                    raise McpServerError(f"duplicate tool name {prefixed!r}")
                self._routes[prefixed] = (spec.name, tool.name)
                self.tools.append(openai_tool)

    # --- calls -----------------------------------------------------------------

    def call(self, name: str, arguments: dict) -> str:
        """Execute `tools/call` for a prefixed tool name and return the result text."""
        if name not in self._routes:
            return f"Error: unknown tool '{name}'"
        server, tool = self._routes[name]
        timeout = config.MCP_CALL_TIMEOUT_SECONDS
        future = asyncio.run_coroutine_threadsafe(
            self._sessions[server].call_tool(tool, arguments, read_timeout_seconds=timeout),
            self._loop,
        )
        try:
            result = future.result(timeout=timeout + _BACKSTOP_MARGIN_SECONDS)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise McpServerError(f"call to {name} timed out") from exc
        except BaseException as exc:
            raise _unwrap(exc, self._stderr_tail()) from exc
        return _result_text(result)

    def _stderr_tail(self) -> str:
        """Last 500 chars of captured server stderr (labelled when several servers).

        Read with `pread` so the shared file offset the child writes through is untouched.
        """
        chunks = []
        for name, log in self._errlogs.items():
            try:
                fd = log.fileno()
                size = os.fstat(fd).st_size
                data = os.pread(fd, _STDERR_TAIL_CHARS * 4, max(0, size - _STDERR_TAIL_CHARS * 4))
            except (OSError, ValueError):
                continue
            text = data.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL_CHARS:]
            if text:
                chunks.append(text if len(self._errlogs) == 1 else f"[{name}] {text}")
        return " ".join(chunks)[-_STDERR_TAIL_CHARS:]
