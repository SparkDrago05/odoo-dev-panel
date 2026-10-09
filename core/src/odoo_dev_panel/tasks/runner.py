"""K2: preview and run a workflow. Every step is planned again just before it runs, gated steps wait for a
confirmation, the first failure stops the run, and a failed run can be retried from its failed step.

History: ``~/.local/state/odoo-dev-panel/tasks/history.jsonl``, one line per event (start, step, end) so an
interrupted run still shows how far it got. Lines hold titles, statuses, summaries and the shown commands; never
the output of a step (that stays in Sessions and receipts) and never a password.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from .. import paths
from . import steps, workflow

HISTORY_MAX_BYTES = 2 * 1024 * 1024
Report = Callable[[dict], None]
Confirm = Callable[[int, dict], Awaitable[bool]]


class Env(Protocol):
    """What the runner needs from its caller (the sidecar or the CLI)."""
    user: str

    async def snapshot(self, databases: bool) -> dict: ...
    async def agent(self, user: str): ...
    def cancelled(self) -> bool: ...


class RunError(ValueError):
    pass


class LocalEnv:
    """Env of this process: discovery scans, agents from ``agent(user)``, a cancel flag."""

    def __init__(self, agent: Callable[[str], Awaitable], user: str | None = None) -> None:
        self.user = user or pwd.getpwuid(os.getuid()).pw_name
        self._agent = agent
        self.cancel = False

    async def snapshot(self, databases: bool) -> dict:
        from ..discover import scan

        return await asyncio.to_thread(scan.scan, None, databases)

    async def agent(self, user: str):
        return await self._agent(user)

    def cancelled(self) -> bool:
        return self.cancel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _title(step: dict) -> str:
    return step.get("title") or steps.OPS[step["op"]].title


def _public(planned: dict) -> dict:
    return {k: planned[k] for k in ("ok", "checks", "commands", "identity")}


async def _bind(data: dict, given: dict | None, env) -> dict:
    kinds = {p.get("kind") for p in data.get("params", {}).values()}
    snap = await env.snapshot(False) if kinds & {"installation", "config"} else {"installations": [], "instances": []}
    return workflow.bind(data, given, snap)


async def plan_step(step: dict, values: dict, env) -> dict:
    """One step with its placeholders filled, planned. Errors become a failed check, never an exception."""
    filled = {k: workflow.fill(v, values) for k, v in step.items()}
    out = {"op": step["op"], "title": workflow.fill(_title(step), values), "gate": steps.gated(step),
           "ok": False, "checks": [], "commands": [], "identity": "", "raw": None}
    try:
        planned = await steps.OPS[step["op"]].plan(filled, env)
        out.update(planned)
        out["ok"] = steps._ok(planned["checks"])
    except (steps.StepError, workflow.WorkflowError, LookupError, OSError) as exc:
        out["checks"] = [{"id": "plan", "status": "fail", "detail": str(exc)}]
    return out


async def preview(name: str, given: dict | None, env, start: int = 0) -> dict:
    """The whole workflow planned now. Later steps may depend on earlier ones (a clone a later step uses), so
    their checks can fail here and pass when they run: each step is planned again just before it runs."""
    wf = workflow.load(name)
    values = await _bind(wf["data"], given, env)
    rows = []
    for n, step in enumerate(wf["data"]["steps"]):
        if n < start:
            rows.append({"index": n, "op": step["op"], "title": workflow.fill(_title(step), values), "gate": False,
                         "ok": True, "checks": [], "commands": [], "identity": "", "skipped": "done in the earlier run"})
            continue
        planned = await plan_step(step, values, env)
        rows.append({"index": n, **{k: v for k, v in planned.items() if k != "raw"}})
    return {"name": name, "title": wf["data"].get("name") or name, "description": wf["data"].get("description"),
            "source": wf["source"], "digest": wf["digest"], "params": values, "start": start, "steps": rows,
            "gates": sum(1 for r in rows[start:] if r["gate"])}


async def run(name: str, given: dict | None, env, report: Report, confirm: Confirm, start: int = 0,
              retry_of: str | None = None, run_id: str | None = None, file: Path | None = None) -> dict:
    """Run from step ``start``. Events: ``{"task_step": n, "step", "status", "text"}``; status of the task itself
    (step "task") is start, gate, ok, fail, declined or cancelled; other steps are the operation's own events."""
    wf = workflow.load(name)
    values = await _bind(wf["data"], given, env)
    run_id = run_id or os.urandom(4).hex()
    rows = wf["data"]["steps"]
    if not 0 <= start < len(rows):
        raise RunError(f"start must be a step number from 0 to {len(rows) - 1}")
    _append({"run": run_id, "type": "start", "at": _now(), "workflow": name, "title": wf["data"].get("name") or name,
             "source": wf["source"], "digest": wf["digest"], "params": values, "start": start, "retry_of": retry_of,
             "steps": [workflow.fill(_title(s), values) for s in rows], "user": env.user}, file)
    results: list[dict] = []
    status = "ok"
    failed_at = None
    for n in range(start, len(rows)):
        step = rows[n]

        def sub(event: dict, n=n) -> None:
            report({"task_step": n, **event})

        if env.cancelled():
            status = "cancelled"
            failed_at = n
            break
        began = time.monotonic()
        planned = await plan_step(step, values, env)
        sub({"step": "task", "status": "start", "text": planned["title"]})
        entry = {"index": n, "op": step["op"], "title": planned["title"], "identity": planned["identity"],
                 "commands": planned["commands"], "gate": planned["gate"]}
        if not planned["ok"]:
            detail = "; ".join(c["detail"] for c in planned["checks"] if c["status"] == "fail")
            entry.update(status="fail", summary=f"checks failed: {detail}")
        elif planned["gate"] and not await _ask(confirm, n, planned, sub):
            entry.update(status="declined", summary="not confirmed")
        else:
            try:
                out = await steps.OPS[step["op"]].execute(planned, env, sub)
                entry.update(status="ok" if out.get("ok") else "fail", summary=out.get("summary") or "",
                             result={k: v for k, v in out.items() if k not in ("ok", "summary")})
            except Exception as exc:  # noqa: BLE001 - a step's failure is recorded, then the run stops
                entry.update(status="fail", summary=str(exc) or type(exc).__name__)
        entry["seconds"] = round(time.monotonic() - began, 1)
        sub({"step": "task", "status": entry["status"], "text": entry["summary"]})
        _append({"run": run_id, "type": "step", "at": _now(), **entry}, file)
        results.append(entry)
        if entry["status"] != "ok":
            status, failed_at = entry["status"], n
            break
    end = {"run": run_id, "type": "end", "at": _now(), "status": status, "failed_at": failed_at}
    _append(end, file)
    return {"run_id": run_id, "workflow": name, "status": status, "failed_at": failed_at, "steps": results,
            "retry": failed_at is not None}


async def _ask(confirm: Confirm, n: int, planned: dict, sub: Report) -> bool:
    sub({"step": "task", "status": "gate", "text": planned["title"], "plan": _public(planned)})
    return bool(await confirm(n, _public(planned)))


def retry_point(run_id: str, file: Path | None = None, active: set[str] | frozenset = frozenset()) -> dict:
    """What a retry of ``run_id`` needs: workflow, params, the failed step. Refused when the workflow changed.
    A run without an end that is not ``active`` was interrupted (the app or terminal closed): it can be retried."""
    row = next((r for r in history(file) if r["run"] == run_id), None)
    if row is None:
        raise RunError(f"no run {run_id} in the history")
    if row["status"] == "ok":
        raise RunError("that run finished; start a new one")
    if row["run"] in active:
        raise RunError("that run has not ended")
    wf = workflow.load(row["workflow"])
    if wf["digest"] != row["digest"]:
        raise RunError(f"workflow {row['workflow']} changed since that run; start a new run instead of a retry")
    done = [s for s in row["steps"] if s["status"] == "ok"]
    start = row["failed_at"] if row["failed_at"] is not None else row["start"] + len(done)
    declared = wf["data"].get("params", {})  # derived values (installation) are bound again
    return {"workflow": row["workflow"], "params": {k: v for k, v in row["params"].items() if k in declared},
            "start": start}


# -- history ------------------------------------------------------------------------

def history_path() -> Path:
    return paths.agent_state_dir() / "tasks" / "history.jsonl"


def _append(event: dict, file: Path | None = None) -> None:
    file = file or history_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        if file.stat().st_size > HISTORY_MAX_BYTES:
            lines = file.read_text().splitlines(keepends=True)
            keep = lines[len(lines) // 2:]
            tmp = file.with_name(f".{file.name}.tmp")
            tmp.write_text("".join(keep))
            os.chmod(tmp, 0o600)
            os.replace(tmp, file)
    except FileNotFoundError:
        pass
    fd = os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def history(file: Path | None = None, workflow_name: str | None = None) -> list[dict]:
    """Runs, newest first: the start fields, its steps, and status (running when no end was written)."""
    runs: dict[str, dict] = {}
    try:
        text = (file or history_path()).read_text()
    except OSError:
        return []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or "run" not in ev:
            continue
        kind = ev.get("type")
        if kind == "start":
            runs[ev["run"]] = {**{k: v for k, v in ev.items() if k != "type"}, "titles": ev.get("steps", []),
                               "steps": [], "status": "running", "failed_at": None, "ended_at": None}
        elif ev["run"] in runs and kind == "step":
            runs[ev["run"]]["steps"].append({k: v for k, v in ev.items() if k not in ("type", "run")})
        elif ev["run"] in runs and kind == "end":
            runs[ev["run"]].update(status=ev["status"], failed_at=ev["failed_at"], ended_at=ev["at"])
    rows = [r for r in runs.values() if workflow_name is None or r["workflow"] == workflow_name]
    return rows[::-1]  # file order is start order
