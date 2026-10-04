"""Run Odoo containers: start, stop, restart, upgrade modules, logs, shell. Plans first, like the database actions.

Everything goes through the ``docker`` CLI as the developer. No root, no agent: the Docker daemon keeps the
containers running when the app closes. Secrets never appear in an argv: Odoo run inside a container goes through
the image entrypoint, which reads the database password from the container's own environment.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .discover.ports import listening_ports
from .provision.execute import ProvisionError, Report, _emit
from .provision.plan import Step
from .provision.preflight import FAIL, OK, WARN, Check

KINDS = ("start", "stop", "restart", "upgrade")
STOP_SECONDS = 30
_MODULE = re.compile(r"^[A-Za-z0-9_]+$")
_DBNAME = re.compile(r"^[A-Za-z0-9_.\-]+$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


class DockerError(ProvisionError):
    pass


@dataclass
class ActionPlan:
    kind: str
    container: str
    version: str | None = None
    database: str | None = None
    update: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)
    mode: str | None = None  # upgrade: "exec" (running container) or "compose-run" (stopped compose service)
    argv: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "container": self.container, "version": self.version, "database": self.database,
                "update": self.update, "install": self.install, "mode": self.mode, "ok": self.ok,
                "checks": [vars(c) for c in self.checks], "steps": [vars(s) for s in self.steps]}


def find(containers: list[dict], name: str) -> dict:
    """One discovered Odoo container by name or id prefix. Only containers the discovery calls Odoo are accepted:
    the app never acts on other people's containers."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise DockerError(f"bad container name: {name!r}")
    hits = [c for c in containers if c["name"] == name or (len(name) >= 4 and c["id"].startswith(name))]
    if not hits:
        raise DockerError(f"{name} is not an Odoo container (odp docker lists them)")
    return hits[0]


def _names(values, label: str, pattern=_MODULE) -> list[str]:
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",")]
    out = [v for v in (values or []) if v]
    for v in out:
        if not isinstance(v, str) or not pattern.match(v):
            raise DockerError(f"bad {label}: {v!r}")
    return out


def host_ports(container: dict) -> list[int]:
    return sorted({p["host_port"] for p in container["ports"]})


def busy_ports(containers: list[dict], listening: dict[int, int | None] | None = None,
               skip: str | None = None) -> dict[int, str]:
    """Host ports in use now: by a listener on this machine, or published by a running container (not ``skip``)."""
    busy = {port: "a process on this machine" for port in (listening_ports() if listening is None else listening)}
    for c in containers:
        if c["running"] and c["name"] != skip:
            for port in host_ports(c):
                busy[port] = f"container {c['name']}"
    return busy


def exec_argv(c: dict, *args: str, interactive: bool = False) -> list[str]:
    """``odoo`` run inside the running container through its entrypoint (the official one adds the database options)."""
    return ["docker", "exec", *(["-it"] if interactive else []), c["name"], *c["entrypoint"], c["program"], *args]


def compose_run_argv(c: dict, *args: str) -> list[str]:
    compose = c["compose"]
    files = [x for part in compose["files"] for x in ("-f", part)]
    return ["docker", "compose", "-p", compose["project"], *files, "--project-directory", compose["working_dir"],
            "run", "--rm", "--no-deps", "--no-TTY", compose["service"], c["program"], *args]


def _db_args(database: str | None) -> list[str]:
    return ["-d", database] if database else []


def plan_action(kind: str, container: dict, all_containers: list[dict], database: str | None = None,
                update=None, install=None, listening: dict[int, int | None] | None = None) -> ActionPlan:
    """Checks and steps; changes nothing."""
    p = ActionPlan(kind=kind, container=container["name"], version=container["version"])
    if kind not in KINDS:
        p.checks.append(Check("kind", FAIL, f"unknown action {kind!r}"))
        return p
    name, running = container["name"], container["running"]
    p.checks.append(Check("container", OK, f"{name} ({container['image']}), {container['status']}"))
    if kind == "start":
        p.checks.append(Check("state", FAIL if running else OK, f"{name} is running already" if running else f"{name} is stopped"))
        p.steps = [Step("start", 1, "docker", f"Start {name}", [f"docker start {name}"])]
    if kind in ("start", "restart"):
        busy = busy_ports(all_containers, listening, skip=name)
        mine = set(host_ports(container)) if (kind == "start" or not running) else set()
        clash = sorted(mine & set(busy))
        if clash:
            p.checks.append(Check("ports", FAIL, "host port " + ", ".join(f"{x} is used by {busy[x]}" for x in clash)
                                  + ". Stop it first, or change the port mapping"))
        elif mine:
            p.checks.append(Check("ports", OK, "host port " + ", ".join(map(str, sorted(mine))) + " is free"))
    if kind == "stop":
        p.checks.append(Check("state", OK if running else FAIL, f"{name} is running" if running else f"{name} is not running"))
        p.steps = [Step("stop", 1, "docker", f"Stop {name} (up to {STOP_SECONDS} s for a clean shutdown)",
                        [f"docker stop -t {STOP_SECONDS} {name}"])]
    if kind == "restart":
        p.steps = [Step("restart", 1, "docker", f"Restart {name}", [f"docker restart -t {STOP_SECONDS} {name}"])]
    if kind == "upgrade":
        _plan_upgrade(p, container, database, update, install)
    return p


def _plan_upgrade(p: ActionPlan, c: dict, database, update, install) -> None:
    try:
        p.update, p.install = _names(update, "module"), _names(install, "module")
    except DockerError as exc:
        p.checks.append(Check("modules", FAIL, str(exc)))
        return
    if not (p.update or p.install):
        p.checks.append(Check("modules", FAIL, "give modules to update (-u) or install (-i)"))
    if not database or not _DBNAME.match(database):
        p.checks.append(Check("database", FAIL, "give the database name"))
        return
    p.database = database
    args = [*_db_args(database), *(["-u", ",".join(p.update)] if p.update else []),
            *(["-i", ",".join(p.install)] if p.install else []), "--stop-after-init", "--no-http"]
    if c["running"]:
        p.mode, p.argv = "exec", exec_argv(c, *args)
        p.checks.append(Check("mode", OK, f"runs inside the running {c['name']}, next to the server (--no-http: no second web server)"))
    elif c["compose"] and c["compose"]["working_dir"] and c["compose"]["files"]:
        p.mode, p.argv = "compose-run", compose_run_argv(c, *args)
        p.checks.append(Check("mode", OK, f"{c['name']} is stopped: runs once in a new container of compose service "
                              f"{c['compose']['service']}, removed afterwards"))
    else:
        p.checks.append(Check("mode", FAIL, f"{c['name']} is stopped and not a compose service: start it first"))
        return
    if not c["entrypoint"] and c["program"] == "odoo" and c["running"]:
        p.checks.append(Check("entrypoint", WARN, "the image has no entrypoint: database options from the environment "
                              "are not added, the command may not reach the database"))
    p.steps = [Step("upgrade", 1, "docker", f"Run odoo {' '.join(args)} in {c['name']}", [shlex.join(p.argv)])]


# -- running -----------------------------------------------------------------

async def _stream(argv: list[str], report: Report, step: str) -> int:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    async for line in proc.stdout:
        _emit(report, step, "output", line.decode(errors="replace").rstrip("\n"))
    return await proc.wait()


async def run_action(p: ActionPlan, report: Report) -> dict:
    if not p.ok:
        raise DockerError("the plan has failed checks: " + "; ".join(c.detail for c in p.checks if c.status == FAIL))
    name = p.container
    argv = {"start": ["docker", "start", name], "stop": ["docker", "stop", "-t", str(STOP_SECONDS), name],
            "restart": ["docker", "restart", "-t", str(STOP_SECONDS), name], "upgrade": p.argv}[p.kind]
    _emit(report, p.kind, "start", p.steps[0].title)
    code = await _stream(argv, report, p.kind)
    if code != 0:
        _emit(report, p.kind, "fail", f"exit code {code}")
        raise DockerError(f"{p.kind} of {name} ended with exit code {code}")
    _emit(report, p.kind, "ok")
    return {"container": name, "action": p.kind, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# -- logs and shell ----------------------------------------------------------

def logs_argv(name: str, tail: int = 500, follow: bool = False) -> list[str]:
    return ["docker", "logs", "--tail", str(max(1, min(int(tail), 100_000))), *(["-f"] if follow else []), name]


def read_logs(name: str, tail: int = 500, timeout: int = 20) -> str:
    """The last lines, stdout and stderr merged (Odoo logs to stderr). Parsed by ``logs.analyze`` like session logs."""
    try:
        proc = subprocess.run(logs_argv(name, tail), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"docker logs did not answer within {timeout} s") from exc
    if proc.returncode != 0:
        raise DockerError((proc.stderr.strip().splitlines() or ["docker logs failed"])[-1])
    # Two streams: the order between them is the daemon's, usually the order written. Odoo writes only to stderr.
    return proc.stdout + proc.stderr


def shell_argv(c: dict, database: str) -> list[str]:
    if not _DBNAME.match(database or ""):
        raise DockerError(f"bad database name: {database!r}")
    if not c["running"]:
        raise DockerError(f"{c['name']} is not running: start it first")
    return exec_argv(c, "shell", "-d", database, "--no-http", interactive=True)
