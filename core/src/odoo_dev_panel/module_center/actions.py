"""M4/M5/M7: upgrade or install modules, run module tests in a throwaway database, scaffold a module.

Every action is a plan first (checks, exact command) and runs through the run-as user's agent as a session, so
its log stays in Sessions. Upgrades take a snapshot first unless told not to. Tests never touch a real database:
they run in a new ``odp_test_*`` database that is dropped when the tests pass and kept when they fail.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import paths, rpc, run
from ..git import workspace

Report = Callable[[dict], None]
TEST_PREFIX = "odp_test_"
HISTORY_MAX = 200
LOG_KEEP = 4 * 1024 * 1024
_MODULE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
_TAGS = re.compile(r"^[A-Za-z0-9_,:/.\-+*]{1,300}$")


class ActionError(ValueError):
    pass


def _emit(report: Report, step: str, status: str, text: str = "") -> None:
    report({"step": step, "status": status, "text": text})


def _names(mods) -> list[str]:
    if not isinstance(mods, list) or not mods or len(mods) > 100:
        raise ActionError("modules is a list of 1 to 100 module names")
    for m in mods:
        if not isinstance(m, str) or not _MODULE.match(m):
            raise ActionError(f"bad module name {m!r}")
    return list(dict.fromkeys(mods))


def _series_major(snapshot: dict, config: str) -> int | None:
    inst = next((i for i in snapshot["instances"] if i["path"] == config), None)
    home = next((i for i in snapshot["installations"] if inst and i["root"] == inst.get("installation")), None)
    try:
        return int((home or {}).get("version", "").split(".")[0])
    except ValueError:
        return None


def _check(cid: str, ok: bool, good: str, bad: str, level: str = "fail") -> dict:
    return {"id": cid, "status": "ok" if ok else level, "detail": good if ok else bad}


def _module_checks(graph: dict, mods: list[str], install: bool) -> list[dict]:
    out = []
    for m in mods:
        info = graph["modules"].get(m)
        if info is None:
            out.append(_check(m, False, "", f"{m} is not on this config's addons_path"))
        elif not info.get("loadable", True):
            out.append(_check(m, False, "", f"{m} is in {info['addons_path']}, which this config does not load"))
        elif not info.get("installable", True):
            out.append(_check(m, False, "", f"{m} is not installable"))
        elif "db_state" in info and not install and info["db_state"] != "installed":
            out.append(_check(m, False, "", f"{m} is {info['db_state'] or 'not installed'} in this database: install it instead"))
        elif "db_state" in info and install and info["db_state"] == "installed":
            out.append(_check(m, True, f"{m} is installed already: -i acts like an upgrade", "", "warn"))
            out[-1]["status"] = "warn"
        else:
            out.append(_check(m, True, f"{m} found in {info['addons_path']}", ""))
        problems = [p for p in (info or {}).get("problems", []) if p["level"] == "error"]
        if problems:
            out.append(_check(f"{m}:manifest", False, "", f"{m}: " + "; ".join(p["text"] for p in problems)))
    return out


# -- upgrade / install ---------------------------------------------------------

def upgrade_plan(config: str, snapshot: dict, graph: dict, database, mods, install: bool = False,
                 snapshot_first: bool = True) -> dict:
    mods = _names(mods)
    if not isinstance(database, str) or not database:
        raise ActionError("database is required")
    known = {d["name"] for e in snapshot.get("databases", []) if e["installation"] == graph.get("installation")
             for d in e.get("databases", [])}
    params = {"db": database, ("install" if install else "update"): mods, "stop_after_init": True}
    try:
        planned = run.plan(snapshot, config, params)
    except rpc.RpcError as exc:
        raise ActionError(exc.message) from exc
    checks = _module_checks(graph, mods, install)
    if known and database not in known:
        # -i into a new name lets Odoo create the database; -u needs one that exists
        checks.append(_check("database", False, "", f"no database {database} for this installation: Odoo creates it"
                             if install else f"no database {database} for this installation", "warn" if install else "fail"))
        if install and snapshot_first:
            checks.append(_check("snapshot", False, "", "nothing to snapshot in a new database", "fail"))
    elif known:
        checks.append(_check("database", True, f"database {database} exists", ""))
    steps = []
    if snapshot_first:
        steps.append({"id": "snapshot", "phase": 1, "actor": "agent", "title": f"Snapshot {database} (database and filestore)",
                      "commands": [f"odp db snapshot {graph.get('installation')} {database}"]})
    steps.append({"id": "upgrade", "phase": len(steps) + 1, "actor": planned["user"],
                  "title": f"{'Install' if install else 'Upgrade'} {', '.join(mods)} in {database}, then stop",
                  "commands": [shlex.join(planned["argv"])]})
    return {"kind": "install" if install else "upgrade", "config": config, "database": database, "modules": mods,
            "snapshot": snapshot_first, "checks": checks, "steps": steps, "ok": not any(c["status"] == "fail" for c in checks),
            "session": planned}


async def run_session(conn, planned: dict, report: Report, step: str) -> tuple[int | None, str, str]:
    """Start a session in the agent, stream its log as step output, return (exit code, session id, log text)."""
    import asyncio

    session = await conn.request("session.start", {k: planned[k] for k in ("argv", "cwd", "name", "meta")}
                                 | {"env": planned.get("env") or {}})
    offset, text = 0, []
    size = 0

    def take(data: str) -> None:
        nonlocal size
        for line in data.splitlines():
            _emit(report, step, "output", line)
        if size < LOG_KEEP:
            text.append(data)
            size += len(data)

    while True:
        chunk = await conn.request("session.read", {"id": session["id"], "offset": offset})
        offset = chunk["offset"]
        if chunk["data"]:
            take(chunk["data"])
            continue
        state = await conn.request("session.get", {"id": session["id"]})
        if state["state"] not in ("running", "stopping"):
            chunk = await conn.request("session.read", {"id": session["id"], "offset": offset})
            take(chunk["data"])
            return state["exit_code"], session["id"], "".join(text)
        await asyncio.sleep(0.5)


async def _snapshot(root: str, database: str, report: Report) -> str:
    from ..database import context, ops

    inst, ctx = await context.prepare(root)
    p = ops.plan_db("snapshot", inst, ctx, source=database)
    if not p.ok:
        raise ActionError("no snapshot: " + "; ".join(c.detail for c in p.checks if c.status == "fail"))
    result = await ops.run_db(p, report)
    return result.get("backup") or ""


async def upgrade_run(plan: dict, conn, report: Report) -> dict:
    from .. import logs

    if not plan["ok"]:
        raise ActionError("; ".join(c["detail"] for c in plan["checks"] if c["status"] == "fail"))
    backup = None
    root = plan["session"]["meta"]["installation"]
    if plan["snapshot"]:
        _emit(report, "snapshot", "start", f"Snapshot {plan['database']}")
        backup = await _snapshot(root, plan["database"], report)
        _emit(report, "snapshot", "ok", backup)
    _emit(report, "upgrade", "start", plan["steps"][-1]["title"])
    code, session_id, text = await run_session(conn, plan["session"], report, "upgrade")
    found = logs.analyze(text)
    _emit(report, "upgrade", "ok" if code == 0 else "fail", f"exit code {code}")
    return {"exit_code": code, "session": session_id, "backup": backup, "counts": found["counts"],
            "problems": [{"title": g["title"], "level": g["level"], "count": g["count"]} for g in found["groups"][:10]]}


# -- tests ---------------------------------------------------------------------

def test_db_name(mods: list[str], now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    head = re.sub(r"[^a-z0-9_]", "_", mods[0])[:30]
    return f"{TEST_PREFIX}{head}_{stamp}"


def test_flags(major: int | None, mods: list[str], tags: str | None, demo: bool) -> list[str]:
    flags = ["--test-enable", "--test-tags", tags or ",".join(f"/{m}" for m in mods), "--log-level=test"]
    if major is not None and major >= 19:
        if demo:
            flags.append("--with-demo")     # 19+ loads no demo data unless asked
    elif not demo:
        flags.append("--without-demo=all")
    return flags


def test_plan(config: str, snapshot: dict, graph: dict, mods, tags=None, demo: bool = True,
              now: datetime | None = None) -> dict:
    mods = _names(mods)
    if tags is not None and (not isinstance(tags, str) or not _TAGS.match(tags)):
        raise ActionError("test tags look like /module, :TestClass.test_method, -slow, post_install")
    database = test_db_name(mods, now)
    major = _series_major(snapshot, config)
    params = {"db": database, "install": mods, "stop_after_init": True, "extra": test_flags(major, mods, tags or None, demo)}
    try:
        planned = run.plan(snapshot, config, params)
    except rpc.RpcError as exc:
        raise ActionError(exc.message) from exc
    planned["meta"]["kind"] = "test"
    planned["name"] = f"{graph.get('instance') or 'tests'} test {','.join(mods)}"
    checks = _module_checks(graph, mods, True)
    checks = [c for c in checks if c["status"] != "warn"]  # "installed already" is about real databases
    checks.append(_check("database", True, f"new database {database}, created by Odoo (the role needs CREATEDB)", ""))
    steps = [
        {"id": "test", "phase": 1, "actor": planned["user"], "title": f"Create {database}, install {', '.join(mods)} and run their tests",
         "commands": [shlex.join(planned["argv"])]},
        {"id": "cleanup", "phase": 2, "actor": "agent", "title": "Drop the test database if every test passed; keep it if not",
         "commands": [f"odp db drop {graph.get('installation')} {database} --confirm {database}  (only when passed)"]},
    ]
    return {"kind": "test", "config": config, "database": database, "modules": mods, "tags": tags or None, "demo": demo,
            "checks": checks, "steps": steps, "ok": not any(c["status"] == "fail" for c in checks), "session": planned}


_NEW_SUMMARY = re.compile(r"(\d+) failed, (\d+) error\(s\) of (\d+) tests")
_OLD_SUMMARY = re.compile(r"Module \S+: (\d+) failures?, (\d+) errors? of (\d+) tests")
# "odoo.addons.m.tests.t: FAIL: TestX.test_y" (17+) or "FAIL: test_y (odoo.addons.m.tests.t.TestX.test_y)" (older)
_FAILED = re.compile(r"(?:(odoo\.addons\.[\w.]+): )?\b(FAIL|ERROR): ([\w.]+)(?: \(([\w.]+)\))?")


def parse_results(text: str, exit_code: int | None) -> dict:
    tests = failures = errors = 0
    found = False
    for rx in (_NEW_SUMMARY, _OLD_SUMMARY):
        for m in rx.finditer(text):
            found = True
            failures += int(m.group(1))
            errors += int(m.group(2))
            tests += int(m.group(3))
        if found:
            break
    failed = []
    for m in _FAILED.finditer(text):
        logger, kind, test, full = m.groups()
        if not (full or logger) or "test" not in test.lower():
            continue  # not a test result line
        name = full if full else f"{logger}.{test}"
        entry = {"kind": kind.lower(), "test": name}
        if entry not in failed:
            failed.append(entry)
    if exit_code != 0:
        status = "failed"
    elif failures or errors or failed:
        status = "failed"
    elif not found:
        status = "no-tests"
    else:
        status = "passed"
    return {"status": status, "tests": tests, "failures": failures, "errors": errors, "failed": failed[:50],
            "summary_found": found}


def history_path() -> Path:
    return paths.agent_state_dir() / "module-tests.json"


def history(file: Path | None = None) -> list[dict]:
    try:
        data = json.loads((file or history_path()).read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_history(rows: list[dict], file: Path | None = None) -> None:
    file = file or history_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".module-tests-")
    with os.fdopen(fd, "w") as fh:
        json.dump(rows[:HISTORY_MAX], fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, file)


def record(entry: dict, file: Path | None = None) -> None:
    rows = [r for r in history(file) if r.get("id") != entry["id"]]
    _save_history([entry] + rows, file)


async def _drop(root: str, database: str, report: Report) -> None:
    from ..database import context, ops

    if not database.startswith(TEST_PREFIX):
        raise ActionError(f"{database} is not a test database made by the module center")
    inst, ctx = await context.prepare(root)
    p = ops.plan_db("drop", inst, ctx, source=database, confirm=database)
    if not p.ok:
        raise ActionError("cannot drop: " + "; ".join(c.detail for c in p.checks if c.status == "fail"))
    await ops.run_db(p, report)


async def test_run(plan: dict, conn, report: Report, file: Path | None = None) -> dict:
    if not plan["ok"]:
        raise ActionError("; ".join(c["detail"] for c in plan["checks"] if c["status"] == "fail"))
    root = plan["session"]["meta"]["installation"]
    _emit(report, "test", "start", plan["steps"][0]["title"])
    code, session_id, text = await run_session(conn, plan["session"], report, "test")
    result = parse_results(text, code)
    _emit(report, "test", "ok" if result["status"] == "passed" else "fail",
          f"{result['status']}: {result['tests']} tests, {result['failures']} failed, {result['errors']} errors")
    kept = True
    note = None
    if result["status"] == "passed":
        _emit(report, "cleanup", "start", f"Drop {plan['database']}")
        try:
            await _drop(root, plan["database"], report)
            kept = False
            _emit(report, "cleanup", "ok")
        except Exception as exc:  # noqa: BLE001 - the test result stands; the database stays
            note = f"the test database was kept: {exc}"
            _emit(report, "cleanup", "fail", note)
    entry = {"id": session_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "installation": root,
             "config": plan["config"], "modules": plan["modules"], "tags": plan["tags"], "demo": plan["demo"],
             "database": plan["database"], "kept": kept, "exit_code": code, "session": session_id,
             "user": plan["session"]["user"], "note": note, **result}
    record(entry, file)
    return entry


async def drop_kept(test_id: str, report: Report, file: Path | None = None) -> dict:
    rows = history(file)
    entry = next((r for r in rows if r.get("id") == test_id), None)
    if entry is None or not entry.get("kept"):
        raise ActionError("no kept test database for that run")
    await _drop(entry["installation"], entry["database"], report)
    entry["kept"] = False
    record(entry, file)
    return entry


# -- scaffold ------------------------------------------------------------------

def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def scaffold_plan(snapshot: dict, root, folder, name, title=None, depends=None, listing=workspace.listing) -> dict:
    """Files of a minimal module (manifest, models, security, views) for ``folder/name``. Writes nothing."""
    inst = next((i for i in snapshot.get("installations", []) if i["root"] == root), None)
    if inst is None:
        raise ActionError(f"{root} is not a discovered Odoo installation")
    if not isinstance(name, str) or not _MODULE.match(name) or not name[0].isalpha():
        raise ActionError("module name: lowercase letters, digits and _, starting with a letter")
    if not isinstance(folder, str) or not folder.startswith("/"):
        raise ActionError("folder must be absolute")
    folder = os.path.normpath(folder)
    real = os.path.realpath(folder)
    repos = [os.path.realpath(p) for p in (r["path"] for r in listing(snapshot, None, root, False)["repos"])]
    if not (_inside(real, os.path.realpath(root)) or any(_inside(real, r) for r in repos)):
        raise ActionError(f"{folder} is neither inside {root} nor inside one of its repositories")
    if not os.path.isdir(folder):
        raise ActionError(f"{folder} does not exist")
    target = os.path.join(folder, name)
    if os.path.lexists(target):
        raise ActionError(f"{target} exists already")
    depends = depends or ["base"]
    if not isinstance(depends, list) or not all(isinstance(d, str) and _MODULE.match(d) for d in depends):
        raise ActionError("depends is a list of module names")
    series = inst.get("version") or "17.0"
    title = (title or name.replace("_", " ").title()).replace('"', "'")[:80]
    manifest = (
        "{\n"
        f'    "name": "{title}",\n'
        f'    "version": "{series}.1.0.0",\n'
        '    "summary": "",\n'
        '    "license": "LGPL-3",\n'
        f'    "depends": {json.dumps(depends)},\n'
        '    "data": [\n'
        '        "security/ir.model.access.csv",\n'
        '        "views/views.xml",\n'
        "    ],\n"
        '    "installable": True,\n'
        "}\n"
    )
    files = {
        "__init__.py": "from . import models\n",
        "__manifest__.py": manifest,
        "models/__init__.py": "# from . import my_model\n",
        "security/ir.model.access.csv": "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n",
        "views/views.xml": '<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n</odoo>\n',
    }
    writable = os.access(folder, os.W_OK | os.X_OK)
    return {"target": target, "files": files, "writable": writable, "ok": writable,
            "checks": [_check("folder", writable, f"{folder} is writable", f"you cannot create folders in {folder}")]}


def scaffold_create(plan: dict) -> list[str]:
    if not plan["ok"]:
        raise ActionError(plan["checks"][0]["detail"])
    target = plan["target"]
    os.mkdir(target)  # fails if it appeared in the meantime
    created = []
    for rel, text in plan["files"].items():
        path = os.path.join(target, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
        created.append(path)
    return created
