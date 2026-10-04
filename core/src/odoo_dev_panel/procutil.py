"""Small /proc helpers shared by the agent and the CLI."""

from __future__ import annotations

import os


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
