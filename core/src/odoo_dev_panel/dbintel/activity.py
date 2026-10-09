"""Z3: what is going on now. PostgreSQL sessions, blocked sessions, long transactions and connection use, read through
the installation's role; Odoo processes' CPU and memory from /proc; and a short verdict on where a slowdown comes from.

Without ``pg_monitor`` the role sees the text and state of its own sessions only; others are counted and marked
hidden. Cancel (pg_cancel_backend) is allowed on a running query of the role's own sessions only.
"""

from __future__ import annotations

import asyncio
import os
import re
import time

from .. import dbquery

TIMEOUT_MS = 10_000
LONG_QUERY_S = 10
LONG_XACT_S = 300
IDLE_XACT_S = 60
CPU_BUSY = 80.0
_ROLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]{0,62}$")

_CLIENT = "(a.backend_type = 'client backend' OR (a.backend_type IS NULL AND a.usename IS NOT NULL))"
SESSIONS_SQL = (
    "SELECT a.pid, a.datname AS database, a.usename AS role, a.application_name AS application, "
    "a.client_addr::text AS client, a.state, a.wait_event_type, a.wait_event, "
    "extract(epoch FROM now() - a.backend_start)::int AS connected_s, "
    "extract(epoch FROM now() - a.xact_start)::int AS xact_s, "
    "extract(epoch FROM now() - a.query_start)::int AS query_s, "
    "left(a.query, 4000) AS query, pg_blocking_pids(a.pid) AS blocked_by, a.usename = current_user AS mine "
    # without pg_monitor, other roles' rows have backend_type NULL (and no query): keep them as hidden sessions
    f"FROM pg_stat_activity a WHERE a.pid <> pg_backend_pid() AND {_CLIENT} "
    "ORDER BY a.query_start NULLS LAST")
SERVER_SQL = (
    "SELECT current_setting('max_connections')::int AS max_connections, "
    "current_setting('server_version') AS version, current_user AS role, "
    "pg_has_role(current_user, 'pg_monitor', 'MEMBER') AS monitor, "
    f"(SELECT count(*) FROM pg_stat_activity a WHERE {_CLIENT}) AS connections")
LOCKS_SQL = "SELECT mode, granted, count(*) AS n FROM pg_locks GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20"


class ActivityError(ValueError):
    pass


async def postgres(root: str, database: str) -> dict:
    """Sessions, server facts and lock counts, seen from ``database`` (any database of the role: the views are
    cluster-wide)."""
    try:
        server, sessions, locks = (await dbquery.json_rows(root, database, SERVER_SQL, TIMEOUT_MS),
                                   await dbquery.json_rows(root, database, SESSIONS_SQL, TIMEOUT_MS),
                                   await dbquery.json_rows(root, database, LOCKS_SQL, TIMEOUT_MS))
    except dbquery.QueryError as exc:
        raise ActivityError(str(exc)) from exc
    s = server[0] if server else {}
    for row in sessions:
        row["hidden"] = not row["mine"] and (row.get("query") or "").startswith("<insufficient privilege>")
        if row["hidden"]:
            row["query"] = None
    return {"server": s, "sessions": sessions, "locks": locks}


async def cancel(root: str, database: str, pid) -> dict:
    """Cancel the running query of one of the role's own sessions. Never terminates the connection."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ActivityError("pid is a process id")
    try:
        rows = await dbquery.json_rows(
            root, database,
            f"SELECT pg_cancel_backend(a.pid) AS cancelled FROM pg_stat_activity a "
            f"WHERE a.pid = {pid} AND a.usename = current_user AND a.state = 'active'", TIMEOUT_MS)
    except dbquery.QueryError as exc:
        raise ActivityError(str(exc)) from exc
    if not rows:
        raise ActivityError(f"session {pid} is not a running query of this installation's role")
    return {"pid": pid, "cancelled": bool(rows[0]["cancelled"])}


# -- Odoo processes ----------------------------------------------------------------------

def _ticks(pid: int, proc_root: str) -> tuple[int, int] | None:
    """(utime+stime, rss pages) of one process, or None when it is gone."""
    try:
        with open(f"{proc_root}/{pid}/stat", "rb") as fh:
            fields = fh.read().rsplit(b")", 1)[1].split()
        with open(f"{proc_root}/{pid}/statm", "rb") as fh:
            rss = int(fh.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return int(fields[11]) + int(fields[12]), rss


def processes(snapshot: dict, sample_s: float = 1.0, proc_root: str = "/proc", sleep=time.sleep) -> list[dict]:
    """Odoo process trees (a server and its workers) with CPU % over ``sample_s`` and resident memory."""
    procs = snapshot.get("processes", [])
    pids = {p["pid"] for p in procs}
    first = {p["pid"]: _ticks(p["pid"], proc_root) for p in procs}
    sleep(sample_s)
    hz = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    trees: dict[int, dict] = {}
    by_pid = {p["pid"]: p for p in procs}
    for p in procs:
        root = p["pid"]
        while by_pid.get(root, {}).get("ppid") in pids:
            root = by_pid[root]["ppid"]
        a, b = first.get(p["pid"]), _ticks(p["pid"], proc_root)
        head = by_pid[root]
        t = trees.setdefault(root, {"pid": root, "user": head.get("user"), "config": head.get("config"),
                                    "instance": head.get("instance"), "installation": head.get("installation"),
                                    "database": head.get("database"), "port": head.get("port"), "unit": head.get("unit"),
                                    "processes": 0, "cpu_percent": 0.0, "rss_bytes": 0, "argv": head.get("argv", [])})
        if b is None:
            continue
        t["processes"] += 1
        t["rss_bytes"] += b[1] * page
        if a is not None:
            t["cpu_percent"] += max(0, b[0] - a[0]) / hz / sample_s * 100
    for t in trees.values():
        t["cpu_percent"] = round(t["cpu_percent"], 1)
    return sorted(trees.values(), key=lambda t: -t["cpu_percent"])


# -- verdict --------------------------------------------------------------------------------

def _opt(instance: dict | None, key: str) -> str | None:
    v = ((instance or {}).get("options") or {}).get(key)
    return v if v not in (None, "", "False") else None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def verdict(pg: dict | None, procs: list[dict], snapshot: dict, pg_error: str | None = None) -> list[dict]:
    """Findings, worst first: {id, area (odoo|postgres|queries|config|visibility), level, title, detail}."""
    out = []

    def add(fid, area, level, title, detail):
        out.append({"id": fid, "area": area, "level": level, "title": title, "detail": detail})

    for t in procs:
        name = t["instance"] or t["config"] or f"pid {t['pid']}"
        if t["cpu_percent"] >= CPU_BUSY:
            add(f"cpu-{t['pid']}", "odoo", "warn", f"Odoo {name} is busy: {t['cpu_percent']}% CPU",
                f"{t['processes']} process(es); a slow page here is Python work (compute, rendering), not the database")
        inst = next((i for i in snapshot.get("instances", []) if i["path"] == t["config"]), None)
        soft = _int(_opt(inst, "limit_memory_soft"))
        if soft and t["processes"] and t["rss_bytes"] / t["processes"] > soft * 0.9:
            add(f"mem-{t['pid']}", "odoo", "warn", f"Odoo {name} is near limit_memory_soft",
                f"{t['rss_bytes'] // 2**20} MiB in {t['processes']} process(es); workers get recycled")
        argv = " ".join(t["argv"])
        if "--log-sql" in argv or "debug_sql" in argv or (_opt(inst, "log_level") or "").startswith("debug"):
            add(f"debuglog-{t['pid']}", "config", "info", f"Odoo {name} logs at debug level",
                "debug and SQL logging slow every request; turn it off when not measuring")
        if re.search(r"--dev[= ](\S*\b(all|qweb|xml|assets)\b)", argv):
            add(f"dev-{t['pid']}", "config", "info", f"Odoo {name} runs with --dev",
                "assets and views are re-read on every request; expect slower pages")
        workers = _int(_opt(inst, "workers")) or 0
        maxconn = _int(_opt(inst, "db_maxconn")) or 64
        if pg and pg.get("server", {}).get("max_connections") and workers:
            possible = maxconn * (workers + 2)
            if possible > pg["server"]["max_connections"]:
                add(f"maxconn-{t['pid']}", "config", "info", f"Odoo {name} may open more connections than PostgreSQL allows",
                    f"db_maxconn {maxconn} × ({workers} workers + 2) = {possible} > max_connections "
                    f"{pg['server']['max_connections']}")
    if pg_error:
        add("pg-error", "postgres", "warn", "PostgreSQL activity could not be read", pg_error)
    if pg:
        s = pg["server"]
        used, cap = s.get("connections") or 0, s.get("max_connections") or 0
        if cap and used >= cap * 0.8:
            add("connections", "postgres", "fail" if used >= cap * 0.95 else "warn",
                f"{used} of {cap} PostgreSQL connections in use", "new requests wait for a connection or fail")
        blocked = [x for x in pg["sessions"] if x.get("blocked_by")]
        if blocked:
            heads = sorted({b for x in blocked for b in x["blocked_by"]})
            add("blocked", "postgres", "warn", f"{len(blocked)} session(s) wait for a lock",
                f"blocked by pid {', '.join(map(str, heads))}")
        idle = [x for x in pg["sessions"] if x.get("state") == "idle in transaction" and (x.get("xact_s") or 0) >= IDLE_XACT_S]
        if idle:
            add("idle-xact", "postgres", "warn", f"{len(idle)} session(s) idle in a transaction",
                "they hold locks and block vacuum: pid " + ", ".join(str(x["pid"]) for x in idle))
        slow = [x for x in pg["sessions"] if x.get("state") == "active" and (x.get("query_s") or 0) >= LONG_QUERY_S]
        if slow:
            add("long-queries", "queries", "warn", f"{len(slow)} query(ies) running for {LONG_QUERY_S}s or more",
                "; ".join(f"pid {x['pid']} {x['query_s']}s" for x in slow[:5]))
        long_x = [x for x in pg["sessions"] if (x.get("xact_s") or 0) >= LONG_XACT_S and x.get("state") != "idle in transaction"]
        if long_x:
            add("long-xact", "postgres", "info", f"{len(long_x)} transaction(s) open for {LONG_XACT_S // 60} min or more",
                "; ".join(f"pid {x['pid']} {x['xact_s']}s" for x in long_x[:5]))
        if not s.get("monitor"):
            hidden = sum(1 for x in pg["sessions"] if x.get("hidden"))
            add("visibility", "visibility", "info", f"Other roles' queries are hidden ({hidden} session(s))",
                f"{s.get('role')} is not a member of pg_monitor; a one-time grant shows every session")
    order = {"fail": 0, "warn": 1, "info": 2}
    out.sort(key=lambda f: order[f["level"]])
    if not any(f["level"] in ("fail", "warn") for f in out):
        out.insert(0, {"id": "calm", "area": "summary", "level": "ok", "title": "No sign of a slowdown right now",
                       "detail": "Odoo processes are not busy, no query runs long, nothing waits for a lock"})
    return out


async def overview(root: str, database: str | None, snapshot: dict, sample_s: float = 1.0) -> dict:
    """Everything for the Performance view of one installation, in one call."""
    procs_task = asyncio.to_thread(processes, {**snapshot, "processes": [p for p in snapshot.get("processes", [])
                                                                          if p.get("installation") == root]}, sample_s)
    pg, pg_error = None, None
    if database:
        try:
            pg = await postgres(root, database)
        except ActivityError as exc:
            pg_error = str(exc)
    else:
        pg_error = "no database of this installation's role to connect through"
    procs = await procs_task
    return {"root": root, "database": database, "postgres": pg, "postgres_error": pg_error, "odoo": procs,
            "verdict": verdict(pg, procs, snapshot, pg_error), "at": time.time()}


def grant_script(role: str, revoke: bool = False) -> str:
    """Root script: GRANT (or REVOKE) pg_monitor to the installation's role on the local server."""
    if not isinstance(role, str) or not _ROLE.match(role):
        raise ActivityError(f"bad role name {role!r}")
    verb = f'REVOKE pg_monitor FROM "{role}"' if revoke else f'GRANT pg_monitor TO "{role}"'
    return ("#!/usr/bin/env bash\nset -euo pipefail\n"
            f"# {'Take back' if revoke else 'Let'} {role} {'from reading' if revoke else 'read'} every session's activity "
            "(pg_monitor: read-only statistics, not superuser)\n"
            f"cd /tmp && sudo -u postgres psql -X -v ON_ERROR_STOP=1 -c '{verb}'\n")
