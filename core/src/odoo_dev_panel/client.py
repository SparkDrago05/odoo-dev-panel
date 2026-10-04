"""Client side of the agent protocol, shared by the sidecar and the CLI."""

from __future__ import annotations

import asyncio
import grp
import pwd
from pathlib import Path

from . import paths, rpc


async def connect(user: str, handlers=None, socket_dir: Path | None = None) -> rpc.Connection:
    path = Path(socket_dir or paths.socket_dir()) / f"{user}.sock"
    if not path.exists():
        raise rpc.RpcError(rpc.UNAVAILABLE, f"no agent for {user} (missing {path})")
    try:
        conn = await rpc.open_unix(str(path), handlers, name=f"agent:{user}")
    except PermissionError as exc:
        raise rpc.RpcError(rpc.FORBIDDEN, f"cannot open {path}: {exc}") from exc
    except OSError as exc:
        raise rpc.RpcError(rpc.UNAVAILABLE, f"agent for {user} is not running: {exc}") from exc
    return conn


def agent_sockets(socket_dir: Path | None = None) -> dict[str, Path]:
    directory = Path(socket_dir or paths.socket_dir())
    try:
        return {p.stem: p for p in sorted(directory.glob("*.sock"))}
    except PermissionError:
        return {}


def group_members(group: str = paths.DEFAULT_GROUP) -> set[str]:
    try:
        entry = grp.getgrnam(group)
    except KeyError:
        return set()
    members = set(entry.gr_mem)
    for account in pwd.getpwall():
        if account.pw_gid == entry.gr_gid:
            members.add(account.pw_name)
    return members


def _uid_min(login_defs: str = "/etc/login.defs") -> int:
    try:
        for line in Path(login_defs).read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "UID_MIN" and fields[1].isdigit():
                return int(fields[1])
    except OSError:
        pass
    return 1000


def candidate_users(group: str = paths.DEFAULT_GROUP, extra: set[str] | None = None) -> list[str]:
    """Users that may run an agent: users with an agent socket, system accounts in the odoo-dev group,
    plus ``extra`` (run-as users of discovered installations, which may not be members yet).
    Regular accounts in the group are developers, not version users, so they are left out."""
    users = set(agent_sockets()) | set(extra or ())
    try:
        members = grp.getgrnam(group).gr_mem
    except KeyError:
        members = []
    uid_min = _uid_min()
    for member in members:
        try:
            if pwd.getpwnam(member).pw_uid < uid_min:
                users.add(member)
        except KeyError:
            pass
    return sorted(users)


async def agent_status(user: str, timeout: float = 2.0) -> dict:
    """Return {'user', 'state', 'info'|'error'} without raising."""
    try:
        conn = await asyncio.wait_for(connect(user), timeout)
    except (rpc.RpcError, asyncio.TimeoutError) as exc:
        return {"user": user, "state": "stopped", "error": str(exc)}
    try:
        info = await conn.request("agent.info", timeout=timeout)
        return {"user": user, "state": "running", "info": info}
    except (rpc.RpcError, rpc.ConnectionClosed, asyncio.TimeoutError) as exc:
        return {"user": user, "state": "error", "error": str(exc)}
    finally:
        await conn.close()


async def wait_running(user: str, timeout: float = 10.0) -> dict:
    """Poll until the agent of ``user`` answers (systemd-run returns before the socket exists)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    status = await agent_status(user)
    while status["state"] != "running" and loop.time() < deadline:
        await asyncio.sleep(0.2)
        status = await agent_status(user)
    return status
