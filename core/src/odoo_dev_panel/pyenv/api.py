"""One entry point per Python-environment operation, shared by ``odp python`` and the sidecar."""

from __future__ import annotations

from ..git import workspace
from . import actions, env, tools

ApiError = (env.EnvError, actions.ActionError, tools.ToolError)


def repos(snapshot: dict, root: str) -> list[str]:
    rows = workspace.listing(snapshot, None, root, False)["repos"]
    return [r["path"] for r in rows if not r.get("missing")]


def show(params: dict, snapshot: dict) -> dict:
    inst = env.installation(snapshot, params.get("root"))
    return env.describe(inst, repos(snapshot, inst["root"]))


def plan(params: dict, snapshot: dict, agent_running: bool | None = None) -> dict:
    """op: install (packages, missing), validate, tool (tool)."""
    op = params.get("op")
    procs = snapshot.get("processes", [])
    if op == "tool":
        tool = params.get("tool")
        inst = env.installation(snapshot, params.get("root")) if (tool == "debugpy" or params.get("root")) else None
        return tools.plan(tool, inst, procs, agent_running)
    inst = env.installation(snapshot, params.get("root"))
    if op == "install":
        return actions.install_plan(inst, procs, params.get("packages") or [], bool(params.get("missing")),
                                    agent_running, repos(snapshot, inst["root"]))
    if op == "validate":
        return actions.validate_plan(inst, procs, agent_running, repos(snapshot, inst["root"]))
    raise env.EnvError("op is install, validate or tool")


async def execute(p: dict, conn, report, root_runner) -> dict:
    """Venv work goes to the run-as agent (``conn``); system tools to ``root_runner``."""
    if p["kind"] == "tool" and p.get("tool") != "debugpy":
        return await tools.run_system(p, report, root_runner)
    out = await actions.run(p, conn, report)
    if p["kind"] == "tool":
        out["tool"] = p["tool"]
    return out
