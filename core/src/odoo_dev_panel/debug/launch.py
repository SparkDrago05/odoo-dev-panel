"""Q2: start Odoo under debugpy as the run-as user, through its agent, for an IDE to attach.

The debugger listens on 127.0.0.1 only, on the preset's port. While it listens, any local user can connect to
that port and run code as the run-as user: every plan says so. Debug runs add ``--workers=0
--max-cron-threads=0`` so requests are served by the process the debugger is in. A test preset runs the module
center's throwaway-database tests (dropped when they pass, kept when they fail) under the debugger.
"""

from __future__ import annotations

import shlex
from typing import Callable

from .. import rpc, run
from ..pyenv import env as env_mod

Report = Callable[[dict], None]


class LaunchError(ValueError):
    pass


def _check(cid: str, status: str, detail: str) -> dict:
    return {"id": cid, "status": status, "detail": detail}


def attach(preset: dict) -> dict:
    return {"host": "127.0.0.1", "port": preset["port"], "wait": preset["wait"]}


def _installation(snapshot: dict, config: str) -> dict:
    inst = next((i for i in snapshot["instances"] if i["path"] == config), None)
    if inst is None:
        raise LaunchError(f"{config} is not a discovered Odoo config")
    home = next((i for i in snapshot["installations"] if i["root"] == inst.get("installation")), None)
    if home is None:
        raise LaunchError(f"{config} is not linked to an installation")
    return home


def common_checks(preset: dict, inst: dict, listening: set[int], packages: Callable[[dict], dict] | None = None) -> list[dict]:
    checks = []
    found = (packages or (lambda i: env_mod.packages(i, i.get("python_version"))))(inst).get("debugpy")
    checks.append(_check("debugpy", "ok" if found else "fail",
                         f"debugpy {found} in {inst.get('venv')}" if found
                         else f"debugpy is not in {inst.get('venv')}: install it from the Python tab "
                              f"(odp python tool debugpy --root {inst['root']})"))
    port = preset["port"]
    checks.append(_check("port", "fail" if port in listening else "ok",
                         f"something already listens on port {port}: stop it or change the preset's port"
                         if port in listening else f"debugpy listens on 127.0.0.1:{port}"))
    checks.append(_check("local-users", "warn", f"while it listens, any local user can connect to 127.0.0.1:{port} "
                                                f"and run code as {inst.get('owner')}"))
    if "reload" in preset.get("dev", []):
        checks.append(_check("reload", "warn", "--dev=reload restarts Odoo on file changes; the restarted process "
                                               "is not under the debugger"))
    checks.append(_check("workers", "ok", "runs with --workers=0 --max-cron-threads=0 (the config is not changed)"))
    if preset["wait"]:
        checks.append(_check("wait", "warn", "Odoo does not start until the IDE attaches"))
    return checks


async def plan(preset: dict, snapshot: dict, listening: set[int], packages=None) -> dict:
    """Checks, the exact command, the session to start and the attach settings. Raises LaunchError."""
    inst = _installation(snapshot, preset["instance"])
    debug = {"port": preset["port"], "wait": preset["wait"]}
    if preset["kind"] == "test":
        from ..module_center import actions, api

        try:
            graph = await api.load({"config": preset["instance"]}, snapshot, with_changes=False)
            p = actions.test_plan(preset["instance"], snapshot, graph, preset["modules"], preset["tags"], preset["demo"],
                                  debug=debug)
        except actions.ActionError as exc:
            raise LaunchError(str(exc)) from exc
        p["session"]["meta"]["preset"] = preset["id"]
        checks = common_checks(preset, inst, listening, packages) + p["checks"]
        p.update(checks=checks, ok=not any(c["status"] == "fail" for c in checks))
        return {"kind": "test", "preset": preset, "ok": p["ok"], "checks": checks,
                "commands": [c for s in p["steps"] for c in s["commands"]], "user": p["session"]["user"],
                "attach": attach(preset), "test": p}
    params = {"db": preset["database"], "update": preset["update"], "install": preset["install"],
              "dev": preset["dev"], "http_port": preset["http_port"], "debug_port": preset["port"],
              "debug_wait": preset["wait"]}
    try:
        session = run.plan(snapshot, preset["instance"], params, set(listening) | {preset["port"]})
    except rpc.RpcError as exc:
        raise LaunchError(exc.message) from exc
    session["meta"]["preset"] = preset["id"]
    session["name"] = f"{session['name']} (debug {preset['name']})"
    checks = common_checks(preset, inst, listening, packages)
    if session["meta"]["port"]:
        checks.append(_check("http", "ok", f"serves on http://localhost:{session['meta']['port']}"))
    return {"kind": "server", "preset": preset, "ok": not any(c["status"] == "fail" for c in checks), "checks": checks,
            "commands": [shlex.join(session["argv"])], "user": session["user"], "attach": attach(preset),
            "session": session}


async def start(planned: dict, conn, report: Report) -> dict:
    """Server: start the session and return it at once. Test: run the tests (until they end) and return the result."""
    if not planned["ok"]:
        raise LaunchError("; ".join(c["detail"] for c in planned["checks"] if c["status"] == "fail"))
    if planned["kind"] == "test":
        from ..module_center import actions

        return {"kind": "test", "test": await actions.test_run(planned["test"], conn, report)}
    s = {k: v for k, v in planned["session"].items() if k != "user"}
    session = await conn.request("session.start", s)
    return {"kind": "server", "session": session, "attach": planned["attach"]}
