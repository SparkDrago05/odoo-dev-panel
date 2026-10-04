"""Build a ``DbContext`` for one installation from a discovery snapshot. Secrets are read here and go no further
than the plan's ``conn``."""

from __future__ import annotations

import asyncio
import os
import pwd
import shutil
import subprocess
from pathlib import Path

from .. import client
from ..discover import configs, scan
from ..discover.filestore import filestore_base
from . import commands as cmd
from .ops import DbContext


class NotFound(Exception):
    pass


def _activity(conn: cmd.Conn | None):
    def count(database: str) -> int | None:
        if conn is None or not shutil.which("psql"):
            return None
        env = {**os.environ, **conn.env()}
        try:
            out = subprocess.run(cmd.psql_argv(conn, "postgres", "-A", "-t", "-c", cmd.activity_sql(database)),
                                 capture_output=True, text=True, timeout=15, env=env, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        text = out.stdout.strip()
        return int(text) if out.returncode == 0 and text.isdigit() else None
    return count


def server_major(conn: cmd.Conn | None) -> int | None:
    if conn is None or not shutil.which("psql"):
        return None
    try:
        out = subprocess.run(cmd.psql_argv(conn, "postgres", "-A", "-t", "-c", "SHOW server_version_num"),
                             capture_output=True, text=True, timeout=15, env={**os.environ, **conn.env()},
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    return int(text) // 10000 if out.returncode == 0 and text.isdigit() else None


def _free(path: str) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _first_conn(snap: dict, root: str, entry: dict, owner: str | None) -> tuple[cmd.Conn | None, str | None]:
    """The connection of the config that listed the databases, else of the first config with a role."""
    paths_ = [entry["via"]] if entry.get("via") else [i["path"] for i in snap["instances"] if i.get("installation") == root]
    for path in paths_:
        conn = cmd.conn_from_options(configs.parse_config(Path(path)) or {}, owner)
        if conn:
            return conn, path
    return None, None


def build(snap: dict, root: str, agent_running: bool | None, recipes_dir: Path | None = None,
          via_agent: dict | None = None) -> tuple[dict, DbContext]:
    """``via_agent``: what ``_query_as_owner`` read through the run-as user's agent (peer authentication)."""
    root = os.path.normpath(root)
    inst = next((i for i in snap["installations"] if i["root"] == root), None)
    if inst is None:
        raise NotFound(f"{root} is not a discovered Odoo installation")
    entry = next((d for d in snap["databases"] if d["installation"] == root), None) or {}
    conn, via = _first_conn(snap, root, entry, inst.get("owner"))
    mine = [i for i in snap["instances"] if i.get("installation") == root]
    first_via = [i for i in mine if i["path"] == via]  # the config that works comes first
    bases = list(dict.fromkeys(filestore_base(i["options"], inst.get("home")) for i in first_via + mine))
    databases, error = entry.get("databases", []), entry.get("error")
    activity, major = _activity(conn), server_major(conn)
    if via_agent is not None:
        databases, error = via_agent["databases"], via_agent["error"]
        counts = via_agent["activity"]
        activity, major = (lambda database: counts.get(database, 0)), via_agent["server_major"]
    ctx = DbContext(
        databases=databases, conn=conn, bases=bases or [filestore_base({}, inst.get("home"))],
        processes=snap["processes"], agent_running=agent_running, activity=activity,
        recipes_dir=recipes_dir, free_bytes=_free, server_major=major,
    )
    ctx.listing_error = error  # type: ignore[attr-defined]
    return inst, ctx


async def _psql_as_owner(agent, conn: cmd.Conn, root: str, sql: str) -> list[str]:
    from ..provision.execute import _agent_session
    from .ops import DbError, resolve

    lines: list[str] = []
    argv = resolve(cmd.psql_argv(conn, "postgres", "-A", "-t", "-F", "\t", "-c", sql))
    code, _ = await _agent_session(agent, "query", argv, root, lambda e: lines.append(e["text"]) if e["status"] == "output" else None)
    if code != 0:
        raise DbError((lines or [f"psql ended with exit code {code}"])[-1])
    return lines


# Prints each argument that is a directory.
_DIRS_SCRIPT = 'for p; do [ -d "$p" ] && printf "%s\\n" "$p"; done; exit 0\n'


async def _filestores_as_owner(agent, root: str, databases: list[dict]) -> None:
    """Resolve the filestores the dev user cannot see (a private data_dir) as the run-as user."""
    from ..provision.execute import _agent_session

    unknown = [f["path"] for d in databases for f in d["filestores"] if f["exists"] is None]
    if not unknown:
        return
    lines: list[str] = []
    code, _ = await _agent_session(agent, "filestores", ["/bin/bash", "-c", _DIRS_SCRIPT, "odp", *unknown], root,
                                   lambda e: lines.append(e["text"]) if e["status"] == "output" else None)
    if code != 0:
        return
    found = set(lines)
    for d in databases:
        for f in d["filestores"]:
            if f["exists"] is None:
                f["exists"] = f["path"] in found
        hits = [f["path"] for f in d["filestores"] if f["exists"]]
        d["filestore"] = (hits or [d["filestore"]])[0]
        d["filestore_exists"] = bool(hits) if all(f["exists"] is not None for f in d["filestores"]) else d["filestore_exists"]


async def _query_as_owner(snap: dict, root: str, owner: str) -> dict:
    """Databases, sessions per database and server version, read by the run-as user's agent. Discovery runs as
    the dev user, and peer authentication lets only the role's own OS user in."""
    from ..discover.databases import QUERY, discover_databases
    from ..discover.model import Installation, Instance

    inst = next(i for i in snap["installations"] if i["root"] == root)
    entry = next((d for d in snap["databases"] if d["installation"] == root), None) or {}
    conn, _ = _first_conn(snap, root, entry, owner)
    assert conn is not None
    agent = await client.connect(owner)
    try:
        rows = [(n, int(sz or 0)) for n, _, sz in (line.partition("\t") for line in await _psql_as_owner(agent, conn, root, QUERY)) if n]
        counts = {n: int(c) for n, _, c in (line.partition("\t") for line in await _psql_as_owner(agent, conn, root, cmd.ACTIVITY_ALL_SQL)) if n}
        version = await _psql_as_owner(agent, conn, root, "SHOW server_version_num")
        known = set(Installation.__dataclass_fields__)
        inst_known = set(Instance.__dataclass_fields__)
        installs = [Installation(**{k: v for k, v in inst.items() if k in known})]
        instances = [Instance(**{k: v for k, v in i.items() if k in inst_known}) for i in snap["instances"] if i.get("installation") == root]
        listed = discover_databases(installs, instances, reader=lambda *_a: rows)
        await _filestores_as_owner(agent, root, listed[0]["databases"])
    finally:
        await agent.close()
    major = int(version[0]) // 10000 if version and version[0].strip().isdigit() else None
    return {"databases": listed[0]["databases"], "error": None, "activity": counts, "server_major": major}


async def prepare(root: str) -> tuple[dict, DbContext]:
    snap = await asyncio.to_thread(scan.scan, None, True)
    norm = os.path.normpath(root)
    owner = next((i.get("owner") for i in snap["installations"] if i["root"] == norm), None)
    agent = await client.agent_status(owner) if owner else {"state": "stopped"}
    running = agent["state"] == "running"
    via_agent = None
    entry = next((d for d in snap["databases"] if d["installation"] == norm), None) or {}
    conn, _ = _first_conn(snap, norm, entry, owner) if any(i["root"] == norm for i in snap["installations"]) else (None, None)
    if owner and conn and conn.needs_os_user(pwd.getpwuid(os.getuid()).pw_name):
        if running:
            try:
                via_agent = await _query_as_owner(snap, norm, owner)
            except Exception as exc:  # noqa: BLE001 - reported like a failed listing
                via_agent = {"databases": [], "error": f"listing through the agent of {owner} failed: {exc}",
                             "activity": {}, "server_major": None}
        else:
            via_agent = {"databases": [], "error": f"{conn.user} uses peer authentication: unlock the agent of {owner} "
                         "to list its databases", "activity": {}, "server_major": None}
    return await asyncio.to_thread(build, snap, root, running, None, via_agent)
