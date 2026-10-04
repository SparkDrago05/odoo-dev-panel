"""Create and delete Odoo stacks in Docker from the app: an Odoo container, its PostgreSQL container, a config
folder and an addons folder, as a compose project in ``~/odp-docker/<name>``.

Everything runs as the developer through the ``docker`` CLI. A stack is marked with ``.odp-stack`` so the app only
ever deletes what it created itself, and only after the stack's name is typed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .dockerops import DockerError, busy_ports, find
from .provision.execute import Report, _emit
from .provision.plan import Step
from .provision.preflight import FAIL, OK, WARN, Check

VERSIONS = ("15.0", "16.0", "17.0", "18.0", "19.0", "20.0")
POSTGRES_IMAGE = "postgres:16"
FIRST_PORT = 18069
MARKER = ".odp-stack"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")

COMPOSE = """# Made by Odoo Dev Panel. Edit freely; the app only reads it.
name: {project}
services:
  web:
    image: {image}
    depends_on: [db]
    ports: ["127.0.0.1:{port}:8069"]
    environment:
      HOST: db
      USER: odoo
      PASSWORD: ${{PG_PASSWORD}}
    volumes:
      - ./config:/etc/odoo
      - {addons}:/mnt/extra-addons
      - web-data:/var/lib/odoo
  db:
    image: {postgres}
    environment:
      POSTGRES_DB: postgres
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: ${{PG_PASSWORD}}
    volumes:
      - db-data:/var/lib/postgresql/data
volumes:
  web-data: {{}}
  db-data: {{}}
"""
ODOO_CONF = "[options]\naddons_path = /mnt/extra-addons\n"


def stacks_root(home: str | None = None) -> str:
    return os.path.join(home or os.path.expanduser("~"), "odp-docker")


def project_name(name: str) -> str:
    return f"odp-{name}"


@dataclass
class NewPlan:
    name: str
    version: str
    port: int | None = None
    addons: str | None = None  # an existing folder to mount, or None: a new empty ./addons
    folder: str = ""
    project: str = ""
    image: str = ""
    checks: list[Check] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        return {"kind": "new", "name": self.name, "version": self.version, "port": self.port, "addons": self.addons,
                "folder": self.folder, "project": self.project, "image": self.image, "ok": self.ok,
                "checks": [vars(c) for c in self.checks], "steps": [vars(s) for s in self.steps]}


def plan_new(name: str, version: str, containers: list[dict], port: int | None = None, addons: str | None = None,
             home: str | None = None, listening: dict[int, int | None] | None = None,
             exists=os.path.lexists, isdir=os.path.isdir) -> NewPlan:
    """Checks and steps for a new stack; changes nothing. Without ``port`` the first free one from 18069 is chosen."""
    name = (name or "").strip()
    p = NewPlan(name=name, version=version, addons=addons or None)
    add = lambda cid, ok, good, bad, level=FAIL: p.checks.append(Check(cid, OK if ok else level, good if ok else bad))  # noqa: E731
    add("name", bool(_NAME.match(name)), f"stack {name}", "name: lowercase letters, digits, - and _, starting with a letter or digit, 31 characters at most")
    add("version", version in VERSIONS, f"Odoo {version}", f"version must be one of {', '.join(VERSIONS)}")
    if not _NAME.match(name) or version not in VERSIONS:
        return p
    p.folder, p.project = os.path.join(stacks_root(home), name), project_name(name)
    p.image = f"odoo:{version}"
    add("folder", not exists(p.folder), f"new folder {p.folder}", f"{p.folder} exists already: pick another name")
    taken = [c["name"] for c in containers if (c["compose"] or {}).get("project") == p.project]
    add("project", not taken, f"compose project {p.project} is free", f"compose project {p.project} has containers already: {', '.join(taken)}")
    busy = busy_ports(containers, listening)
    if port is None:
        port = next((x for x in range(FIRST_PORT, FIRST_PORT + 200) if x not in busy), None)
    p.port = port
    ok_port = isinstance(port, int) and 1024 <= port <= 65535
    add("port", ok_port and port not in busy, f"http://localhost:{port} (bound to this machine only)",
        f"port {port} is used by {busy.get(port)}" if ok_port and port in busy else "pick a port between 1024 and 65535")
    if p.addons:
        add("addons", p.addons.startswith("/") and isdir(p.addons), f"your addons folder {p.addons} is mounted",
            f"{p.addons} must be the absolute path of an existing folder")
    p.steps = [
        Step("files", 1, "dev", f"Write {p.folder}: compose.yaml, .env (database password, mode 0600), config/odoo.conf"
             + ("" if p.addons else ", an empty addons folder"), []),
        Step("pull", 2, "dev", f"Download the images {p.image} and {POSTGRES_IMAGE} (once; large on the first run)",
             [f"docker compose -p {p.project} pull"]),
        Step("up", 3, "dev", "Start the stack", [f"docker compose -p {p.project} up -d"]),
    ]
    return p


def write_files(p: NewPlan) -> None:
    os.makedirs(p.folder, mode=0o755)
    Path(p.folder, MARKER).write_text(json.dumps({"name": p.name, "version": p.version,
                                                   "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    fd = os.open(os.path.join(p.folder, ".env"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"PG_PASSWORD={secrets.token_urlsafe(18)}\n")
    # The container's user must read these: world-readable folder and config, no secret in the config.
    os.makedirs(os.path.join(p.folder, "config"), mode=0o755)
    Path(p.folder, "config", "odoo.conf").write_text(ODOO_CONF)
    os.chmod(os.path.join(p.folder, "config", "odoo.conf"), 0o644)
    if not p.addons:
        os.makedirs(os.path.join(p.folder, "addons"), mode=0o755)
    Path(p.folder, "compose.yaml").write_text(COMPOSE.format(
        project=p.project, image=p.image, postgres=POSTGRES_IMAGE, port=p.port, addons=p.addons or "./addons"))


async def _stream(argv: list[str], report: Report, step: str) -> int:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    async for line in proc.stdout:
        _emit(report, step, "output", line.decode(errors="replace").rstrip("\n"))
    return await proc.wait()


def compose_argv(folder: str, project: str, *args: str) -> list[str]:
    return ["docker", "compose", "-p", project, "--project-directory", folder, "-f", os.path.join(folder, "compose.yaml"), *args]


async def run_new(p: NewPlan, report: Report) -> dict:
    """Write the files, pull, start. A failure removes what this run made (the plan guarantees a new folder and an
    empty project)."""
    if not p.ok:
        raise DockerError("the plan has failed checks: " + "; ".join(c.detail for c in p.checks if c.status == FAIL))
    _emit(report, "files", "start", f"Write {p.folder}")
    try:
        write_files(p)
    except OSError as exc:
        shutil.rmtree(p.folder, ignore_errors=True)
        _emit(report, "files", "fail", str(exc))
        raise DockerError(f"could not write {p.folder}: {exc}") from exc
    _emit(report, "files", "ok")
    try:
        for step, args, title in (("pull", ["pull"], "Download the images"), ("up", ["up", "-d"], "Start the stack")):
            _emit(report, step, "start", title)
            code = await _stream(compose_argv(p.folder, p.project, *args), report, step)
            if code != 0:
                raise DockerError(f"{step}: docker compose ended with exit code {code}")
            _emit(report, step, "ok")
    except BaseException as exc:
        _emit(report, "rollback", "start", "Remove what this run made")
        await _stream(compose_argv(p.folder, p.project, "down", "-v", "--remove-orphans"), report, "rollback")
        shutil.rmtree(p.folder, ignore_errors=True)
        _emit(report, "rollback", "ok", f"{p.folder} removed")
        raise exc if isinstance(exc, DockerError) else DockerError(str(exc)) from exc
    return {"name": p.name, "folder": p.folder, "port": p.port, "container": f"{p.project}-web-1", "version": p.version}


# -- delete ------------------------------------------------------------------

@dataclass
class DeletePlan:
    container: str
    name: str = ""
    folder: str = ""
    project: str = ""
    confirmed: bool = False
    checks: list[Check] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        return {"kind": "delete", "container": self.container, "name": self.name, "folder": self.folder,
                "project": self.project, "confirmed": self.confirmed, "ok": self.ok,
                "checks": [vars(c) for c in self.checks], "steps": [vars(s) for s in self.steps]}


def stack_of(container: dict, home: str | None = None) -> tuple[str, str] | None:
    """(name, folder) when ``container`` belongs to a stack this app created, else None."""
    compose = container.get("compose")
    if not compose or not compose.get("working_dir"):
        return None
    folder = os.path.normpath(compose["working_dir"])
    if os.path.dirname(folder) != os.path.normpath(stacks_root(home)):
        return None
    if not os.path.isfile(os.path.join(folder, MARKER)) or compose["project"] != project_name(os.path.basename(folder)):
        return None
    return os.path.basename(folder), folder


def plan_delete(container_name: str, containers: list[dict], confirm: str | None = None, home: str | None = None) -> DeletePlan:
    c = find(containers, container_name)
    p = DeletePlan(container=c["name"])
    found = stack_of(c, home)
    if found is None:
        p.checks.append(Check("stack", FAIL, f"{c['name']} was not created by this app (no {MARKER} in a folder under "
                              f"{stacks_root(home)}): remove it yourself with docker"))
        return p
    p.name, p.folder = found
    p.project = project_name(p.name)
    p.checks.append(Check("stack", OK, f"stack {p.name} in {p.folder}"))
    p.checks.append(Check("data", WARN, "all databases and filestores of this stack are deleted for good: back up "
                          "what you need first (Databases panel, Backup)"))
    p.confirmed = confirm == p.name
    p.checks.append(Check("confirm", OK if p.confirmed else FAIL,
                          f"typed {p.name}" if p.confirmed else f"type the stack name ({p.name}) to confirm"))
    p.steps = [Step("down", 1, "dev", f"Stop and remove the containers and volumes of {p.project}",
                    [f"docker compose -p {p.project} down -v --remove-orphans"]),
               Step("folder", 2, "dev", f"Remove {p.folder}", [])]
    return p


async def run_delete(p: DeletePlan, report: Report) -> dict:
    if not p.ok:
        raise DockerError("the plan has failed checks: " + "; ".join(c.detail for c in p.checks if c.status == FAIL))
    if not os.path.isfile(os.path.join(p.folder, MARKER)):
        raise DockerError(f"{p.folder} is not a stack made by this app")
    _emit(report, "down", "start", f"Remove the containers and volumes of {p.project}")
    code = await _stream(compose_argv(p.folder, p.project, "down", "-v", "--remove-orphans"), report, "down")
    if code != 0:
        _emit(report, "down", "fail", f"exit code {code}")
        raise DockerError(f"docker compose down ended with exit code {code}: the folder is kept")
    _emit(report, "down", "ok")
    _emit(report, "folder", "start", f"Remove {p.folder}")
    shutil.rmtree(p.folder)
    _emit(report, "folder", "ok")
    return {"name": p.name, "folder": p.folder}
