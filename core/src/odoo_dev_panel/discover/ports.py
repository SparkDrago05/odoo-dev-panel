"""D10: listening TCP ports, read from /proc/net, and conflicts between Odoo processes."""

from __future__ import annotations

import os

TCP_LISTEN = "0A"


def listening_ports(proc_root: str = "/proc") -> dict[int, int | None]:
    """Map listening port -> socket inode."""
    result: dict[int, int | None] = {}
    for name in ("tcp", "tcp6"):
        try:
            with open(f"{proc_root}/net/{name}") as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 9 and fields[3] == TCP_LISTEN:
                result[int(fields[1].rsplit(":", 1)[1], 16)] = int(fields[9])
    return result


def inode_owners(proc_root: str = "/proc") -> dict[int, int]:
    """Map socket inode -> pid, for processes whose fds we may read."""
    owners: dict[int, int] = {}
    try:
        pids = [n for n in os.listdir(proc_root) if n.isdigit()]
    except OSError:
        return owners
    for pid in pids:
        try:
            for fd in os.listdir(f"{proc_root}/{pid}/fd"):
                target = os.readlink(f"{proc_root}/{pid}/fd/{fd}")
                if target.startswith("socket:["):
                    owners[int(target[8:-1])] = int(pid)
        except OSError:
            continue
    return owners


def analyse(procs, proc_root: str = "/proc") -> dict:
    """Return listeners relevant to Odoo and conflicts.

    A conflict is a port wanted by two Odoo processes, or an Odoo port held by a pid that is not that process.
    """
    listeners = listening_ports(proc_root)
    owners = inode_owners(proc_root)
    wanted: dict[int, list[int]] = {}
    for proc in procs:
        if proc.port is not None:
            wanted.setdefault(proc.port, []).append(proc.pid)
    conflicts = []
    for port, pids in sorted(wanted.items()):
        holder = owners.get(listeners.get(port, -1))
        if len(pids) > 1:
            conflicts.append({"port": port, "kind": "shared", "pids": pids, "holder": holder})
        elif port in listeners and holder is not None and holder != pids[0] and not _is_child(holder, pids[0], proc_root):
            conflicts.append({"port": port, "kind": "foreign", "pids": pids, "holder": holder})
    return {
        "listening": sorted(listeners),
        "odoo": [
            {"port": p.port, "pid": p.pid, "listening": p.port in listeners} for p in procs if p.port is not None
        ],
        "conflicts": conflicts,
    }


def _is_child(pid: int, parent: int, proc_root: str) -> bool:
    for _ in range(8):  # workers and gevent children
        try:
            with open(f"{proc_root}/{pid}/stat", "rb") as fh:
                data = fh.read()
            pid = int(data[data.rfind(b")") + 2 :].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid == parent:
            return True
        if pid <= 1:
            return False
    return False
