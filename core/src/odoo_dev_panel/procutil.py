"""Small /proc helpers shared by the agent and the CLI."""

from __future__ import annotations

import asyncio
import os
import signal


def proc_starttime(pid: int) -> int | None:
    """Return the start time of a process in clock ticks since boot, or None.

    Together with the pid this identifies a process uniquely, which guards
    against acting on a recycled pid.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    # The comm field may contain spaces and parentheses: split after the last ')'.
    fields = data[data.rfind(b")") + 2 :].split()
    # fields[0] is field 3 (state); starttime is field 22.
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def proc_state(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return data[data.rfind(b")") + 2 :].split()[0].decode()


def is_same_process(pid: int, starttime: int | None) -> bool:
    """True if pid is alive, not a zombie, and still the process we started."""
    if not pid or starttime is None:
        return False
    current = proc_starttime(pid)
    if current is None or current != starttime:
        return False
    return proc_state(pid) != "Z"


def group_alive(pgid: int) -> bool:
    """True if any process in the process group still exists (and we may signal it)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cmdline(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except OSError:
        return []


class StopError(Exception):
    pass


async def stop_odoo_pid(pid: int, starttime: int | None, timeout: float = 15.0, force: bool = False) -> dict:
    """SIGTERM one Odoo process (its workers follow it), wait, and SIGKILL only when ``force`` is set.

    The pid must still be the process that was discovered (same start time) and must still look like Odoo.
    Only that pid is signalled, never its process group: a shell or a unit may share the group.
    """
    from .discover.processes import is_odoo_argv

    if not isinstance(pid, int) or pid <= 1:
        raise StopError("bad pid")
    if proc_starttime(pid) is None:
        return {"stopped": True, "signal": None, "note": "already gone"}
    if starttime is not None and proc_starttime(pid) != starttime:
        raise StopError(f"pid {pid} is not the process that was discovered")
    if not is_odoo_argv(cmdline(pid)):
        raise StopError(f"pid {pid} is not an Odoo process")

    def gone() -> bool:
        return proc_starttime(pid) is None or proc_state(pid) == "Z"

    async def wait(seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            if gone():
                return True
            await asyncio.sleep(0.25)
        return gone()

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"stopped": True, "signal": "TERM", "note": "already gone"}
    except PermissionError as exc:
        raise StopError(f"not allowed to signal pid {pid}: {exc}") from exc
    if await wait(timeout):
        return {"stopped": True, "signal": "TERM"}
    if not force:
        return {"stopped": False, "signal": "TERM", "note": f"still running after {timeout:g}s (Odoo finishes requests first)"}
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return {"stopped": await wait(5), "signal": "KILL"}
