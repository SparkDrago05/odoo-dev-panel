"""Per-user agent: runs as an Odoo version user (for example odoo19) and owns its sessions.

Started once per user per boot, normally through ``sudo -u odooNN odp agent serve``.
It detaches from the caller, binds ``<socket_dir>/<user>.sock`` and serves JSON-RPC.
Only peers whose uid passes :meth:`Agent.peer_allowed` may connect.
"""

from __future__ import annotations

import asyncio
import codecs
import grp
import logging
import os
import pwd
import signal
import socket
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__, paths
from ..rpc import FORBIDDEN, INVALID_PARAMS, Connection, RpcError
from .sessions import EXITED, LOST, SessionManager

_logger = logging.getLogger(__name__)

FOLLOW_CHUNK = 64 * 1024
FOLLOW_POLL = 0.2


def _peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


class Agent:
    def __init__(self, socket_dir: Path, state_dir: Path, allow_group: str | None, allow_uids: set[int]):
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.socket_dir = Path(socket_dir)
        self.socket_path = self.socket_dir / f"{self.user}.sock"
        self.manager = SessionManager(state_dir, self.user)
        self.allow_group = allow_group
        self.allow_uids = set(allow_uids)
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._server: asyncio.AbstractServer | None = None
        self._stop = asyncio.Event()
        self._connections: set[Connection] = set()
        self.manager.add_listener(self._broadcast_state)

    # -- access control ----------------------------------------------------

    def peer_allowed(self, uid: int) -> bool:
        if uid in (0, os.getuid()) or uid in self.allow_uids:
            return True
        if not self.allow_group:
            return False
        try:
            group = grp.getgrnam(self.allow_group)
            account = pwd.getpwuid(uid)
        except KeyError:
            return False
        return account.pw_name in group.gr_mem or account.pw_gid == group.gr_gid

    # -- server ------------------------------------------------------------

    async def start(self) -> None:
        self.manager.load()
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if await _socket_alive(self.socket_path):
                raise RuntimeError(f"an agent is already listening on {self.socket_path}")
            self.socket_path.unlink()
        old_umask = os.umask(0o117)
        try:
            self._server = await asyncio.start_unix_server(self._on_client, path=str(self.socket_path), limit=2**26)
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o660)
        if self.allow_group:
            try:
                os.chown(self.socket_path, -1, grp.getgrnam(self.allow_group).gr_gid)
            except (KeyError, PermissionError) as exc:
                _logger.warning("cannot set socket group to %s: %s", self.allow_group, exc)
        _logger.info("agent for %s listening on %s", self.user, self.socket_path)

    async def wait_closed(self) -> None:
        await self._stop.wait()
        if self._server:
            self._server.close()
        # Close clients before wait_closed: since Python 3.12 it waits for every open connection,
        # so a connected app would keep a stopped agent alive forever.
        for conn in list(self._connections):
            await conn.close()
        if self._server:
            await self._server.wait_closed()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        _logger.info("agent for %s stopped; %d session(s) left running", self.user, self.manager.running_count())

    def shutdown(self) -> None:
        self._stop.set()

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        uid = _peer_uid(writer.get_extra_info("socket"))
        if not self.peer_allowed(uid):
            _logger.warning("refused connection from uid %s", uid)
            conn = Connection(reader, writer, name=f"uid{uid}")
            await conn.notify("agent.refused", {"reason": f"uid {uid} is not allowed"})
            await conn.close()
            return
        conn = Connection(reader, writer, self._handlers(), name=f"uid{uid}")
        conn.context["uid"] = uid
        conn.context["follows"] = {}
        self._connections.add(conn)
        conn.on_close(lambda: self._connections.discard(conn))
        await conn.serve()

    def _broadcast_state(self, session) -> None:
        payload = session.to_public()
        for conn in list(self._connections):
            if conn.context.get("watch_state"):
                conn.spawn(_quiet(conn.notify("session.state", payload)))

    # -- handlers ----------------------------------------------------------

    def _handlers(self):
        return {
            "agent.info": self.h_info,
            "agent.shutdown": self.h_shutdown,
            "session.start": self.h_start,
            "session.list": self.h_list,
            "session.get": self.h_get,
            "session.stop": self.h_stop,
            "process.stop": self.h_process_stop,
            "session.read": self.h_read,
            "session.follow": self.h_follow,
            "session.unfollow": self.h_unfollow,
            "session.watch": self.h_watch,
            "session.write": self.h_write,
            "session.resize": self.h_resize,
        }

    async def h_info(self, params, conn):
        return {
            "user": self.user,
            "uid": os.getuid(),
            "pid": os.getpid(),
            "version": __version__,
            "started_at": self.started_at,
            "socket": str(self.socket_path),
            "running_sessions": self.manager.running_count(),
        }

    async def h_shutdown(self, params, conn):
        if conn.context.get("uid") not in (0, os.getuid()) and not self.peer_allowed(conn.context.get("uid", -1)):
            raise RpcError(FORBIDDEN, "not allowed")
        asyncio.get_running_loop().call_later(0.1, self.shutdown)
        return {"stopping": True, "running_sessions": self.manager.running_count()}

    async def h_start(self, params, conn):
        params = params or {}
        session = await self.manager.start(
            params.get("argv"), cwd=params.get("cwd"), env=params.get("env"), name=params.get("name"),
            meta=params.get("meta") if isinstance(params.get("meta"), dict) else None,
            pty=bool(params.get("pty")),
            size=_size(params),
        )
        return session.to_public()

    async def h_list(self, params, conn):
        return [s.to_public() for s in self.manager.list()]

    async def h_get(self, params, conn):
        return self.manager.get(_session_id(params)).to_public()

    async def h_stop(self, params, conn):
        timeout = float((params or {}).get("timeout", 15))
        session = await self.manager.stop(_session_id(params), timeout=timeout)
        return session.to_public()

    async def h_process_stop(self, params, conn):
        """Stop an Odoo process this agent's user owns but did not start (started in a terminal, by cron...)."""
        from .. import procutil

        p = params or {}
        try:
            return await procutil.stop_odoo_pid(p.get("pid"), p.get("starttime"), float(p.get("timeout", 15)),
                                                bool(p.get("force")))
        except procutil.StopError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc

    async def h_read(self, params, conn):
        params = params or {}
        offset = int(params.get("offset", 0))
        limit = min(int(params.get("limit", FOLLOW_CHUNK)), 4 * FOLLOW_CHUNK)
        data, new_offset = self.manager.read(_session_id(params), offset, limit)
        return {"data": data.decode("utf-8", "replace"), "offset": new_offset}

    async def h_write(self, params, conn):
        data = (params or {}).get("data")
        if not isinstance(data, str):
            raise RpcError(INVALID_PARAMS, "data must be a string")
        return {"written": self.manager.write(_session_id(params), data)}

    async def h_resize(self, params, conn):
        self.manager.resize(_session_id(params), *_size(params))
        return True

    async def h_watch(self, params, conn):
        conn.context["watch_state"] = bool((params or {}).get("enabled", True))
        return True

    async def h_follow(self, params, conn):
        """Stream ``session.output`` notifications from a byte offset until unfollowed or the session ends."""
        session_id = _session_id(params)
        session = self.manager.get(session_id)
        offset = int((params or {}).get("offset", 0))
        follows = conn.context["follows"]
        old = follows.pop(session_id, None)
        if old:
            old.cancel()
        follows[session_id] = conn.spawn(self._follow(conn, session, offset))
        return {"id": session_id, "offset": offset}

    async def h_unfollow(self, params, conn):
        task = conn.context["follows"].pop(_session_id(params), None)
        if task:
            task.cancel()
        return True

    async def _follow(self, conn: Connection, session, offset: int) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                data, new_offset = self.manager.read(session.id, offset, FOLLOW_CHUNK)
                if data:
                    offset = new_offset
                    text = decoder.decode(data)
                    if text:
                        await conn.notify("session.output", {"id": session.id, "offset": offset, "data": text})
                    continue
                if session.state in (EXITED, LOST):
                    await conn.notify("session.ended", session.to_public())
                    return
                await asyncio.sleep(FOLLOW_POLL)
        finally:
            if conn.context["follows"].get(session.id) is asyncio.current_task():
                conn.context["follows"].pop(session.id, None)


def _size(params) -> tuple[int, int]:
    rows, cols = (params or {}).get("rows", 40), (params or {}).get("cols", 120)
    if not all(isinstance(v, int) and 1 <= v <= 1000 for v in (rows, cols)):
        raise RpcError(INVALID_PARAMS, "rows and cols must be integers from 1 to 1000")
    return rows, cols


def _session_id(params) -> str:
    session_id = (params or {}).get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RpcError(INVALID_PARAMS, "id is required")
    return session_id


async def _quiet(coro):
    try:
        await coro
    except Exception:  # noqa: BLE001
        pass


async def _socket_alive(path: Path) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 2)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def _run(agent: Agent, ready_fd: int | None) -> int:
    try:
        await agent.start()
    except Exception as exc:  # noqa: BLE001
        _report_ready(ready_fd, f"error: {exc}")
        raise
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, agent.shutdown)
    _report_ready(ready_fd, f"ok {os.getpid()} {agent.socket_path}")
    await agent.wait_closed()
    return 0


def _report_ready(fd: int | None, message: str) -> None:
    if fd is None:
        return
    try:
        os.write(fd, message.encode() + b"\n")
        os.close(fd)
    except OSError:
        pass


def _setup_logging(log_file: Path | None) -> None:
    handler: logging.Handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def serve(
    socket_dir: str | None = None,
    state_dir: str | None = None,
    allow_group: str | None = paths.DEFAULT_GROUP,
    allow_uids: set[int] | None = None,
    foreground: bool = False,
) -> int:
    """Entry point of ``odp agent serve``. Detaches unless foreground is set.

    In detached mode the calling process exits only after the socket is bound,
    so a caller such as ``sudo -u odoo19 odp agent serve`` learns whether start-up worked.
    """
    socket_dir_p = Path(socket_dir or paths.socket_dir())
    state_dir_p = Path(state_dir or paths.agent_state_dir())
    log_file = state_dir_p / "agent.log"

    if foreground:
        _setup_logging(None)
        agent = Agent(socket_dir_p, state_dir_p, allow_group, allow_uids or set())
        return asyncio.run(_run(agent, None))

    read_fd, write_fd = os.pipe()
    if os.fork() > 0:
        os.close(write_fd)
        with os.fdopen(read_fd) as fh:
            message = fh.readline().strip()
        if message.startswith("ok"):
            print(f"agent started: {message[3:]}")
            return 0
        print(f"agent failed to start: {message or 'no response'}", file=sys.stderr)
        return 1

    # First child: new session, then fork again so the agent can never regain a terminal.
    os.close(read_fd)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir("/")
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.close(devnull)
    _setup_logging(log_file)
    try:
        agent = Agent(socket_dir_p, state_dir_p, allow_group, allow_uids or set())
        code = asyncio.run(_run(agent, write_fd))
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("agent crashed")
        _report_ready(write_fd, "error: see agent.log")
        code = 1
    os._exit(code)
