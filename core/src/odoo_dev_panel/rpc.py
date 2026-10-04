"""JSON-RPC 2.0 over Content-Length framed byte streams.

The same framing is used on every hop: Tauri <-> sidecar (stdio) and
sidecar/CLI <-> agent (Unix socket). Frames look like LSP messages::

    Content-Length: 42\r\n
    \r\n
    {"jsonrpc": "2.0", ...}
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Awaitable, Callable

_logger = logging.getLogger(__name__)

MAX_FRAME = 64 * 1024 * 1024

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Application error codes
NOT_FOUND = -32001
FORBIDDEN = -32002
CONFLICT = -32003
UNAVAILABLE = -32004


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class ConnectionClosed(Exception):
    pass


def encode_frame(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


async def read_frame(reader: asyncio.StreamReader) -> dict | None:
    """Read one frame. Return None on clean EOF before a frame starts."""
    length = None
    first = True
    while True:
        line = await reader.readline()
        if not line:
            if first:
                return None
            raise ConnectionClosed("EOF inside frame header")
        first = False
        line = line.rstrip(b"\r\n")
        if not line:
            break
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    if length is None or length < 0 or length > MAX_FRAME:
        raise RpcError(INVALID_REQUEST, f"bad Content-Length: {length}")
    body = await reader.readexactly(length)
    return json.loads(body)


Handler = Callable[[Any, "Connection"], Awaitable[Any]]


class Connection:
    """One JSON-RPC peer. Both sides can send requests and notifications."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: Any,
        handlers: dict[str, Handler] | None = None,
        name: str = "peer",
    ):
        self.reader = reader
        self.writer = writer
        self.handlers = handlers or {}
        self.name = name
        self.context: dict[str, Any] = {}
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._close_callbacks: list[Callable[[], None]] = []
        self.closed = asyncio.Event()

    def on_close(self, callback: Callable[[], None]) -> None:
        self._close_callbacks.append(callback)

    async def _send(self, message: dict) -> None:
        if self.closed.is_set():
            raise ConnectionClosed(f"{self.name} closed")
        data = encode_frame(message)
        async with self._write_lock:
            self.writer.write(data)
            await self.writer.drain()

    async def notify(self, method: str, params: Any = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: Any = None, timeout: float | None = None) -> Any:
        msg_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(msg_id, None)

    def spawn(self, coro: Awaitable[Any]) -> asyncio.Task:
        """Run a task that is cancelled when the connection closes."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def serve(self) -> None:
        """Read and dispatch messages until EOF."""
        try:
            while True:
                try:
                    message = await read_frame(self.reader)
                except (ConnectionClosed, asyncio.IncompleteReadError, ConnectionResetError):
                    break
                except (RpcError, ValueError) as exc:
                    _logger.warning("%s: dropping bad frame: %s", self.name, exc)
                    await self._send_error(None, RpcError(PARSE_ERROR, str(exc)))
                    continue
                if message is None:
                    break
                self._dispatch(message)
        finally:
            await self.close()

    def _dispatch(self, message: dict) -> None:
        if "method" in message:
            self.spawn(self._handle_request(message))
            return
        msg_id = message.get("id")
        future = self._pending.get(msg_id)
        if future is None or future.done():
            return
        if "error" in message:
            err = message["error"] or {}
            future.set_exception(
                RpcError(err.get("code", INTERNAL_ERROR), err.get("message", "error"), err.get("data"))
            )
        else:
            future.set_result(message.get("result"))

    async def _handle_request(self, message: dict) -> None:
        msg_id = message.get("id")
        method = message.get("method")
        handler = self.handlers.get(method)
        try:
            if handler is None:
                raise RpcError(METHOD_NOT_FOUND, f"unknown method: {method}")
            result = await handler(message.get("params"), self)
        except RpcError as exc:
            if msg_id is not None:
                await self._send_error(msg_id, exc)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report every failure to the peer
            _logger.exception("%s: handler %s failed", self.name, method)
            if msg_id is not None:
                await self._send_error(msg_id, RpcError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
            return
        if msg_id is not None:
            try:
                await self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})
            except ConnectionClosed:
                pass

    async def _send_error(self, msg_id: Any, exc: RpcError) -> None:
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "error": exc.to_dict()})
        except ConnectionClosed:
            pass

    async def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionClosed(f"{self.name} closed"))
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        for callback in self._close_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001
                _logger.exception("%s: close callback failed", self.name)
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001
            pass


async def open_unix(path: str, handlers: dict[str, Handler] | None = None, name: str = "peer") -> Connection:
    reader, writer = await asyncio.open_unix_connection(path, limit=MAX_FRAME)
    conn = Connection(reader, writer, handlers, name=name)
    conn.spawn(conn.serve())
    return conn
