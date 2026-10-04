"""Read-only queries of one database for the module overlay and the database compare.

The same connection rules as the database actions: a role that authenticates by OS user (peer) is read through the
run-as user's agent, a role with a password over TCP as the developer, and ``docker:<container>`` through
``docker exec`` in the database container. The database name goes into argv only (``-d``), never into SQL, and
must be one the installation's role owns.
"""

from __future__ import annotations

import asyncio
import os
import pwd
import shutil

from . import client
from .database import commands as cmd, context
from .provision.execute import ProvisionError, _agent_session

MODULE_SQL = "SELECT name, state, COALESCE(latest_version, '') FROM ir_module_module ORDER BY name"


class QueryError(Exception):
    pass


async def _local(argv: list[str], env: dict[str, str]) -> list[str]:
    found = shutil.which(argv[0])
    if not found:
        raise QueryError(f"{argv[0]} not found. Install the PostgreSQL client: sudo apt-get install postgresql-client")
    proc = await asyncio.create_subprocess_exec(found, *argv[1:], stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                env={**os.environ, **env})
    out, err = await asyncio.wait_for(proc.communicate(), 60)
    if proc.returncode != 0:
        raise QueryError((err.decode(errors="replace").strip().splitlines() or [f"{argv[0]} failed"])[-1])
    return out.decode(errors="replace").splitlines()


async def rows(root: str, database: str, sql: str) -> list[list[str]]:
    """Tab-separated rows of ``sql`` in ``database`` of an installation root or ``docker:<container>``."""
    if root.startswith("docker:"):
        from . import dockerdb
        from .dockerops import DockerError

        try:
            _c, dctx = await dockerdb.prepare(root[len("docker:"):])
        except DockerError as exc:
            raise QueryError(str(exc)) from exc
        if dctx.list_error:
            raise QueryError(dctx.list_error)
        if database not in {d["name"] for d in dctx.databases}:
            raise QueryError(f"{database} is not a database in {dctx.db_container}")
        code, out, err = await dockerdb._sh(dockerdb.psql(dctx, database, "-A", "-t", "-F", "\t", "-c", sql), timeout=60)
        if code != 0:
            raise QueryError((err or ["psql failed"])[-1])
        return [line.split("\t") for line in out]
    try:
        inst, ctx = await context.prepare(root)
    except context.NotFound as exc:
        raise QueryError(str(exc)) from exc
    if ctx.conn is None:
        raise QueryError("no config of this installation has a db_user")
    if database not in {d["name"] for d in ctx.databases}:
        raise QueryError(f"{database} is not a database of this installation's role"
                         + (f" ({getattr(ctx, 'listing_error', None)})" if getattr(ctx, "listing_error", None) else ""))
    argv = cmd.psql_argv(ctx.conn, database, "-A", "-t", "-F", "\t", "-c", sql)
    me = pwd.getpwuid(os.getuid()).pw_name
    if not ctx.conn.needs_os_user(me):
        return [line.split("\t") for line in await _local(argv, ctx.conn.env())]
    owner = inst.get("owner")
    if not owner or not ctx.agent_running:
        raise QueryError(f"{ctx.conn.user} uses peer authentication: unlock the agent of {owner or 'its run-as user'}")
    agent = await client.connect(owner)
    lines: list[str] = []
    try:
        from .database.ops import resolve

        code, _ = await _agent_session(agent, "query", resolve(argv), root,
                                       lambda e: lines.append(e["text"]) if e["status"] == "output" else None)
    except ProvisionError as exc:
        raise QueryError(str(exc)) from exc
    finally:
        await agent.close()
    if code != 0:
        raise QueryError((lines or ["psql failed"])[-1])
    return [line.split("\t") for line in lines]


async def installed_modules(root: str, database: str) -> dict[str, dict]:
    """``{module: {"state", "version"}}`` from ``ir_module_module``."""
    out: dict[str, dict] = {}
    for row in await rows(root, database, MODULE_SQL):
        if len(row) >= 2 and row[0]:
            out[row[0]] = {"state": row[1], "version": row[2] if len(row) > 2 else ""}
    return out
