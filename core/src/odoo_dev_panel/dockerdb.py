"""Database actions for Odoo containers whose PostgreSQL runs in a container of the same compose project.

Same actions, same plans and the same backup format as ``database.ops`` (a backup made here restores natively and
the other way round), but the work goes through the ``docker`` CLI as the developer:

- database tools run with ``docker exec`` in the database container (its local socket needs no password);
- dumps and archives stream to and from files on the host, which belong to the developer (mode 0750), so no
  agent is involved;
- the filestore is read and written by a short-lived helper container of the same image with
  ``--volumes-from`` the Odoo container, so it works whether the Odoo container runs or not, with the same
  user and the same mounts as Odoo.

A step removes only what the same run created, as in ``database.ops``. The Odoo container must not hold sessions on
a database that is dropped, reverted or neutralized: stop it first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .database import commands as cmd, ops, recipes
from .database.paths import (SNAPSHOT_NAME, aside_name, backup_dir, filestore_dir, is_snapshot_of, name_error,
                             trash_dir)
from .discover import docker as discovery
from .dockerops import DockerError, find
from .provision.execute import Report, _emit
from .provision.plan import Step
from .provision.preflight import FAIL, OK, WARN, Check

KINDS = ops.KINDS
FS_BASE = "/var/lib/odoo/filestore"
DATA_DIR = "/var/lib/odoo"
CREATED, NO_FILESTORE = ops.CREATED, ops.NO_FILESTORE
DEFAULT_KEEP = ops.DEFAULT_KEEP
DbError = DockerError


def host_root(name: str, home: str | None = None) -> str:
    return os.path.join(home or os.path.expanduser("~"), "odp-backups", "docker", name)


def snapshot_root(name: str, home: str | None = None) -> str:
    return os.path.join(host_root(name, home), "snapshots")


@dataclass
class DContext:
    """Everything the plan needs from the outside, so tests need no Docker."""
    container: dict
    db_container: str | None
    db_running: bool
    user: str
    databases: list[dict] = field(default_factory=list)  # name, size, filestore, filestore_exists
    list_error: str | None = None
    filestores: set[str] | None = None  # database folders found under FS_BASE; None: cannot tell
    activity: dict[str, int] = field(default_factory=dict)
    home: str | None = None
    recipes_dir: Path | None = None
    free_bytes: Callable[[str], int | None] = lambda path: None
    stamp: str = ""


@dataclass
class DPlan:
    kind: str
    root: str  # "docker:<container>"
    run_as: str = "docker"
    container: str = ""
    source: str | None = None
    target: str | None = None
    backup: str | None = None
    filestore_src: str | None = None
    filestore_dst: str | None = None
    recipe: str | None = None
    recipe_sum: str | None = None
    confirmed: bool = False
    keep: int | None = None
    aside: str | None = None
    aside_fs: str | None = None
    checks: list[Check] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    recipe_sql: str | None = field(default=None, repr=False)
    ctx: DContext | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("kind", "root", "run_as", "container", "source", "target", "backup",
                                           "filestore_src", "filestore_dst", "recipe", "recipe_sum", "confirmed",
                                           "keep", "aside", "aside_fs")}
        return {**d, "connection": None, "ok": self.ok, "checks": [vars(c) for c in self.checks],
                "steps": [vars(s) for s in self.steps]}


# -- docker commands ---------------------------------------------------------

def dbx(ctx: DContext, *argv: str, interactive: bool = False) -> list[str]:
    return ["docker", "exec", *(["-i"] if interactive else []), ctx.db_container or "", *argv]


def psql(ctx: DContext, database: str, *extra: str, interactive: bool = False) -> list[str]:
    return dbx(ctx, "psql", "-U", ctx.user, "-d", database, "-X", "-v", "ON_ERROR_STOP=1", *extra, interactive=interactive)


def helper(ctx: DContext, script: str, *args: str, interactive: bool = False) -> list[str]:
    """A throwaway container of the Odoo image, with the Odoo container's volumes and no network."""
    c = ctx.container
    return ["docker", "run", "--rm", "--network", "none", *(["-i"] if interactive else []),
            "--volumes-from", c["name"], *(["--user", c["user"]] if c["user"] else []),
            "--entrypoint", "/bin/sh", c["image_id"], "-c", script, "odp", *args]


async def _sh(argv: list[str], stdin=None, stdout=None, timeout: float | None = None) -> tuple[int, list[str], list[str]]:
    """Run argv; returns (exit code, stdout lines, stderr lines). ``stdout`` may be an open file."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    return (proc.returncode if proc.returncode is not None else -1,
            out.decode(errors="replace").splitlines() if out else [], err.decode(errors="replace").splitlines())


# -- context and listing -----------------------------------------------------

def _db_user(c: dict) -> str:
    return c["db"]["user"] or "odoo"


async def prepare(name: str, home: str | None = None, recipes_dir: Path | None = None) -> tuple[dict, DContext]:
    found = await asyncio.to_thread(discovery.discover_docker)
    if found["error"]:
        raise DockerError(found["error"])
    c = find(found["containers"], name)
    dbc = c["db"]["container"]
    ctx = DContext(container=c, db_container=dbc, db_running=False, user=_db_user(c), home=home,
                   recipes_dir=recipes_dir, free_bytes=_free, stamp="")
    if not dbc:
        ctx.list_error = "the database of this container is not a container of its compose project"
        return c, ctx
    code, out, err = await _sh(["docker", "inspect", "-f", "{{.State.Running}}", dbc], timeout=15)
    ctx.db_running = code == 0 and out[:1] == ["true"]
    if not ctx.db_running:
        ctx.list_error = f"the database container {dbc} is not running"
        return c, ctx
    query = ("SELECT d.datname, pg_database_size(d.datname) FROM pg_database d WHERE NOT d.datistemplate "
             "AND d.datname <> 'postgres' ORDER BY d.datname")
    code, out, err = await _sh(psql(ctx, "postgres", "-A", "-t", "-F", "\t", "-c", query), timeout=30)
    if code != 0:
        ctx.list_error = (err or ["psql failed"])[-1]
        return c, ctx
    code, counts, _ = await _sh(psql(ctx, "postgres", "-A", "-t", "-F", "\t", "-c", cmd.ACTIVITY_ALL_SQL), timeout=30)
    if code == 0:
        for line in counts:
            n, _, k = line.partition("\t")
            if n and k.isdigit():
                ctx.activity[n] = int(k)
    if c["data"] and not c["data"]["in_image"]:
        code, listing, _ = await _sh(helper(ctx, 'ls -1 -- "$1" 2>/dev/null; exit 0', FS_BASE), timeout=120)
        ctx.filestores = set(listing) if code == 0 else None
    for line in out:
        n, _, size = line.partition("\t")
        if n:
            has = None if ctx.filestores is None else n in ctx.filestores
            ctx.databases.append({"name": n, "size": int(size or 0), "filestore": os.path.join(FS_BASE, n),
                                  "filestore_exists": has,
                                  "filestores": [{"path": os.path.join(FS_BASE, n), "exists": has}]})
    return c, ctx


def _free(path: str) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _nearest(path: str) -> str:
    while path and not os.path.exists(path) and path != os.path.dirname(path):
        path = os.path.dirname(path)
    return path


# -- plan --------------------------------------------------------------------

def plan_db(kind: str, container: dict, ctx: DContext, source: str | None = None, target: str | None = None,
            backup: str | None = None, dest: str | None = None, recipe: str | None = None,
            confirm: str | None = None, keep: int | None = None) -> DPlan:
    stamp = ctx.stamp or ops._stamp()
    p = DPlan(kind=kind, root=f"docker:{container['name']}", container=container["name"], source=source,
              target=target, ctx=ctx)
    checks = p.checks

    def add(check_id: str, ok: bool, good: str, bad: str, level: str = FAIL) -> None:
        checks.append(Check(check_id, OK if ok else level, good if ok else bad))

    if kind not in KINDS:
        add("kind", False, "", f"unknown action {kind!r}")
        return p
    root = snapshot_root(container["name"], ctx.home)
    if kind == "forget":
        norm = os.path.normpath(backup) if backup else ""
        ok = bool(backup) and backup.startswith("/") and os.path.dirname(norm) == root and bool(SNAPSHOT_NAME.fullmatch(os.path.basename(norm)))
        p.backup = backup
        add("snapshot", ok, f"snapshot {norm}", f"give the absolute path of a snapshot folder in {root}/")
        p.steps = _steps(p)
        return p
    add("database-container", bool(ctx.db_container) and ctx.db_running,
        f"{ctx.db_container} (role {ctx.user})", ctx.list_error or "no database container")
    data = container["data"]
    names = {d["name"]: d for d in ctx.databases}
    src = names.get(source) if source else None
    if kind in ("backup", "clone", "drop", "neutralize", "snapshot", "revert"):
        add("source", src is not None, f"database {source} ({src['size'] if src else 0} bytes)",
            f"{source or '(none)'} is not a database in {ctx.db_container}")
    if kind in ("snapshot", "revert") and src:
        err = name_error(source or "")
        add("source-name", err is None, f"{source} is a plain name", f"snapshots need a plain database name: {err}")
    if target is not None or kind in ("restore", "clone"):
        err = name_error(target or "")
        add("target-name", err is None, f"new name {target}", f"new name {target!r}: {err}")
        add("target-free", target not in names, f"{target} does not exist yet",
            f"{target} exists already. Drop it first, or pick another name")
    # filestore: lives under the data mount, handled by a helper container
    has_fs_mount = bool(data) and not data["in_image"]
    if kind in ("backup", "clone", "drop", "snapshot", "revert") or kind == "restore":
        if not has_fs_mount:
            checks.append(Check("filestore", WARN, f"{container['name']} has no volume or bind mount for {DATA_DIR}: its "
                                "filestore lives inside the container and is not copied, only the database"))
        else:
            checks.append(Check("helper", OK, f"filestore handled by a helper container of {container['image']}"))
    if src and has_fs_mount:
        if src["filestore_exists"] is None:
            checks.append(Check("filestore", WARN, "the filestore folder could not be listed: unknown"))
        elif src["filestore_exists"]:
            p.filestore_src = src["filestore"]
        else:
            checks.append(Check("filestore", WARN, f"{source} has no filestore: only the database moves"))
    if kind in ("restore", "clone") and has_fs_mount and (kind == "restore" or p.filestore_src):
        if not name_error(target or ""):
            p.filestore_dst = filestore_dir(FS_BASE, target or "")
            taken = ctx.filestores is not None and target in ctx.filestores
            add("filestore-free", not taken, f"{p.filestore_dst} is free", f"{p.filestore_dst} exists already")
    if kind in ("backup", "snapshot"):
        dest = root if kind == "snapshot" else dest or host_root(container["name"], ctx.home)
        add("dest", bool(dest) and dest.startswith("/"), f"backup folder {dest}", "give an absolute --dest")
        if dest and dest.startswith("/"):
            p.backup = backup_dir(dest, source or "", stamp)
            anchor = _nearest(dest)
            add("dest-writable", os.access(anchor, os.W_OK), f"you can write in {anchor}", f"you cannot write in {anchor}")
            free = ctx.free_bytes(anchor)
            need = src["size"] if src else 0
            if free is not None:
                add("space", free > need * 1.1, f"{free} bytes free, about {need} needed (and the filestore)",
                    f"{free} bytes free, about {need} needed", WARN)
    if kind == "snapshot":
        p.keep = keep if keep is not None else DEFAULT_KEEP
        add("keep", p.keep >= 1, f"keeps the newest {p.keep} snapshot(s) of {source}; older snapshots of it are removed",
            "keep at least 1 snapshot")
    if kind == "restore":
        p.backup = backup
        add("backup", bool(backup) and backup.startswith("/"), f"backup {backup}", "give the absolute path of a backup folder")
    if kind == "revert":
        p.backup = backup
        add("snapshot", bool(backup) and backup.startswith("/") and is_snapshot_of(backup, source or ""),
            f"snapshot {backup}", f"give the absolute path of a snapshot of {source} (<database>-YYYYMMDD-HHMMSS)")
        if src and not name_error(source or ""):
            p.aside, p.target = aside_name(source, stamp), source
            add("aside-free", p.aside not in names, f"the current {source} is kept as {p.aside}",
                f"{p.aside} exists already: wait a second and plan again")
            p.filestore_dst = filestore_dir(FS_BASE, source)
            if p.filestore_src:
                p.aside_fs = filestore_dir(FS_BASE, p.aside)
                add("aside-fs-free", ctx.filestores is not None and p.aside not in ctx.filestores,
                    f"its filestore is kept as {p.aside_fs}", f"{p.aside_fs} exists already or cannot be checked")
    if kind in ("drop", "neutralize", "revert") and src:
        n = ctx.activity.get(source or "", 0)
        add("sessions", n == 0, f"no session on {source}",
            f"{n} session(s) are connected to {source}: stop the container first ({container['name']})")
    if kind in ("drop", "neutralize", "revert"):
        p.confirmed = confirm == source and bool(source)
        add("confirm", p.confirmed, f"typed {source}", f"type the database name ({source}) to confirm")
    if kind == "neutralize" or (kind == "clone" and recipe):
        p.recipe = recipe or recipes.DEFAULT
        try:
            sql = recipes.load(p.recipe, ctx.recipes_dir)
            p.recipe_sum, p.recipe_sql = recipes.checksum(sql), sql
            checks.append(Check("recipe", OK, f"recipe {p.recipe} ({p.recipe_sum})"))
        except (OSError, ValueError) as exc:
            add("recipe", False, "", f"recipe {p.recipe}: {exc}")
    p.steps = _steps(p)
    return p


def _steps(p: DPlan) -> list[Step]:
    s = lambda sid, phase, title, commands: Step(sid, phase, "docker", title, commands)  # noqa: E731
    db, tgt, fs_src, fs_dst, dbc = p.source, p.target, p.filestore_src, p.filestore_dst, "<db container>"
    out: list[Step] = []
    if p.kind in ("backup", "snapshot"):
        out = [s("mkdir", 1, f"Make {p.backup} on this machine", [f"mkdir {p.backup}"]),
               s("dump", 2, f"Dump {db} to a file here", [f"docker exec {dbc} pg_dump -Fc -d {db} > dump.pgdump"])]
        if fs_src:
            out.append(s("filestore", 2, f"Archive {fs_src} to a file here", [f"docker run --rm --volumes-from {p.container} ... tar -cf - {fs_src} > filestore.tar"]))
        out += [s("manifest", 3, "Write manifest.json and SHA256SUMS", []),
                s("verify", 4, "Read the dump back (pg_restore --list)", [])]
        if p.kind == "snapshot":
            out.append(s("prune", 5, f"Remove snapshots of {db} beyond the newest {p.keep}", []))
    elif p.kind == "restore":
        out = [s("checksums", 1, f"Check SHA256SUMS in {p.backup}", []),
               s("createdb", 2, f"Create {tgt}", [f"docker exec {dbc} createdb {tgt}"]),
               s("restore", 2, f"Restore the dump into {tgt}", [f"docker exec -i {dbc} pg_restore -d {tgt} < dump.pgdump"]),
               s("filestore", 3, f"Extract the filestore to {fs_dst}", ["helper container: tar -xf - < filestore.tar"]),
               s("verify", 4, f"Query {tgt}", [])]
    elif p.kind == "clone":
        out = [s("createdb", 1, f"Create {tgt}", [f"docker exec {dbc} createdb {tgt}"]),
               s("copy-db", 2, f"Copy {db} into {tgt} (pg_dump | pg_restore, inside the database container)", [])]
        if fs_src:
            out.append(s("filestore", 2, f"Copy {fs_src} to {fs_dst}", ["helper container: cp -a"]))
        if p.recipe:
            out.append(s("neutralize", 3, f"Run recipe {p.recipe} on {tgt} only", [f"docker exec -i {dbc} psql -d {tgt} -f -"]))
        out.append(s("verify", 4, f"Query {tgt}", []))
    elif p.kind == "drop":
        if fs_src:
            out.append(s("filestore", 1, f"Move {fs_src} to a trash folder next to it (not deleted)", ["helper container: mv"]))
        out.append(s("dropdb", 2, f"Drop {db}", [f"docker exec {dbc} dropdb {db}"]))
    elif p.kind == "neutralize":
        out = [s("neutralize", 1, f"Run recipe {p.recipe} on {db}", [f"docker exec -i {dbc} psql -d {db} -f -"])]
    elif p.kind == "revert":
        out = [s("checksums", 1, f"Check SHA256SUMS in {p.backup}", []),
               s("rename", 2, f"Keep the current {db} as {p.aside}", [cmd.rename_sql(db or "", p.aside or "")])]
        if fs_src:
            out.append(s("aside-fs", 2, f"Move its filestore to {p.aside_fs}", ["helper container: mv"]))
        out += [s("createdb", 3, f"Create {tgt}", [f"docker exec {dbc} createdb {tgt}"]),
                s("restore", 3, f"Restore the snapshot into {tgt}", [f"docker exec -i {dbc} pg_restore -d {tgt} < dump.pgdump"]),
                s("filestore", 3, f"Extract the filestore to {fs_dst}", ["helper container: tar -xf - < filestore.tar"]),
                s("verify", 4, f"Query {tgt}", [])]
    elif p.kind == "forget":
        out = [s("forget", 1, f"Remove snapshot {p.backup}", [f"rm -rf {p.backup}"])]
    return out


# -- run ---------------------------------------------------------------------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_sums(folder: str) -> None:
    names = sorted(n for n in os.listdir(folder) if n != "SHA256SUMS" and os.path.isfile(os.path.join(folder, n)))
    Path(folder, "SHA256SUMS").write_text("".join(f"{_sha256(os.path.join(folder, n))}  {n}\n" for n in names))


def check_sums(folder: str) -> None:
    try:
        lines = Path(folder, "SHA256SUMS").read_text().splitlines()
    except OSError as exc:
        raise DockerError(f"no SHA256SUMS in {folder}: {exc.strerror or exc}") from exc
    if not lines:
        raise DockerError(f"SHA256SUMS in {folder} is empty")
    for line in lines:
        digest, _, name = line.partition("  ")
        if not name or "/" in name or _sha256(os.path.join(folder, name)) != digest:
            raise DockerError(f"checksum of {name or line!r} does not match: the backup was changed or is damaged")


class _Run:
    def __init__(self, p: DPlan, report: Report) -> None:
        self.p, self.ctx, self.report = p, p.ctx, report
        assert self.ctx is not None

    async def ok(self, step: str, argv: list[str], stdin=None, stdout=None) -> list[str]:
        code, out, err = await _sh(argv, stdin=stdin, stdout=stdout)
        for line in err:
            _emit(self.report, step, "output", line)
        if code != 0:
            raise DockerError(f"{step}: {argv[1] if argv[0] == 'docker' else argv[0]} ended with exit code {code}"
                              + (f": {err[-1]}" if err else ""))
        return out

    async def run(self, step: str, argv: list[str], stdin=None) -> tuple[int, list[str]]:
        code, out, err = await _sh(argv, stdin=stdin)
        for line in err:
            _emit(self.report, step, "output", line)
        return code, out


async def run_db(p: DPlan, report: Report, state_dir: Path | None = None) -> dict:
    if not p.ok:
        raise DockerError("the plan has failed checks: " + "; ".join(c.detail for c in p.checks if c.status == FAIL))
    r = _Run(p, report)
    state = {"phase": "start", "db": False, "fs": False, "dir": False}
    try:
        result = await _dispatch(p, r, report, state)
        return {**result, "receipt": ops.write_receipt(p, "complete", state["phase"], result, state_dir)}  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        await _rollback(p, r, report, state)
        try:
            ops.write_receipt(p, "failed", state["phase"], {"error": str(exc)}, state_dir)  # type: ignore[arg-type]
        except OSError:
            pass
        _emit(report, state["phase"], "fail", str(exc))
        raise exc if isinstance(exc, DockerError) else DockerError(str(exc)) from exc


def _begin(report: Report, state: dict, phase: str, text: str) -> None:
    state["phase"] = phase
    _emit(report, phase, "start", text)


async def _create_and_fill(p: DPlan, r: _Run, report: Report, state: dict, fill, text: str) -> None:
    ctx = r.ctx
    _begin(report, state, "createdb", f"Create {p.target}")
    await r.ok("createdb", dbx(ctx, "createdb", "-U", ctx.user, "-T", "template0", "-E", "UTF8", "--", p.target or ""))
    state["db"] = True
    _emit(report, "createdb", "ok")
    _begin(report, state, "copy-db" if p.kind == "clone" else "restore", text)
    await fill()
    _emit(report, state["phase"], "ok")


async def _verify(p: DPlan, r: _Run, report: Report, state: dict) -> None:
    _begin(report, state, "verify", f"Query {p.target}")
    await r.ok("verify", psql(r.ctx, p.target or "", "-A", "-t", "-c", "SELECT count(*) FROM ir_module_module"))
    _emit(report, "verify", "ok")


async def _restore_dump(r: _Run, target: str, dump: str) -> None:
    ctx = r.ctx
    with open(dump, "rb") as fh:
        await r.ok("restore", dbx(ctx, "pg_restore", "-U", ctx.user, "--no-owner", "--no-acl", "--exit-on-error", "-d", target,
                                  interactive=True), stdin=fh)


async def _untar(p: DPlan, r: _Run, report: Report, state: dict, archive: str, label: str) -> None:
    _begin(report, state, "filestore", f"Extract the filestore to {p.filestore_dst}")
    tar = os.path.join(archive, "filestore.tar")
    if not p.filestore_dst or not os.path.isfile(tar):
        _emit(report, "filestore", "ok", f"no filestore in the {label}")
        return
    script = 'set -e\nmkdir -- "$1" || exit 3\necho ODP:created\ntar -C "$1" -xf -\n'
    with open(tar, "rb") as fh:
        code, lines = await r.run("filestore", helper(r.ctx, script, p.filestore_dst, interactive=True), stdin=fh)
    state["fs"] = CREATED in lines
    if code != 0:
        raise DockerError("the filestore folder exists already" if code == 3 else f"filestore ended with exit code {code}")
    _emit(report, "filestore", "ok")


async def _dispatch(p: DPlan, r: _Run, report: Report, state: dict) -> dict:
    ctx = r.ctx
    if p.kind == "forget":
        _begin(report, state, "forget", f"Remove {p.backup}")
        folder = p.backup or ""
        try:
            manifest = json.loads(Path(folder, "manifest.json").read_text())
        except (OSError, ValueError) as exc:
            raise DockerError(f"{folder} has no readable manifest.json") from exc
        if manifest.get("snapshot") is not True:
            raise DockerError(f"{folder} is not a snapshot: refused")
        shutil.rmtree(folder)
        _emit(report, "forget", "ok")
        return {"removed": folder}
    if p.kind in ("backup", "snapshot"):
        _begin(report, state, "mkdir", f"Make {p.backup}")
        parent = os.path.dirname(p.backup or "")
        os.makedirs(parent, mode=0o750, exist_ok=True)
        os.mkdir(p.backup or "", 0o750)
        state["dir"] = True
        _emit(report, "mkdir", "ok")
        _begin(report, state, "dump", f"Dump {p.source}")
        with open(f"{p.backup}/dump.pgdump", "wb") as fh:
            await r.ok("dump", dbx(ctx, "pg_dump", "-U", ctx.user, "-Fc", "-d", p.source or ""), stdout=fh)
        _emit(report, "dump", "ok")
        if p.filestore_src:
            _begin(report, state, "filestore", f"Archive {p.filestore_src}")
            with open(f"{p.backup}/filestore.tar", "wb") as fh:
                await r.ok("filestore", helper(ctx, 'set -e\ntar -C "$1" -cf - .\n', p.filestore_src), stdout=fh)
            _emit(report, "filestore", "ok")
        _begin(report, state, "manifest", "Write manifest.json and SHA256SUMS")
        Path(p.backup or "", "manifest.json").write_text(json.dumps({
            "tool": "odoo-dev-panel", "database": p.source, "installation": p.root, "container": p.container,
            "filestore": bool(p.filestore_src), "filestore_path": p.filestore_src,
            **({"snapshot": True} if p.kind == "snapshot" else {}),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2) + "\n")
        write_sums(p.backup or "")
        _emit(report, "manifest", "ok")
        _begin(report, state, "verify", "Read the dump back")
        with open(f"{p.backup}/dump.pgdump", "rb") as fh:
            lines = await r.ok("verify", dbx(ctx, "pg_restore", "--list", interactive=True), stdin=fh)
        _emit(report, "verify", "ok", f"{sum(1 for x in lines if not x.startswith(';'))} entries in the dump")
        pruned: list[str] = []
        if p.kind == "snapshot":
            state["dir"] = False  # complete: a failure from here on must not remove the new snapshot
            _begin(report, state, "prune", f"Keep the newest {p.keep} snapshot(s) of {p.source}")
            pruned = prune(os.path.dirname(p.backup or ""), p.source or "", p.keep or DEFAULT_KEEP)
            _emit(report, "prune", "ok", f"removed {', '.join(pruned)}" if pruned else "nothing to remove")
        return {"backup": p.backup, "database": p.source, "filestore": bool(p.filestore_src),
                **({"pruned": pruned} if p.kind == "snapshot" else {})}

    if p.kind == "restore":
        _begin(report, state, "checksums", f"Check {p.backup}")
        check_sums(p.backup or "")
        _emit(report, "checksums", "ok")
        await _create_and_fill(p, r, report, state, lambda: _restore_dump(r, p.target or "", f"{p.backup}/dump.pgdump"),
                               f"Restore the dump into {p.target}")
        await _untar(p, r, report, state, p.backup or "", "backup")
        await _verify(p, r, report, state)
        return {"database": p.target, "filestore": p.filestore_dst if state["fs"] else None}

    if p.kind == "clone":
        async def copy() -> None:
            await r.ok("copy-db", dbx(ctx, "bash", "-c", 'set -o pipefail\npg_dump -U "$1" -Fc -d "$2" | '
                                      'pg_restore -U "$1" --no-owner --no-acl --exit-on-error -d "$3"\n', "odp",
                                      ctx.user, p.source or "", p.target or ""))
        await _create_and_fill(p, r, report, state, copy, f"Copy {p.source} into {p.target}")
        if p.filestore_src:
            _begin(report, state, "filestore", f"Copy {p.filestore_src} to {p.filestore_dst}")
            code, lines = await r.run("filestore", helper(ctx, cmd.COPY_TREE_SCRIPT, p.filestore_src, p.filestore_dst or ""))
            state["fs"] = CREATED in lines
            if code != 0:
                raise DockerError("the filestore folder exists already" if code == 3 else f"filestore ended with exit code {code}")
            _emit(report, "filestore", "ok")
        if p.recipe:
            await _neutralize(p, r, p.target or "", report, state)
        await _verify(p, r, report, state)
        return {"database": p.target, "filestore": p.filestore_dst if state["fs"] else None, "recipe": p.recipe}

    if p.kind == "neutralize":
        await _neutralize(p, r, p.source or "", report, state)
        return {"database": p.source, "recipe": p.recipe}

    if p.kind == "drop":
        trash = None
        if p.filestore_src:
            _begin(report, state, "filestore", f"Move {p.filestore_src} to a trash folder")
            trash = trash_dir(FS_BASE, p.source or "", ops._stamp())
            await r.ok("filestore", helper(ctx, cmd.MOVE_SCRIPT, p.filestore_src, trash))
            state["trash"] = (p.filestore_src, trash)
            _emit(report, "filestore", "ok", trash)
        _begin(report, state, "dropdb", f"Drop {p.source}")
        await r.ok("dropdb", dbx(ctx, "dropdb", "-U", ctx.user, "--", p.source or ""))
        state.pop("trash", None)
        _emit(report, "dropdb", "ok")
        return {"database": p.source, "trash": trash}

    if p.kind == "revert":
        _begin(report, state, "checksums", f"Check {p.backup}")
        check_sums(p.backup or "")
        _emit(report, "checksums", "ok")
        _begin(report, state, "rename", f"Keep the current {p.source} as {p.aside}")
        await r.ok("rename", psql(ctx, "postgres", "-c", cmd.rename_sql(p.source or "", p.aside or "")))
        state["renamed"] = True
        _emit(report, "rename", "ok")
        if p.filestore_src:
            _begin(report, state, "aside-fs", f"Move {p.filestore_src} to {p.aside_fs}")
            await r.ok("aside-fs", helper(ctx, cmd.MOVE_SCRIPT, p.filestore_src, p.aside_fs or ""))
            state["trash"] = (p.filestore_src, p.aside_fs)
            _emit(report, "aside-fs", "ok")
        await _create_and_fill(p, r, report, state, lambda: _restore_dump(r, p.target or "", f"{p.backup}/dump.pgdump"),
                               f"Restore the snapshot into {p.target}")
        await _untar(p, r, report, state, p.backup or "", "snapshot")
        await _verify(p, r, report, state)
        return {"database": p.target, "aside": p.aside, "aside_fs": p.aside_fs if p.filestore_src else None}
    raise DockerError(f"unknown action {p.kind}")


async def _neutralize(p: DPlan, r: _Run, database: str, report: Report, state: dict) -> None:
    _begin(report, state, "neutralize", f"Run recipe {p.recipe} on {database} only")
    sql = recipes.render(p.recipe_sql or "", database)
    try:
        proc = await asyncio.create_subprocess_exec(*psql(r.ctx, database, "-f", "-", interactive=True),
                                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = await proc.communicate(sql.encode())
        for line in err.decode(errors="replace").splitlines():
            _emit(report, "neutralize", "output", line)
        if proc.returncode != 0:
            raise DockerError(f"psql ended with exit code {proc.returncode}")
    except DockerError as exc:
        state["neutralize_failed"] = True
        raise DockerError(f"recipe {p.recipe} failed on {database}: {exc}. {database} is left as it is, NOT neutralized"
                          if p.kind == "clone" else f"recipe {p.recipe} failed on {database}: {exc}") from exc
    _emit(report, "neutralize", "ok")


async def _rollback(p: DPlan, r: _Run, report: Report, state: dict) -> None:
    """Remove only what this run made; same order as ``database.ops``."""
    async def attempt(step: str, argv: list[str] | None, action=None) -> None:
        try:
            if action:
                action()
            else:
                await r.ok("rollback", argv or [])
            _emit(report, "rollback", "output", step)
        except Exception as exc:  # noqa: BLE001
            _emit(report, "rollback", "output", f"could not {step}: {exc}")

    ctx = r.ctx
    if p.kind in ("clone", "restore") and state.get("neutralize_failed"):
        _emit(report, "rollback", "output", f"{p.target} and its filestore are kept: they are NOT neutralized. Do not use them")
        return
    if state.get("fs") and p.filestore_dst:
        await attempt(f"remove {p.filestore_dst}", helper(ctx, 'rm -rf -- "$1"', p.filestore_dst))
    if state.get("db") and p.target:
        await attempt(f"drop {p.target}", dbx(ctx, "dropdb", "-U", ctx.user, "--if-exists", "--", p.target))
    if state.get("dir") and p.backup:
        await attempt(f"remove {p.backup}", None, lambda: shutil.rmtree(p.backup or "", ignore_errors=True))
    if state.get("trash"):
        src, trash = state["trash"]
        await attempt(f"move {trash} back to {src}", helper(ctx, cmd.MOVE_SCRIPT, trash, src))
    if state.get("renamed"):
        await attempt(f"rename {p.aside} back to {p.source}",
                      psql(ctx, "postgres", "-c", cmd.rename_sql(p.aside or "", p.source or "")))


# -- snapshots ---------------------------------------------------------------

def list_snapshots(container: str, database: str | None = None, home: str | None = None) -> list[dict]:
    """Snapshots on this machine (the developer's own folders), newest first."""
    return _list_in(snapshot_root(container, home), database)


def _list_in(where: str, database: str | None = None) -> list[dict]:
    out = []
    try:
        names = os.listdir(where)
    except OSError:
        return []
    for name in names:
        folder = os.path.join(where, name)
        try:
            manifest = json.loads(Path(folder, "manifest.json").read_text())
            size = sum(os.path.getsize(os.path.join(folder, n)) for n in os.listdir(folder))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or manifest.get("snapshot") is not True or not SNAPSHOT_NAME.fullmatch(name):
            continue
        if database and manifest.get("database") != database:
            continue
        out.append({"path": folder, "name": name, "database": manifest.get("database"), "created_at": manifest.get("created_at"),
                    "filestore": bool(manifest.get("filestore")), "bytes": size, "label": manifest.get("label")})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def prune(where: str, database: str, keep: int) -> list[str]:
    """Remove snapshots of ``database`` in ``where`` beyond the newest ``keep``. Manual backups and other databases stay."""
    removed = []
    for s in _list_in(where, database)[keep:]:
        shutil.rmtree(s["path"], ignore_errors=True)
        removed.append(s["path"])
    return removed
