"""K1: the step types of a workflow. Each one wraps an existing core plan and its execution, so a workflow step
checks, shows and runs exactly what the matching panel or ``odp`` command would.

A planned step is ``{ok, checks, commands, identity, gate, raw}``: ``raw`` is the underlying plan and never leaves
the core (a database plan holds the PostgreSQL password).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from dataclasses import dataclass, field
from typing import Awaitable, Callable

Report = Callable[[dict], None]
RUN_AS_PATH = "/usr/local/bin:/usr/bin:/bin"


class StepError(ValueError):
    pass


@dataclass
class Op:
    title: str
    fields: dict[str, str]            # name -> text, list, bool, int
    required: tuple[str, ...] = ()
    gate: bool = False                # pauses for confirmation unless the step says auto = true
    always_gate: bool = False         # auto is ignored: confirmed on every run
    plan: Callable[..., Awaitable[dict]] | None = field(default=None, repr=False)
    execute: Callable[..., Awaitable[dict]] | None = field(default=None, repr=False)


def _check(cid: str, status: str, detail: str) -> dict:
    return {"id": cid, "status": status, "detail": detail}


def _ok(checks: list[dict]) -> bool:
    return not any(c["status"] == "fail" for c in checks)


def _installation_of(snapshot: dict, step: dict) -> dict:
    root = step.get("installation")
    if not root and step.get("config"):
        inst = next((i for i in snapshot["instances"] if i["path"] == os.path.normpath(step["config"])), None)
        if inst is None:
            raise StepError(f"{step['config']} is not a discovered Odoo config")
        root = inst.get("installation")
    if not root:
        raise StepError("installation (or config) is required")
    found = next((i for i in snapshot["installations"] if i["root"] == os.path.normpath(root)), None)
    if found is None:
        raise StepError(f"{root} is not a discovered Odoo installation")
    return found


def _you(env) -> str:
    return f"{env.user} (you)"


def _run_as(user: str | None) -> str:
    return f"{user} (run-as user, through its agent)" if user else "the run-as user"


# -- git ----------------------------------------------------------------------------

def _git(op: str):
    async def plan(step: dict, env) -> dict:
        from ..git import api

        params = {"repos": step["repos"]} if step.get("repos") else {"bulk": True, "installation": step.get("installation")}
        if not step.get("repos") and not step.get("installation"):
            raise StepError("installation or repos is required")
        try:
            p = await asyncio.to_thread(api.plan, op, params, await env.snapshot(False))
        except (api.ApiError, LookupError) as exc:
            raise StepError(str(exc)) from exc
        d = p.as_dict()
        checks = list(d["checks"])
        if not p.ok:
            checks.append(_check("runnable", "fail", f"no repository can {op}"))
        return {"checks": checks, "commands": [c for s in d["steps"] for c in s["commands"]], "identity": _you(env),
                "raw": p}

    async def execute(planned: dict, env, report: Report) -> dict:
        from ..git import ops

        out = await ops.run_plan(planned["raw"], report, env.cancelled)
        bad = out["counts"]["failed"] + out["counts"]["cancelled"]
        return {"ok": bad == 0, "summary": ", ".join(f"{v} {k}" for k, v in out["counts"].items() if v),
                "repos": [{k: r.get(k) for k in ("repo", "status", "reason", "changed_files", "changed_modules")}
                          for r in out["results"]]}

    return plan, execute


# -- databases --------------------------------------------------------------------

def _db(kind: str):
    async def plan(step: dict, env) -> dict:
        from ..database import context, ops

        inst = _installation_of(await env.snapshot(False), step)
        try:
            inst, ctx = await context.prepare(inst["root"])
        except context.NotFound as exc:
            raise StepError(str(exc)) from exc
        database = step.get("database")
        backup = step.get("backup")
        if kind == "revert" and not backup and database:
            snaps = await ops.list_snapshots(inst, ctx, database)
            backup = snaps[0]["path"] if snaps else None
        p = await asyncio.to_thread(
            ops.plan_db, kind, inst, ctx,
            source=database if kind != "restore" else None,
            target=step.get("target") or None, backup=backup or None, dest=step.get("dest") or None,
            recipe=step.get("recipe") or None,
            # the gate of the step is the confirmation of the typed name
            confirm=database if kind in ("drop", "neutralize", "revert") else None, keep=step.get("keep"))
        d = p.as_dict()
        return {"checks": [{"id": c["id"], "status": c["status"], "detail": c["detail"]} for c in d["checks"]],
                "commands": [c for s in d["steps"] for c in s["commands"]],
                "identity": f"{p.run_as or 'the installation role'} (PostgreSQL role and filestore owner)", "raw": p}

    async def execute(planned: dict, env, report: Report) -> dict:
        from ..database import ops

        try:
            out = await ops.run_db(planned["raw"], report)
        except ops.DbError as exc:
            raise StepError(str(exc)) from exc
        keep = {k: out[k] for k in ("backup", "database", "trash", "aside", "receipt") if out.get(k)}
        return {"ok": True, "summary": out.get("backup") or out.get("database") or kind, **keep}

    return plan, execute


# -- modules ----------------------------------------------------------------------

def _modules(kind: str):
    async def plan(step: dict, env) -> dict:
        from ..module_center import api

        params = {"config": step.get("config"), "modules": step.get("modules"), "database": step.get("database"),
                  "snapshot": step.get("snapshot", True), "tags": step.get("tags"), "demo": step.get("demo", True)}
        try:
            p = await api.plan(kind, params, await env.snapshot(kind != "test"))
        except api.ApiError as exc:
            raise StepError(str(exc)) from exc
        return {"checks": p["checks"], "commands": [c for s in p["steps"] for c in s["commands"]],
                "identity": _run_as(p["session"]["user"]), "raw": p}

    async def execute(planned: dict, env, report: Report) -> dict:
        from ..module_center import api

        p = planned["raw"]
        conn = await env.agent(p["session"]["user"])
        try:
            out = await api.execute(p, conn, report)
        except api.ApiError as exc:
            raise StepError(str(exc)) from exc
        if kind == "test":
            return {"ok": out["status"] == "passed", "summary": f"{out['status']}: {out['tests']} tests, {out['failures']} "
                    f"failed, {out['errors']} errors" + (f"; {out['database']} kept" if out["kept"] else ""),
                    "session": out["session"], "test_id": out["id"]}
        return {"ok": out["exit_code"] == 0, "summary": f"exit code {out['exit_code']}", "session": out["session"],
                "backup": out.get("backup")}

    return plan, execute


# -- python environment -------------------------------------------------------------

def _python(kind: str):
    async def plan(step: dict, env) -> dict:
        from .. import client
        from ..pyenv import api

        snap = await env.snapshot(False)
        inst = _installation_of(snap, step)
        params = {"op": kind, "root": inst["root"], "packages": step.get("packages") or [],
                  "missing": step.get("missing", False)}
        running = None
        if inst.get("owner"):
            running = (await client.agent_status(inst["owner"]))["state"] == "running"
        try:
            p = await asyncio.to_thread(api.plan, params, snap, running)
        except api.ApiError as exc:
            raise StepError(str(exc)) from exc
        return {"checks": p["checks"], "commands": [c for s in p["steps"] for c in s["commands"]],
                "identity": _run_as(p["session"]["user"]), "raw": p}

    async def execute(planned: dict, env, report: Report) -> dict:
        from ..pyenv import api

        p = planned["raw"]
        conn = await env.agent(p["session"]["user"])
        try:
            out = await api.execute(p, conn, report, None)
        except api.ApiError as exc:
            raise StepError(str(exc)) from exc
        failed = out.get("failed") or []
        return {"ok": out["ok"], "summary": f"exit code {out['exit_code']}" + (f"; failed imports: {', '.join(failed[:10])}"
                                                                               if failed else ""),
                "session": out["session"]}

    return plan, execute


# -- instances ---------------------------------------------------------------------

async def _instance_start_plan(step: dict, env) -> dict:
    from .. import rpc, run

    snap = await env.snapshot(False)
    busy = {p["port"] for p in snap["processes"] if p.get("port")}
    params = {k: step[k] for k in ("http_port", "dev", "debug_port", "debug_wait", "log_sql") if step.get(k)}
    if step.get("database"):
        params["db"] = step["database"]
    try:
        busy |= set(await asyncio.to_thread(run.listening_ports))
        p = run.plan(snap, step.get("config") or "", params, busy | ({step["debug_port"]} if step.get("debug_port") else set()))
    except rpc.RpcError as exc:
        raise StepError(exc.message) from exc
    port = p["meta"]["port"]
    checks = [_check("port", "ok", f"serves on http://localhost:{port}")] if port else []
    if step.get("debug_port"):
        from ..debug import launch

        inst = _installation_of(snap, {"config": step.get("config")})
        checks += launch.common_checks({"port": step["debug_port"], "wait": bool(step.get("debug_wait")),
                                        "dev": step.get("dev") or []}, inst, busy)
    return {"checks": checks,
            "commands": [shlex.join(p["argv"])], "identity": _run_as(p["user"]), "raw": p}


async def _instance_start(planned: dict, env, report: Report) -> dict:
    p = dict(planned["raw"])
    conn = await env.agent(p.pop("user"))
    session = await conn.request("session.start", p)
    report({"step": "start", "status": "output", "text": f"session {session['id']} started"})
    await asyncio.sleep(2)
    state = await conn.request("session.get", {"id": session["id"]})
    if state["state"] not in ("running", "stopping"):
        return {"ok": False, "summary": f"Odoo exited at once (exit code {state.get('exit_code')}); see Sessions",
                "session": session["id"]}
    port = p["meta"].get("port")
    return {"ok": True, "summary": f"running on http://localhost:{port}" if port else "running", "session": session["id"],
            "port": port}


async def _instance_stop_plan(step: dict, env) -> dict:
    from .. import client, rpc

    snap = await env.snapshot(False)
    config = os.path.normpath(step.get("config") or "")
    inst = _installation_of(snap, {"config": config})
    user = inst.get("owner")
    sessions = []
    checks = []
    if user and (await client.agent_status(user))["state"] == "running":
        try:
            conn = await env.agent(user)
            rows = await conn.request("session.list", timeout=10)
            sessions = [s for s in rows if (s.get("meta") or {}).get("instance") == config
                        and s.get("state") in ("running", "stopping")]
        except (rpc.RpcError, rpc.ConnectionClosed, asyncio.TimeoutError) as exc:
            checks.append(_check("agent", "fail", f"the agent of {user} did not answer: {exc}"))
    if not sessions and not checks:
        checks.append(_check("sessions", "ok", "no running session of this config: nothing to stop"))
    for s in sessions:
        checks.append(_check(s["id"], "ok", f"stops session {s['id']} ({s.get('name')})"))
    return {"checks": checks, "commands": [f"odp stop -u {user} {s['id']}" for s in sessions],
            "identity": _run_as(user), "raw": {"user": user, "sessions": [s["id"] for s in sessions]}}


async def _instance_stop(planned: dict, env, report: Report) -> dict:
    raw = planned["raw"]
    if not raw["sessions"]:
        return {"ok": True, "summary": "nothing was running"}
    conn = await env.agent(raw["user"])
    for sid in raw["sessions"]:
        report({"step": "stop", "status": "output", "text": f"stopping session {sid}"})
        await conn.request("session.stop", {"id": sid, "timeout": 15}, timeout=30)
    return {"ok": True, "summary": f"stopped {len(raw['sessions'])} session(s)"}


# -- command (the trust boundary) ----------------------------------------------------

async def _command_plan(step: dict, env) -> dict:
    argv = step["argv"]
    if not all(isinstance(a, str) and a and "\0" not in a for a in argv):
        raise StepError("argv items are non-empty text")
    as_ = step.get("as", "me")
    checks = []
    inst = None
    if as_ == "run-as" or step.get("installation") or step.get("config"):
        inst = _installation_of(await env.snapshot(False), step)
    cwd = step.get("cwd") or (inst["root"] if inst else str(os.path.expanduser("~")))
    if not cwd.startswith("/"):
        raise StepError("cwd must be an absolute path")
    if as_ == "run-as":
        user = inst.get("owner")
        if not user:
            raise StepError(f"{inst['root']} has no run-as user")
        identity = f"{user} (run-as user of {inst['root']}, through its agent)"
        if "/" not in argv[0]:  # the agent runs absolute programs only: resolve on the system PATH, shown in the plan
            found = shutil.which(argv[0], path=RUN_AS_PATH)
            if found is None:
                checks.append(_check("program", "fail", f"{argv[0]} is not in {RUN_AS_PATH}; give its absolute path"))
            else:
                argv = [found, *argv[1:]]
    else:
        user = env.user
        identity = f"{env.user} (you, with your own permissions)"
        if not os.path.isdir(cwd):
            checks.append(_check("cwd", "fail", f"{cwd} is not a folder"))
        if "/" not in argv[0] and shutil.which(argv[0]) is None:
            checks.append(_check("program", "fail", f"{argv[0]} is not on PATH"))
    checks.append(_check("identity", "warn", f"runs as {identity}, in {cwd}"))
    return {"checks": checks, "commands": [shlex.join(argv)], "identity": identity,
            "raw": {"argv": list(argv), "cwd": cwd, "as": as_, "user": user,
                    "installation": inst["root"] if inst else None}}


async def _command(planned: dict, env, report: Report) -> dict:
    raw = planned["raw"]
    if raw["as"] == "run-as":
        from ..module_center.actions import run_session

        conn = await env.agent(raw["user"])
        session = {"argv": raw["argv"], "cwd": raw["cwd"], "name": f"task {os.path.basename(raw['argv'][0])}",
                   "meta": {"installation": raw["installation"], "kind": "task"}}
        code, sid, _text = await run_session(conn, session, report, "command")
        return {"ok": code == 0, "summary": f"exit code {code}", "session": sid}
    proc = await asyncio.create_subprocess_exec(*raw["argv"], cwd=raw["cwd"], stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                start_new_session=True)
    assert proc.stdout is not None
    async for line in proc.stdout:
        report({"step": "command", "status": "output", "text": line.decode(errors="replace").rstrip("\n")})
    code = await proc.wait()
    return {"ok": code == 0, "summary": f"exit code {code}"}


# -- the registry -----------------------------------------------------------------

_DB_TARGET = {"installation": "text", "config": "text"}
OPS: dict[str, Op] = {
    "git.fetch": Op("Fetch repositories", {"installation": "text", "repos": "list"}),
    "git.pull": Op("Pull repositories (fast-forward only)", {"installation": "text", "repos": "list"}),
    "db.snapshot": Op("Snapshot a database", {**_DB_TARGET, "database": "text", "keep": "int"}, ("database",)),
    "db.backup": Op("Back up a database", {**_DB_TARGET, "database": "text", "dest": "text"}, ("database",)),
    "db.clone": Op("Clone a database", {**_DB_TARGET, "database": "text", "target": "text", "recipe": "text"},
                   ("database", "target")),
    "db.neutralize": Op("Neutralize a database", {**_DB_TARGET, "database": "text", "recipe": "text"}, ("database",),
                        gate=True),
    "db.restore": Op("Restore a backup", {**_DB_TARGET, "backup": "text", "target": "text"}, ("backup", "target"),
                     gate=True),
    "db.revert": Op("Revert a database to a snapshot", {**_DB_TARGET, "database": "text", "backup": "text"},
                    ("database",), gate=True),
    "db.drop": Op("Drop a database", {**_DB_TARGET, "database": "text"}, ("database",), gate=True, always_gate=True),
    "modules.upgrade": Op("Upgrade modules", {"config": "text", "database": "text", "modules": "list",
                                              "snapshot": "bool"}, ("config", "database", "modules"), gate=True),
    "modules.install": Op("Install modules", {"config": "text", "database": "text", "modules": "list",
                                              "snapshot": "bool"}, ("config", "database", "modules"), gate=True),
    "modules.test": Op("Test modules in a throwaway database", {"config": "text", "modules": "list", "tags": "text",
                                                                 "demo": "bool"}, ("config", "modules")),
    "python.validate": Op("Validate the Python environment", {**_DB_TARGET}),
    "python.install": Op("Install Python packages", {**_DB_TARGET, "packages": "list", "missing": "bool"}, gate=True),
    "instance.start": Op("Start an instance", {"config": "text", "database": "text", "http_port": "int",
                                               "dev": "list", "debug_port": "int", "debug_wait": "bool", "log_sql": "bool"},
                         ("config",)),
    "instance.stop": Op("Stop an instance's sessions", {"config": "text"}, ("config",)),
    "command": Op("Run a command", {"argv": "list", "as": "text", "cwd": "text", **_DB_TARGET}, ("argv",),
                  gate=True, always_gate=True),
}
for _name in ("fetch", "pull"):
    OPS[f"git.{_name}"].plan, OPS[f"git.{_name}"].execute = _git(_name)
for _name in ("snapshot", "backup", "clone", "neutralize", "restore", "revert", "drop"):
    OPS[f"db.{_name}"].plan, OPS[f"db.{_name}"].execute = _db(_name)
for _name in ("upgrade", "install", "test"):
    OPS[f"modules.{_name}"].plan, OPS[f"modules.{_name}"].execute = _modules(_name)
for _name in ("validate", "install"):
    OPS[f"python.{_name}"].plan, OPS[f"python.{_name}"].execute = _python(_name)
OPS["instance.start"].plan, OPS["instance.start"].execute = _instance_start_plan, _instance_start
OPS["instance.stop"].plan, OPS["instance.stop"].execute = _instance_stop_plan, _instance_stop
OPS["command"].plan, OPS["command"].execute = _command_plan, _command


def gated(step: dict) -> bool:
    op = OPS[step["op"]]
    if op.always_gate:
        return True
    if step.get("confirm"):
        return True
    return op.gate and not step.get("auto", False)


def catalog() -> list[dict]:
    """The step types for the editor and ``odp tasks ops``."""
    return [{"op": name, "title": op.title, "fields": op.fields, "required": list(op.required), "gate": op.gate,
             "always_gate": op.always_gate} for name, op in OPS.items()]
