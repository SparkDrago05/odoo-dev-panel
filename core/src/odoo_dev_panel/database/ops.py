"""Plan and run backup, restore, clone, drop and neutralize. Every action moves database and filestore together.

Database tools and file tools run as the run-as user through its agent (or locally when that is the dev user).
Neutralization runs locally over TCP: it needs the recipe from the dev user's home, not the agent's files.
A step removes only what the same run created. The only user data ever removed is a dropped database, and its
filestore is moved to a trash folder first.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import client, paths as app_paths
from ..discover.databases import dir_state
from ..doctor.repair import _writable_by
from ..provision.execute import ProvisionError, Report, _agent_session, _emit, run_local
from ..provision.plan import Step
from ..provision.preflight import FAIL, OK, WARN, Check
from . import commands as cmd, recipes
from .paths import backup_dir, default_dest, filestore_dir, name_error, trash_dir, tree_size

KINDS = ("backup", "restore", "clone", "drop", "neutralize")
CREATED, NO_FILESTORE = "ODP:created", "ODP:no-filestore"


class DbError(ProvisionError):
    pass


@dataclass
class DbPlan:
    kind: str
    root: str
    run_as: str
    source: str | None = None  # database to read (backup, clone) or act on (drop, neutralize)
    target: str | None = None  # database to create (restore, clone)
    backup: str | None = None  # backup folder: read by restore, made by backup
    filestore_src: str | None = None
    filestore_dst: str | None = None
    recipe: str | None = None
    recipe_sum: str | None = None
    confirmed: bool = False
    checks: list[Check] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    conn: cmd.Conn | None = field(default=None, repr=False)  # holds the password: never serialized
    recipe_sql: str | None = field(default=None, repr=False)
    pg_bin: str | None = None  # client tools of the server's major version, first on PATH

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("kind", "root", "run_as", "source", "target", "backup", "filestore_src",
                                           "filestore_dst", "recipe", "recipe_sum", "confirmed", "pg_bin")}
        return {**d, "connection": self.conn.public() if self.conn else None, "ok": self.ok,
                "checks": [vars(c) for c in self.checks], "steps": [vars(s) for s in self.steps]}


@dataclass
class DbContext:
    """Everything the plan needs from the outside, so tests need no PostgreSQL."""
    databases: list[dict]  # discovery rows of this installation
    conn: cmd.Conn | None
    bases: list[str | None]  # candidate filestore bases, first one is used for new databases
    processes: list[dict]
    agent_running: bool | None
    activity: Callable[[str], int | None] = lambda database: None  # sessions on a database
    recipes_dir: Path | None = None
    free_bytes: Callable[[str], int | None] = lambda path: None
    stamp: str = ""
    server_major: int | None = None
    pg_lib: str = "/usr/lib/postgresql"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _nearest(path: str) -> str:
    while path and not os.path.exists(path) and path != os.path.dirname(path):
        path = os.path.dirname(path)
    return path


def plan_db(kind: str, inst: dict, ctx: DbContext, source: str | None = None, target: str | None = None,
            backup: str | None = None, dest: str | None = None, recipe: str | None = None,
            confirm: str | None = None) -> DbPlan:
    """What the action will do and why it cannot run yet (checks with status fail). Changes nothing."""
    root, run_as = inst["root"], inst.get("owner") or ""
    stamp = ctx.stamp or _stamp()
    p = DbPlan(kind=kind, root=root, run_as=run_as, source=source, target=target, conn=ctx.conn)
    checks = p.checks

    def add(check_id: str, ok: bool, good: str, bad: str, level: str = FAIL) -> None:
        checks.append(Check(check_id, OK if ok else level, good if ok else bad))

    if kind not in KINDS:
        add("kind", False, "", f"unknown action {kind!r}")
        return p
    add("connection", ctx.conn is not None, f"role {ctx.conn.user if ctx.conn else ''}",
        "no config of this installation has a db_user")
    local = bool(run_as) and run_as == pwd.getpwuid(os.getuid()).pw_name
    if kind != "neutralize":
        add("run-as", bool(run_as), f"files and database tools run as {run_as}" + (" (this user)" if local else ""),
            f"{root} has no owner")
        if run_as and not local and ctx.agent_running is not None:
            add("agent", ctx.agent_running, f"agent of {run_as} is running", f"agent of {run_as} is not running: unlock it first")
    # Debian's pg_wrapper may pick a newer client than the server (pg_dump 18 writes SET transaction_timeout,
    # which a 16 server rejects): use the tools of the server's own major version when they are installed.
    if ctx.server_major:
        candidate = os.path.join(ctx.pg_lib, str(ctx.server_major), "bin")
        if os.path.isfile(os.path.join(candidate, "pg_dump")):
            p.pg_bin = candidate
            checks.append(Check("pg-tools", OK, f"PostgreSQL {ctx.server_major} client tools from {candidate}"))
        else:
            checks.append(Check("pg-tools", WARN, f"no {candidate}: the PostgreSQL tools on PATH are used; they must "
                                f"match the server ({ctx.server_major}). Run: sudo apt-get install postgresql-client-{ctx.server_major}"))
    names = {d["name"]: d for d in ctx.databases}
    src = names.get(source) if source else None

    if kind in ("backup", "clone", "drop", "neutralize"):
        add("source", src is not None, f"database {source} ({src['size'] if src else 0} bytes)",
            f"{source or '(none)'} is not a database of this installation's role")
    if target is not None or kind in ("restore", "clone"):
        err = name_error(target or "")
        add("target-name", err is None, f"new name {target}", f"new name {target!r}: {err}")
        add("target-free", target not in names, f"{target} does not exist yet",
            f"{target} exists already. Drop it first, or pick another name")
    # filestore
    base = next((b for b in ctx.bases if b), None)
    if src:
        found = [f["path"] for f in src.get("filestores", []) if f["exists"]]
        p.filestore_src = found[0] if found else None
        if src.get("filestore_exists") is None:
            checks.append(Check("filestore", WARN, "a folder above the filestore cannot be searched by this user: unknown"))
        elif not found:
            checks.append(Check("filestore", WARN, f"{source} has no filestore for {run_as or 'this user'}: only the database moves"))
    if kind in ("restore", "clone"):
        dst_base = os.path.dirname(p.filestore_src) if (kind == "clone" and p.filestore_src) else base
        if kind == "restore" or p.filestore_src:
            add("filestore-base", bool(dst_base), f"new filestore in {dst_base}", "no filestore location known (no data_dir, no home)")
            p.filestore_dst = filestore_dir(dst_base, target or "") if dst_base and not name_error(target or "") else None
            if p.filestore_dst:
                state = dir_state(p.filestore_dst)
                add("filestore-free", state is False, f"{p.filestore_dst} is free",
                    f"{p.filestore_dst} exists already" if state else f"cannot tell whether {p.filestore_dst} exists",
                    FAIL if state else WARN)
    # where files go
    if kind == "backup":
        dest = dest or default_dest(inst.get("home"))
        add("dest", bool(dest) and dest.startswith("/"), f"backup folder {dest}", "give an absolute --dest")
        if dest and dest.startswith("/"):
            p.backup = backup_dir(dest, source or "", stamp)
            anchor = _nearest(dest)
            add("dest-writable", bool(run_as) and _writable_by(anchor, run_as), f"{run_as} can write in {anchor}",
                f"{run_as} cannot write in {anchor}. Run: sudo chgrp {run_as} {anchor} && sudo chmod g+w {anchor}")
            need = (src["size"] if src else 0) + ((tree_size(p.filestore_src) or 0) if p.filestore_src else 0)
            free = ctx.free_bytes(anchor)
            if free is not None:
                add("space", free > need * 1.1, f"{free} bytes free, about {need} needed", f"{free} bytes free, about {need} needed", WARN)
    if kind == "restore":
        p.backup = backup
        add("backup", bool(backup) and backup.startswith("/"), f"backup {backup}", "give the absolute path of a backup folder")
    # processes and sessions
    mine = [pr for pr in ctx.processes if pr.get("installation") == root or (run_as and pr.get("user") == run_as)]
    if kind in ("drop", "neutralize", "restore"):
        add("not-running", not mine, "no Odoo process of this installation runs",
            "stop these Odoo processes first: pid " + ", ".join(str(pr["pid"]) for pr in mine))
    elif mine:
        checks.append(Check("running", WARN, "Odoo is running: the copy may catch it between two writes. pid "
                            + ", ".join(str(pr["pid"]) for pr in mine)))
    if source and src and kind in ("drop", "neutralize"):
        n = ctx.activity(source)
        if n:
            add("sessions", False, "", f"{n} session(s) are connected to {source}")
    # typed confirmation
    if kind in ("drop", "neutralize"):
        p.confirmed = confirm == source and bool(source)
        add("confirm", p.confirmed, f"typed {source}", f"type the database name ({source}) to confirm")
    # recipe
    if kind == "neutralize" or (kind == "clone" and recipe):
        p.recipe = recipe or recipes.DEFAULT
        try:
            sql = recipes.load(p.recipe, ctx.recipes_dir)
            p.recipe_sum = recipes.checksum(sql)
            p.recipe_sql = sql
            checks.append(Check("recipe", OK, f"recipe {p.recipe} ({p.recipe_sum})"))
        except (OSError, ValueError) as exc:
            add("recipe", False, "", f"recipe {p.recipe}: {exc}")
    p.steps = build_steps(p)
    return p


def build_steps(p: DbPlan) -> list[Step]:
    s = lambda sid, phase, title, commands: Step(sid, phase, "dev" if sid == "neutralize" else "agent", title, commands)  # noqa: E731
    src, tgt, fs_src, fs_dst = p.source, p.target, p.filestore_src, p.filestore_dst
    out: list[Step] = []
    if p.kind == "backup":
        out = [s("mkdir", 1, f"Make {p.backup}", [f"mkdir {p.backup}"]),
               s("dump", 2, f"Dump {src}", [f"pg_dump -Fc -f {p.backup}/dump.pgdump {src}"])]
        if fs_src:
            out.append(s("filestore", 2, f"Archive {fs_src}", [f"tar -C {fs_src} -cf {p.backup}/filestore.tar ."]))
        out += [s("manifest", 3, "Write manifest.json and SHA256SUMS", []),
                s("verify", 4, "Read the dump back (pg_restore --list)", [f"pg_restore --list {p.backup}/dump.pgdump"])]
    elif p.kind == "restore":
        out = [s("checksums", 1, f"Check SHA256SUMS in {p.backup}", ["sha256sum -c SHA256SUMS"]),
               s("createdb", 2, f"Create {tgt}", [f"createdb {tgt}"]),
               s("restore", 2, f"Restore the dump into {tgt}", [f"pg_restore -d {tgt} {p.backup}/dump.pgdump"]),
               s("filestore", 3, f"Extract the filestore to {fs_dst}", [f"tar -C {fs_dst} -xf {p.backup}/filestore.tar"]),
               s("verify", 4, f"Query {tgt}", [])]
    elif p.kind == "clone":
        out = [s("createdb", 1, f"Create {tgt}", [f"createdb {tgt}"]),
               s("copy-db", 2, f"Copy {src} into {tgt} (pg_dump | pg_restore)", [f"pg_dump -Fc {src} | pg_restore -d {tgt}"])]
        if fs_src:
            out.append(s("filestore", 2, f"Copy {fs_src} to {fs_dst}", [f"cp -a --reflink=auto {fs_src} {fs_dst}"]))
        if p.recipe:
            out.append(s("neutralize", 3, f"Run recipe {p.recipe} on {tgt} only", [f"psql -d {tgt} -f <recipe>"]))
        out.append(s("verify", 4, f"Query {tgt}", []))
    elif p.kind == "drop":
        if fs_src:
            out.append(s("filestore", 1, f"Move {fs_src} to a trash folder next to it (not deleted)", [f"mv {fs_src} <trash>"]))
        out.append(s("dropdb", 2, f"Drop {src}", [f"dropdb {src}"]))
    elif p.kind == "neutralize":
        out = [s("neutralize", 1, f"Run recipe {p.recipe} on {src}", [f"psql -d {src} -f <recipe>"])]
    return out


def write_receipt(p: DbPlan, status: str, phase: str, detail: dict, state_dir: Path | None = None) -> str:
    directory = (state_dir or app_paths.agent_state_dir()) / "db"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    name = p.target or p.source or "db"
    path = directory / f"{p.kind}-{name}-{stamp.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"tool": "odoo-dev-panel", "status": status, "last_phase": phase,
                                "updated_at": stamp.isoformat(timespec="seconds"), **detail, "plan": p.as_dict()},
                               indent=2, default=str) + "\n")
    return str(path)


class _Runner:
    """Run an argv as the run-as user (agent) or here, collecting the output lines of the step."""

    def __init__(self, conn, p: DbPlan, report: Report, local: bool) -> None:
        self.conn, self.p, self.report, self.local = conn, p, report, local

    async def run(self, step: str, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, list[str]]:
        lines: list[str] = []

        def tee(event: dict) -> None:
            if event["status"] == "output":
                lines.append(event["text"])
            self.report(event)

        path = _search(self.p)
        env = {**(self.p.conn.env() if self.p.conn else {}), "PATH": path, **(env or {})}
        argv = resolve(argv, path)
        if self.local:
            code = await _run_local_code(step, argv, tee, env)
        else:
            code, _ = await _agent_session(self.conn, step, argv, self.p.root, tee, env)
        return (code if code is not None else -1), lines

    async def ok(self, step: str, argv: list[str], env: dict[str, str] | None = None) -> list[str]:
        code, lines = await self.run(step, argv, env)
        if code != 0:
            raise DbError(f"{step}: {argv[0]} ended with exit code {code}")
        return lines


def resolve(argv: list[str], path: str | None = None) -> list[str]:
    """The agent runs only absolute paths: look tools up here (same machine, same PostgreSQL client)."""
    if os.path.isabs(argv[0]):
        return argv
    found = shutil.which(argv[0], path=path)
    if not found:
        raise DbError(f"{argv[0]} not found. Install the PostgreSQL client: sudo apt-get install postgresql-client")
    return [found, *argv[1:]]


async def _run_local_code(step: str, argv: list[str], report: Report, env: dict[str, str]) -> int:
    try:
        await run_local(step, argv, report, env)
        return 0
    except ProvisionError as exc:
        text = str(exc)
        return int(text.rsplit(" ", 1)[-1]) if text.rsplit(" ", 1)[-1].isdigit() else -1


def _bash(script: str, *args: str) -> list[str]:
    return ["/bin/bash", "-c", script, "odp", *args]


def _conn_args(c: cmd.Conn) -> list[str]:
    return [c.host or "", c.port, c.user]


async def run_db(p: DbPlan, report: Report, state_dir: Path | None = None) -> dict:
    """Run the plan. Raises DbError; the source database and filestore are untouched unless the action is drop."""
    if not p.ok:
        raise DbError("the plan has failed checks: " + "; ".join(c.detail for c in p.checks if c.status == FAIL))
    assert p.conn is not None
    me = pwd.getpwuid(os.getuid()).pw_name
    # Neutralize runs here over TCP, unless peer authentication lets only the run-as user in.
    local = p.run_as == me or (p.kind == "neutralize" and not p.conn.needs_os_user(me))
    conn = None
    if not local:
        status = await client.agent_status(p.run_as)
        if status["state"] != "running":
            raise DbError(f"agent of {p.run_as} is not running: unlock it first")
        conn = await client.connect(p.run_as)
    runner = _Runner(conn, p, report, local)
    state = {"phase": "start", "db": False, "fs": False, "dir": False}
    try:
        result = await _dispatch(p, runner, report, state)
        receipt = write_receipt(p, "complete", state["phase"], result, state_dir)
        return {**result, "receipt": receipt}
    except Exception as exc:
        await _rollback(p, runner, report, state)
        try:
            write_receipt(p, "failed", state["phase"], {"error": str(exc)}, state_dir)
        except OSError:
            pass
        _emit(report, state["phase"], "fail", str(exc))
        raise exc if isinstance(exc, DbError) else DbError(str(exc)) from exc
    finally:
        if conn:
            await conn.close()


def _begin(report: Report, state: dict, phase: str, text: str) -> None:
    state["phase"] = phase
    _emit(report, phase, "start", text)


async def _dispatch(p: DbPlan, r: _Runner, report: Report, state: dict) -> dict:
    c = p.conn
    assert c is not None
    if p.kind == "backup":
        _begin(report, state, "mkdir", f"Make {p.backup}")
        parent = os.path.dirname(p.backup)
        # 0750: a backup holds client data; other users of the machine must not be able to read it
        await r.ok("mkdir", ["/bin/mkdir", "-p", "-m", "0750", "--", parent])
        await r.ok("mkdir", ["/bin/mkdir", "-m", "0750", "--", p.backup])
        state["dir"] = True
        _emit(report, "mkdir", "ok")
        _begin(report, state, "dump", f"Dump {p.source}")
        await r.ok("dump", cmd.dump_argv(c, p.source, f"{p.backup}/dump.pgdump"))
        _emit(report, "dump", "ok")
        if p.filestore_src:
            _begin(report, state, "filestore", f"Archive {p.filestore_src}")
            await r.ok("filestore", _bash(cmd.TAR_SCRIPT, p.filestore_src, f"{p.backup}/filestore.tar"))
            _emit(report, "filestore", "ok")
        _begin(report, state, "manifest", "Write manifest.json and SHA256SUMS")
        manifest = json.dumps({"tool": "odoo-dev-panel", "database": p.source, "installation": p.root,
                               "filestore": bool(p.filestore_src), "filestore_path": p.filestore_src,
                               "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2)
        await r.ok("manifest", ["/bin/sh", "-c", 'printf "%s\\n" "$1" > "$2"', "odp", manifest, f"{p.backup}/manifest.json"])
        await r.ok("manifest", _bash(cmd.SUMS_SCRIPT, p.backup))
        _emit(report, "manifest", "ok")
        _begin(report, state, "verify", "Read the dump back")
        await r.ok("verify", _bash(cmd.LIST_DUMP_SCRIPT, f"{p.backup}/dump.pgdump"))
        _emit(report, "verify", "ok")
        return {"backup": p.backup, "database": p.source, "filestore": bool(p.filestore_src)}

    if p.kind == "restore":
        _begin(report, state, "checksums", f"Check {p.backup}")
        await r.ok("checksums", _bash(cmd.CHECK_SUMS_SCRIPT, p.backup))
        _emit(report, "checksums", "ok")
        await _create_and_fill(p, r, report, state, lambda: r.ok("restore", cmd.restore_argv(c, p.target, f"{p.backup}/dump.pgdump")),
                               f"Restore the dump into {p.target}")
        _begin(report, state, "filestore", f"Extract the filestore to {p.filestore_dst}")
        code, lines = await r.run("filestore", _bash(cmd.UNTAR_SCRIPT, p.backup, p.filestore_dst))
        state["fs"] = CREATED in lines
        if code != 0:
            raise DbError("the filestore folder exists already" if code == 3 else f"filestore ended with exit code {code}")
        _emit(report, "filestore", "ok", "no filestore in the backup" if NO_FILESTORE in lines else "")
        await _verify(p, r, report, state)
        return {"database": p.target, "filestore": p.filestore_dst if state["fs"] else None}

    if p.kind == "clone":
        await _create_and_fill(p, r, report, state, lambda: r.ok("copy-db", _bash(cmd.CLONE_SCRIPT, *_conn_args(c), p.source, p.target)),
                               f"Copy {p.source} into {p.target}")
        if p.filestore_src:
            _begin(report, state, "filestore", f"Copy {p.filestore_src} to {p.filestore_dst}")
            code, lines = await r.run("filestore", _bash(cmd.COPY_TREE_SCRIPT, p.filestore_src, p.filestore_dst))
            state["fs"] = CREATED in lines
            if code != 0:
                raise DbError("the filestore folder exists already" if code == 3 else f"filestore ended with exit code {code}")
            _emit(report, "filestore", "ok")
        if p.recipe:
            await _neutralize(p, r, p.target, report, state)
        await _verify(p, r, report, state)
        return {"database": p.target, "filestore": p.filestore_dst if state["fs"] else None, "recipe": p.recipe}

    if p.kind == "neutralize":
        await _neutralize(p, r, p.source, report, state)
        return {"database": p.source, "recipe": p.recipe}

    if p.kind == "drop":
        trash = None
        if p.filestore_src:
            _begin(report, state, "filestore", f"Move {p.filestore_src} to a trash folder")
            trash = trash_dir(os.path.dirname(p.filestore_src), p.source, _stamp())
            await r.ok("filestore", _bash(cmd.MOVE_SCRIPT, p.filestore_src, trash))
            state["trash"] = (p.filestore_src, trash)
            _emit(report, "filestore", "ok", trash)
        _begin(report, state, "dropdb", f"Drop {p.source}")
        await r.ok("dropdb", cmd.dropdb_argv(p.conn, p.source))
        state.pop("trash", None)
        _emit(report, "dropdb", "ok")
        return {"database": p.source, "trash": trash}
    raise DbError(f"unknown action {p.kind}")


async def _create_and_fill(p: DbPlan, r: _Runner, report: Report, state: dict, fill, text: str) -> None:
    _begin(report, state, "createdb", f"Create {p.target}")
    await r.ok("createdb", cmd.createdb_argv(p.conn, p.target))
    state["db"] = True
    _emit(report, "createdb", "ok")
    _begin(report, state, "copy-db" if p.kind == "clone" else "restore", text)
    await fill()
    _emit(report, state["phase"], "ok")


async def _verify(p: DbPlan, r: _Runner, report: Report, state: dict) -> None:
    _begin(report, state, "verify", f"Query {p.target}")
    await r.ok("verify", cmd.verify_argv(p.conn, p.target))
    _emit(report, "verify", "ok")


async def _neutralize(p: DbPlan, r: _Runner, database: str, report: Report, state: dict) -> None:
    _begin(report, state, "neutralize", f"Run recipe {p.recipe} on {database} only")
    sql = recipes.render(p.recipe_sql or "", database)
    try:
        if r.local:
            with tempfile.TemporaryDirectory(prefix="odp-recipe-") as tmp:
                path = os.path.join(tmp, "recipe.sql")
                Path(path).write_text(sql)
                search = _search(p)
                await run_local("neutralize", resolve(cmd.psql_argv(p.conn, database, "-f", path), search), report,
                                {**p.conn.env(), "PATH": search})
        else:
            # The agent cannot read the dev user's temp files: the recipe goes in through stdin.
            await r.ok("neutralize", _bash(cmd.PSQL_STDIN_SCRIPT, *resolve(cmd.psql_argv(p.conn, database, "-f", "-"), _search(p))),
                       {"ODP_SQL": sql})
    except ProvisionError as exc:  # DbError included
        state["neutralize_failed"] = True
        raise DbError(f"recipe {p.recipe} failed on {database}: {exc}. {database} is left as it is, NOT neutralized"
                      if p.kind == "clone" else f"recipe {p.recipe} failed on {database}: {exc}") from exc
    _emit(report, "neutralize", "ok")


def _search(p: DbPlan) -> str:
    return os.pathsep.join(x for x in (p.pg_bin, os.environ.get("PATH", "/usr/bin:/bin")) if x)


async def _rollback(p: DbPlan, r: _Runner, report: Report, state: dict) -> None:
    """Remove only what this run made. A failed neutralization keeps the clone, marked as such in the receipt."""
    async def attempt(step: str, argv: list[str]) -> None:
        try:
            await r.ok("rollback", argv)
            _emit(report, "rollback", "output", step)
        except Exception as exc:  # noqa: BLE001
            _emit(report, "rollback", "output", f"could not {step}: {exc}")

    if state.get("trash"):
        src, trash = state["trash"]
        await attempt(f"move {trash} back to {src}", _bash(cmd.MOVE_SCRIPT, trash, src))
    if p.kind in ("clone", "restore") and state.get("neutralize_failed"):
        _emit(report, "rollback", "output", f"{p.target} and its filestore are kept: they are NOT neutralized. Do not use them")
        return
    if state.get("fs") and p.filestore_dst:
        await attempt(f"remove {p.filestore_dst}", ["/bin/rm", "-rf", "--", p.filestore_dst])
    if state.get("db") and p.target:
        await attempt(f"drop {p.target}", cmd.dropdb_argv(p.conn, p.target, if_exists=True))
    if state.get("dir") and p.backup:
        await attempt(f"remove {p.backup}", ["/bin/rm", "-rf", "--", p.backup])
