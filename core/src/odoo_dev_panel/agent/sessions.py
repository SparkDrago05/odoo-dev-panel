"""Run sessions: processes started by the agent, owned by the agent's OS user.

Design rules:

* A session process is never tied to the agent's lifetime. It runs in its own
  session (setsid), reads /dev/null and writes straight to a log file, so an
  agent crash does not send it SIGHUP or break its output.
* Each session is persisted as ``<state>/sessions/<id>/session.json`` so that a
  restarted agent can re-adopt running processes.
* A process is identified by pid plus /proc start time, never by pid alone.
* Only interactive sessions (Odoo shell) get a PTY. The agent holds the PTY
  master and copies its output to the log file, so such a session takes input
  only while the agent that started it is alive. After an agent restart it is
  re-adopted read-only.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
import struct
import subprocess
import termios
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import procutil
from ..rpc import CONFLICT, INVALID_PARAMS, NOT_FOUND, RpcError

_logger = logging.getLogger(__name__)

RUNNING = "running"
STOPPING = "stopping"
EXITED = "exited"
LOST = "lost"  # session.json says running, but the process is gone or was replaced

PASSTHROUGH_ENV = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TZ")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Session:
    id: str
    user: str
    argv: list[str]
    cwd: str
    name: str
    log_path: str
    pid: int = 0
    pgid: int = 0
    starttime: int | None = None
    started_at: str = ""
    ended_at: str | None = None
    exit_code: int | None = None
    state: str = RUNNING
    adopted: bool = False
    meta: dict = field(default_factory=dict)  # tool-owned labels: instance, db, kind, port
    pty: bool = False

    def to_public(self) -> dict:
        data = asdict(self)
        try:
            data["log_size"] = os.path.getsize(self.log_path)
        except OSError:
            data["log_size"] = 0
        return data


@dataclass
class _Runtime:
    popen: subprocess.Popen | None = None
    pidfd: int | None = None
    master: int | None = None  # PTY master, only for interactive sessions started by this agent
    log_fd: int | None = None
    exited: asyncio.Event = field(default_factory=asyncio.Event)


class SessionManager:
    def __init__(self, state_dir: Path, user: str):
        self.state_dir = Path(state_dir)
        self.sessions_dir = self.state_dir / "sessions"
        self.user = user
        self.sessions: dict[str, Session] = {}
        self._runtime: dict[str, _Runtime] = {}
        self._listeners: list = []

    # -- persistence -------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def _save(self, session: Session) -> None:
        path = self._session_dir(session.id) / "session.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(session), indent=2))
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)

    def load(self) -> None:
        """Load persisted sessions and re-adopt the ones still running."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.sessions_dir.glob("*/session.json")):
            try:
                data = json.loads(path.read_text())
                session = Session(**data)
            except (OSError, ValueError, TypeError) as exc:
                _logger.warning("skipping unreadable session %s: %s", path, exc)
                continue
            self.sessions[session.id] = session
            if session.state in (RUNNING, STOPPING):
                if procutil.is_same_process(session.pid, session.starttime):
                    session.adopted = True
                    session.state = RUNNING
                    self._watch(session, popen=None)
                    _logger.info("re-adopted session %s (pid %s)", session.id, session.pid)
                else:
                    # The process ended while no agent was watching: the exit code is unknown.
                    session.state = LOST if procutil.proc_starttime(session.pid) else EXITED
                    session.ended_at = session.ended_at or _now()
                self._save(session)

    # -- events ------------------------------------------------------------

    def add_listener(self, callback) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, session: Session) -> None:
        for callback in list(self._listeners):
            try:
                callback(session)
            except Exception:  # noqa: BLE001
                _logger.exception("session listener failed")

    # -- lifecycle ---------------------------------------------------------

    def _watch(self, session: Session, popen: subprocess.Popen | None) -> None:
        runtime = _Runtime(popen=popen)
        self._runtime[session.id] = runtime
        try:
            runtime.pidfd = os.pidfd_open(session.pid)
        except ProcessLookupError:
            self._mark_exited(session, popen.poll() if popen else None)
            return
        loop = asyncio.get_running_loop()
        loop.add_reader(runtime.pidfd, self._on_pidfd, session.id)

    def _on_pidfd(self, session_id: str) -> None:
        session = self.sessions[session_id]
        runtime = self._runtime[session_id]
        code = None
        if runtime.popen is not None:
            code = runtime.popen.poll()
            if code is None:
                return  # spurious wake-up
        self._mark_exited(session, code)

    def _mark_exited(self, session: Session, code: int | None) -> None:
        runtime = self._runtime.get(session.id)
        if runtime and runtime.pidfd is not None:
            asyncio.get_running_loop().remove_reader(runtime.pidfd)
            os.close(runtime.pidfd)
            runtime.pidfd = None
        if runtime and runtime.master is not None:
            self._drain_pty(runtime)
            self._close_pty(runtime)
        session.state = EXITED
        session.exit_code = code
        session.ended_at = _now()
        self._save(session)
        if runtime:
            runtime.exited.set()
        _logger.info("session %s exited with %s", session.id, code)
        self._emit(session)

    # -- PTY ---------------------------------------------------------------

    def _on_pty(self, session_id: str) -> None:
        runtime = self._runtime[session_id]
        if not self._drain_pty(runtime):
            self._close_pty(runtime)  # EOF/EIO: every holder of the slave side is gone

    @staticmethod
    def _drain_pty(runtime: _Runtime) -> bool:
        """Copy what the PTY has to the log. Returns False at end of file."""
        while runtime.master is not None:
            try:
                data = os.read(runtime.master, 65536)
            except BlockingIOError:
                return True
            except OSError:
                return False
            if not data:
                return False
            os.write(runtime.log_fd, data)
        return False

    @staticmethod
    def _close_pty(runtime: _Runtime) -> None:
        if runtime.master is None:
            return
        try:
            asyncio.get_running_loop().remove_reader(runtime.master)
        except RuntimeError:
            pass
        os.close(runtime.master)
        os.close(runtime.log_fd)
        runtime.master = runtime.log_fd = None

    def _pty_runtime(self, session_id: str) -> _Runtime:
        session = self.get(session_id)
        runtime = self._runtime.get(session_id)
        if not session.pty:
            raise RpcError(CONFLICT, f"session {session_id} has no terminal")
        if session.state != RUNNING or runtime is None or runtime.master is None:
            raise RpcError(CONFLICT, f"session {session_id} takes no input (ended, or started by an earlier agent)")
        return runtime

    def write(self, session_id: str, data: str) -> int:
        runtime = self._pty_runtime(session_id)
        raw = data.encode()
        os.write(runtime.master, raw)
        return len(raw)

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        runtime = self._pty_runtime(session_id)
        fcntl.ioctl(runtime.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def start(self, argv: list[str], cwd: str | None = None, env: dict | None = None, name: str | None = None,
                    meta: dict | None = None, pty: bool = False, size: tuple[int, int] = (40, 120)) -> Session:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise RpcError(INVALID_PARAMS, "argv must be a non-empty list of strings")
        exe = argv[0]
        if not os.path.isabs(exe):
            raise RpcError(INVALID_PARAMS, f"argv[0] must be an absolute path: {exe}")
        if not os.access(exe, os.X_OK):
            raise RpcError(INVALID_PARAMS, f"not executable for {self.user}: {exe}")
        cwd = cwd or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            raise RpcError(INVALID_PARAMS, f"cwd does not exist: {cwd}")

        session_id = uuid.uuid4().hex[:12]
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, mode=0o750)
        log_path = session_dir / "output.log"

        child_env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env.update({str(k): str(v) for k, v in (env or {}).items()})

        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        master = None
        try:
            if pty:
                master, slave = os.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
                child_env.setdefault("TERM", "dumb")
                popen = subprocess.Popen(
                    argv, cwd=cwd, env=child_env, stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True, close_fds=True,
                    preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
                )
                os.close(slave)
            else:
                popen = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        except OSError as exc:
            if master is not None:
                os.close(master)
            os.close(log_fd)
            raise RpcError(INVALID_PARAMS, f"cannot start {exe}: {exc}") from exc
        if master is None:
            os.close(log_fd)

        session = Session(
            id=session_id,
            user=self.user,
            argv=list(argv),
            cwd=cwd,
            name=name or os.path.basename(exe),
            log_path=str(log_path),
            pid=popen.pid,
            pgid=popen.pid,
            starttime=procutil.proc_starttime(popen.pid),
            started_at=_now(),
            meta=dict(meta or {}),
            pty=bool(pty),
        )
        self.sessions[session_id] = session
        self._save(session)
        self._watch(session, popen)
        runtime = self._runtime[session_id]
        if master is not None and session.state == RUNNING:
            os.set_blocking(master, False)
            runtime.master, runtime.log_fd = master, log_fd
            asyncio.get_running_loop().add_reader(master, self._on_pty, session_id)
        elif master is not None:
            os.close(master)
            os.close(log_fd)
        _logger.info("started session %s pid %s: %s", session_id, popen.pid, argv)
        self._emit(session)
        return session

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise RpcError(NOT_FOUND, f"no session {session_id}")
        return session

    def list(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.started_at, reverse=True)

    async def stop(self, session_id: str, timeout: float = 15.0) -> Session:
        session = self.get(session_id)
        if session.state not in (RUNNING, STOPPING):
            return session
        if not procutil.is_same_process(session.pid, session.starttime):
            # Never signal a pid that no longer belongs to this session.
            raise RpcError(CONFLICT, f"session {session_id} process {session.pid} is not the one we started")
        runtime = self._runtime[session.id]
        session.state = STOPPING
        self._save(session)
        self._emit(session)
        self._signal_group(session.pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(runtime.exited.wait(), timeout)
        except asyncio.TimeoutError:
            _logger.warning("session %s did not stop in %ss, sending SIGKILL", session.id, timeout)
            self._signal_group(session.pgid, signal.SIGKILL)
            await asyncio.wait_for(runtime.exited.wait(), 5)
        # Workers may outlive the leader; clean up what is left of the group.
        if procutil.group_alive(session.pgid):
            await asyncio.sleep(0.5)
            if procutil.group_alive(session.pgid):
                self._signal_group(session.pgid, signal.SIGKILL)
        return session

    @staticmethod
    def _signal_group(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    def read(self, session_id: str, offset: int, limit: int = 65536) -> tuple[bytes, int]:
        session = self.get(session_id)
        try:
            with open(session.log_path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(limit)
        except FileNotFoundError:
            return b"", offset
        return data, offset + len(data)

    def running_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.state in (RUNNING, STOPPING))
