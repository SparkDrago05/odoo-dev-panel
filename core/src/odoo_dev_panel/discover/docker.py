"""Odoo containers from the Docker CLI. Read-only: ``docker ps`` and ``docker inspect``, nothing else.

Environment values whose name looks secret are never kept. The Docker daemon is a separate call from the scan, so a
slow daemon cannot slow the rest of discovery.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Callable

TIMEOUT = 10
SECRET = re.compile(r"PASSWORD|SECRET|KEY|TOKEN", re.I)
OFFICIAL_RC = "/etc/odoo/odoo.conf"
DATA_DIR = "/var/lib/odoo"
_TAG_VERSION = re.compile(r"^(\d{2})(?:\.0)?(?:$|[-.])")
_ODOO_CMD = re.compile(r"(^|/)(odoo|odoo-bin)$")
_ANONYMOUS = re.compile(r"[0-9a-f]{64}")  # a volume the image declares (VOLUME) and nobody named

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)


def _env(row: dict) -> dict[str, str]:
    """Environment of the container without secret values (the name stays, the value is dropped)."""
    out = {}
    for item in (row.get("Config") or {}).get("Env") or []:
        name, _, value = item.partition("=")
        if not SECRET.search(name):
            out[name] = value
    return out


def _repo(image: str) -> str:
    """``myorg/odoo-custom:17.0`` -> ``odoo-custom``. A registry host with a port keeps its colon out of the tag."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return last.split(":", 1)[0]


def _tag(image: str) -> str | None:
    last = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def _command(row: dict) -> list[str]:
    config = row.get("Config") or {}
    return [*(config.get("Entrypoint") or []), *(config.get("Cmd") or [])]


def is_odoo(row: dict) -> bool:
    config = row.get("Config") or {}
    image = config.get("Image") or row.get("Image") or ""
    if "odoo" in _repo(image).lower():
        return True
    if {"ODOO_VERSION", "ODOO_RC"} & set(_env(row)):
        return True
    return any(_ODOO_CMD.search(part) for part in _command(row))


def _version(row: dict) -> str | None:
    env = _env(row)
    if env.get("ODOO_VERSION"):
        return env["ODOO_VERSION"]
    tag = _tag((row.get("Config") or {}).get("Image") or "")
    match = _TAG_VERSION.match(tag or "")
    return f"{match.group(1)}.0" if match else None


def _config_path(row: dict) -> str | None:
    cmd = _command(row)
    for i, part in enumerate(cmd):
        if part in ("-c", "--config") and i + 1 < len(cmd):
            return cmd[i + 1]
        if part.startswith("--config="):
            return part.split("=", 1)[1]
    env = _env(row)
    if env.get("ODOO_RC"):
        return env["ODOO_RC"]
    return OFFICIAL_RC if "ODOO_VERSION" in env else None


def _mounts(row: dict) -> list[dict]:
    return [{"type": m.get("Type"), "source": m.get("Source") or None, "name": m.get("Name") or None,
             "destination": m.get("Destination"), "rw": bool(m.get("RW", True))}
            for m in row.get("Mounts") or [] if m.get("Destination")]


def host_path(path: str | None, mounts: list[dict]) -> dict | None:
    """Where ``path`` (inside the container) is on the host: through the deepest mount that covers it.
    A bind mount gives a host path; a volume only its name (its folder is root only)."""
    if not path:
        return None
    best = None
    for m in mounts:
        dest = m["destination"].rstrip("/") or "/"
        if path == dest or path.startswith(dest + "/") or dest == "/":
            if best is None or len(dest) > len(best["destination"].rstrip("/")):
                best = m
    if best is None:
        return {"container": path, "host": None, "volume": None, "anonymous": False, "in_image": True}
    rest = os.path.relpath(path, best["destination"]) if path != best["destination"] else ""
    rest = "" if rest == "." else rest
    if best["type"] == "bind" and best["source"]:
        return {"container": path, "host": os.path.join(best["source"], rest) if rest else best["source"],
                "volume": None, "anonymous": False, "in_image": False}
    anonymous = bool(_ANONYMOUS.fullmatch(best["name"] or ""))
    return {"container": path, "host": None, "volume": best["name"][:12] if anonymous else best["name"],
            "anonymous": anonymous, "in_image": False}


def _ports(row: dict) -> list[dict]:
    out: dict[tuple[int, str], dict] = {}
    for key, bindings in ((row.get("NetworkSettings") or {}).get("Ports") or {}).items():
        for b in bindings or []:
            if b.get("HostPort", "").isdigit():
                # Docker lists the same port once for 0.0.0.0 and once for ::. One entry per host port is enough.
                out.setdefault((int(b["HostPort"]), key), {"container": key, "host_ip": b.get("HostIp") or None,
                                                          "host_port": int(b["HostPort"])})
    return [out[k] for k in sorted(out)]


def _compose(row: dict) -> dict | None:
    labels = (row.get("Config") or {}).get("Labels") or {}
    project = labels.get("com.docker.compose.project")
    if not project:
        return None
    files = labels.get("com.docker.compose.project.config_files")
    return {"project": project, "service": labels.get("com.docker.compose.service"),
            "working_dir": labels.get("com.docker.compose.project.working_dir"),
            "files": files.split(",") if files else []}


def _state(row: dict) -> dict:
    state = row.get("State") or {}
    return {"status": state.get("Status"), "running": bool(state.get("Running")), "started_at": state.get("StartedAt"),
            "exit_code": state.get("ExitCode"), "oom_killed": bool(state.get("OOMKilled"))}


def odoo_program(row: dict) -> str:
    """The odoo executable the container starts, as the official entrypoint expects it as first argument."""
    for part in _command(row):
        if _ODOO_CMD.search(part):
            return part
    return "odoo"


def entrypoint(row: dict) -> list[str]:
    """The image entrypoint. Odoo run inside the container must go through it: the official one turns the HOST,
    USER and PASSWORD variables into database options."""
    return [p for p in (row.get("Config") or {}).get("Entrypoint") or [] if isinstance(p, str)]


def _name(row: dict) -> str:
    return (row.get("Name") or "").lstrip("/") or (row.get("Id") or "")[:12]


def _is_postgres(row: dict) -> bool:
    return "postgres" in _repo((row.get("Config") or {}).get("Image") or "").lower()


def odoo_containers(rows: list[dict]) -> list[dict]:
    """Odoo containers from ``docker inspect`` output, with their compose project, mounts mapped to the host,
    published ports and database container. Pure."""
    out = []
    for row in rows:
        if not is_odoo(row) or _is_postgres(row):
            continue
        env, mounts, compose = _env(row), _mounts(row), _compose(row)
        db_host = env.get("HOST") or None
        db_container, pick = None, None
        if compose:
            peers = [r for r in rows if (_compose(r) or {}).get("project") == compose["project"] and _is_postgres(r)]
            named = [r for r in peers if db_host in ((_compose(r) or {}).get("service"), _name(r))]
            pick = (named or peers or [None])[0]
            db_container = _name(pick) if pick else None
        config = _config_path(row)
        out.append({
            "id": (row.get("Id") or "")[:12],
            "name": _name(row),
            "image_id": row.get("Image"),
            "user": (row.get("Config") or {}).get("User") or None,
            "entrypoint": entrypoint(row),
            "program": odoo_program(row),
            "restart": ((row.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or None,
            "image": (row.get("Config") or {}).get("Image") or row.get("Image"),
            "version": _version(row),
            **_state(row),
            "compose": compose,
            "ports": _ports(row),
            "config": host_path(config, mounts),
            "addons": [host_path(m["destination"], mounts) for m in mounts if "addons" in m["destination"]],
            "data": host_path(DATA_DIR, mounts) if any(m["destination"].startswith(DATA_DIR) for m in mounts) else None,
            "db": {"host": db_host, "port": env.get("PORT") or None, "user": env.get("USER") or None,
                   "container": db_container, "running": bool(((pick or {}).get("State") or {}).get("Running"))
                   if db_container else None},
            "mounts": mounts,
        })
    return sorted(out, key=lambda c: ((c["compose"] or {}).get("project") or "", c["name"]))


def _access_error(stderr: str) -> str:
    text = (stderr or "").strip().splitlines()
    last = text[-1] if text else "docker failed"
    if "permission denied" in last.lower():
        return ("no access to the Docker daemon. Members of the docker group have it (root-equivalent): "
                "sudo usermod -aG docker $USER, then log out and back in")
    if "cannot connect" in last.lower() or "is the docker daemon running" in last.lower():
        return "the Docker daemon is not running"
    return last


def discover_docker(run: Runner = _run, which: Callable[[str], str | None] = shutil.which) -> dict:
    """``{"containers": [...], "error": str | None, "available": bool}``. Never raises for daemon trouble."""
    if not which("docker"):
        return {"containers": [], "error": "docker is not installed", "available": False}
    try:
        ps = run(["docker", "ps", "-a", "-q", "--no-trunc"])
        if ps.returncode != 0:
            return {"containers": [], "error": _access_error(ps.stderr), "available": False}
        ids = ps.stdout.split()
        if not ids:
            return {"containers": [], "error": None, "available": True}
        inspect = run(["docker", "inspect", *ids])
    except subprocess.TimeoutExpired:
        return {"containers": [], "error": f"the Docker daemon did not answer within {TIMEOUT} s", "available": False}
    except OSError as exc:
        return {"containers": [], "error": str(exc), "available": False}
    # A container removed between ps and inspect makes inspect fail for that id only; the rest is still printed.
    try:
        rows = json.loads(inspect.stdout or "[]")
    except ValueError:
        return {"containers": [], "error": _access_error(inspect.stderr), "available": True}
    return {"containers": odoo_containers(rows), "error": None, "available": True}
