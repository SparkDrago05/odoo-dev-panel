"""D5/D6: databases owned by an installation's PostgreSQL role, with filestore paths."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

from .filestore import filestore_base, filestore_path
from .model import Installation, Instance

QUERY = (
    "SELECT d.datname, pg_database_size(d.datname) FROM pg_database d "
    "JOIN pg_roles r ON r.oid = d.datdba WHERE r.rolname = current_user AND NOT d.datistemplate "
    "ORDER BY d.datname"
)


def list_databases(host: str | None, port: str, user: str, password: str | None, psql: str | None = None) -> list[tuple[str, int]]:
    """Query as the role itself, with the password passed through the environment only."""
    psql = psql or shutil.which("psql")
    if not psql:
        raise RuntimeError("psql not found")
    env = dict(os.environ)
    env.pop("PGPASSWORD", None)
    if password:
        env["PGPASSWORD"] = password
    proc = subprocess.run(
        [psql, *(["-h", host] if host else []), "-p", port, "-U", user, "-d", "postgres", "-X", "-A", "-t", "-F", "\t", "-c", QUERY],
        capture_output=True, text=True, timeout=15, env=env, stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip().splitlines() or ["psql failed"])[-1])
    rows = []
    for line in proc.stdout.splitlines():
        name, _, size = line.partition("\t")
        if name:
            rows.append((name, int(size or 0)))
    return rows


def dir_state(path: str) -> bool | None:
    """True: a directory. False: missing. None: a parent cannot be searched (another user's private home)."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:  # PermissionError and the like
        return None


def _connection(raw_options: dict[str, str], owner: str | None) -> tuple[str | None, str, str | None, str | None]:
    """Same rules as Odoo (and ``database.commands.conn_from_options``): no db_user means the run-as OS user,
    no db_host means the Unix socket, except that TCP localhost is used when a password is set."""
    from ..database.commands import conn_from_options

    conn = conn_from_options(raw_options, owner)
    if conn is None:
        return None, "5432", None, None
    return conn.host, conn.port, conn.user, conn.password


def discover_databases(installs: list[Installation], instances: list[Instance], reader=list_databases, parse=None) -> list[dict]:
    """One entry per (installation, database). ``parse`` reads a config including secrets; they never leave this function."""
    from .configs import parse_config

    parse = parse or (lambda p: parse_config(__import__("pathlib").Path(p)))
    result: list[dict] = []
    for inst in installs:
        mine = [i for i in instances if i.installation == inst.root]
        tried: set[str] = set()
        rows, error, via = None, None, None
        for instance in mine:  # first config whose role can log in wins
            raw = parse(instance.path) or {}
            host, port, user, password = _connection(raw, inst.owner)
            if not user or (host, port, user) in tried:
                continue
            tried.add((host, port, user))
            try:
                rows = reader(host, port, user, password)
                via = instance.path
                break
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                error = str(exc)
                if "Peer authentication failed" in error:
                    error = f"{user} uses peer authentication; the Databases panel lists it through the agent of {inst.owner}"
        if rows is None:
            result.append({"installation": inst.root, "databases": [], "error": error or "no config with a db_user", "via": None})
            continue
        # Configs of one installation may set different data_dir values: report every candidate path.
        bases = list(dict.fromkeys(filestore_base(i.options, inst.home) for i in mine) or [filestore_base({}, inst.home)])
        dbs = []
        for name, size in rows:
            candidates = [p for p in (filestore_path(b, name) for b in bases) if p]
            states = {p: dir_state(p) for p in candidates}
            found = [p for p in candidates if states[p]]
            unknown = any(v is None for v in states.values())
            dbs.append({
                "name": name, "size": size,
                "filestore": (found or candidates or [None])[0],
                # None: a parent folder cannot be searched by this user, so nothing is known
                "filestore_exists": True if found else (None if unknown or not candidates else False),
                "filestores": [{"path": p, "exists": states[p]} for p in candidates],
            })
        result.append({"installation": inst.root, "databases": dbs, "error": None, "via": via})
    return result
