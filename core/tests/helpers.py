import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

CORE_SRC = Path(__file__).resolve().parents[1] / "src"
REPO = Path(__file__).resolve().parents[2]
FAKE_ODOO = REPO / "spike" / "fake_odoo.py"


def core_env(**extra) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CORE_SRC)
    env.update(extra)
    return env


class AgentProcess:
    """An agent running in the foreground as the current user, in temporary directories."""

    def __init__(self, root: Path):
        self.socket_dir = root / "run"
        self.state_dir = root / "state"
        self.proc: subprocess.Popen | None = None

    @property
    def socket_path(self) -> Path:
        import pwd

        return self.socket_dir / f"{pwd.getpwuid(os.getuid()).pw_name}.sock"

    def start(self) -> None:
        self._stderr = open(self.state_dir.parent / "agent-stderr.log", "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "odoo_dev_panel", "agent", "serve", "--foreground",
             "--socket-dir", str(self.socket_dir), "--state-dir", str(self.state_dir), "--allow-group", ""],
            env=core_env(),
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        wait_for(self._accepting, 10, "agent socket")

    def _accepting(self) -> bool:
        import socket

        if self.proc.poll() is not None:
            raise AssertionError(f"agent exited with {self.proc.returncode}")
        with socket.socket(socket.AF_UNIX) as sock:
            try:
                sock.connect(str(self.socket_path))
            except OSError:
                return False
        return True

    def kill9(self) -> None:
        self.proc.kill()
        self.proc.wait()
        self._close_stderr()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)
        self._close_stderr()

    def _close_stderr(self) -> None:
        handle = getattr(self, "_stderr", None)
        if handle and not handle.closed:
            handle.close()


def wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def run(coro):
    return asyncio.run(coro)
