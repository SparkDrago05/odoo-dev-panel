"""Phase 3 Run: turn a discovered Instance into the command line of one Odoo process."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..discover.model import Installation, Instance
from ..discover.ports import listening_ports
from ..rpc import INVALID_PARAMS, RpcError

DEFAULT_PORT = 8069
PORT_SCAN_LIMIT = 200
DEV_FLAGS = ("all", "reload", "qweb", "werkzeug", "xml", "pdb")
_MODULE = re.compile(r"^[A-Za-z0-9_]+$")
_DBNAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


@dataclass
class RunSpec:
    """What the developer asked for. Everything except ``kind`` is optional."""

    db: str | None = None
    http_port: int | None = None
    update: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)
    stop_after_init: bool = False
    dev: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    shell: bool = False  # ``odoo-bin shell``: interactive, needs a PTY and a database

    @classmethod
    def from_params(cls, params: dict | None) -> "RunSpec":
        params = params or {}
        spec = cls(
            db=params.get("db") or None,
            http_port=params.get("http_port"),
            update=_names(params.get("update"), "update"),
            install=_names(params.get("install"), "install"),
            stop_after_init=bool(params.get("stop_after_init")),
            dev=_names(params.get("dev"), "dev", pattern=None),
            extra=list(params.get("extra") or []),
            shell=bool(params.get("shell")),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.db is not None and not _DBNAME.match(self.db):
            raise RpcError(INVALID_PARAMS, f"bad database name: {self.db}")
        if self.http_port is not None and not (isinstance(self.http_port, int) and 1 <= self.http_port <= 65535):
            raise RpcError(INVALID_PARAMS, f"bad http_port: {self.http_port}")
        for flag in self.dev:
            if flag not in DEV_FLAGS:
                raise RpcError(INVALID_PARAMS, f"unknown --dev flag: {flag}")
        if not all(isinstance(a, str) for a in self.extra):
            raise RpcError(INVALID_PARAMS, "extra must be a list of strings")
        if (self.update or self.install) and not self.db:
            raise RpcError(INVALID_PARAMS, "-u and -i need a database")
        if self.shell and not self.db:
            raise RpcError(INVALID_PARAMS, "the Odoo shell needs a database")
        if self.shell and (self.update or self.install or self.stop_after_init):
            raise RpcError(INVALID_PARAMS, "the Odoo shell takes no -u, -i or --stop-after-init")

    @property
    def kind(self) -> str:
        if self.shell:
            return "shell"
        if self.update:
            return "upgrade"
        if self.install:
            return "install"
        return "serve"


def _names(value, label: str, pattern=_MODULE) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [v for v in value.split(",") if v]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RpcError(INVALID_PARAMS, f"{label} must be a list of strings")
    if pattern is not None:
        for v in value:
            if not pattern.match(v):
                raise RpcError(INVALID_PARAMS, f"bad {label} name: {v}")
    return value


def conf_port(instance: Instance) -> int | None:
    raw = instance.options.get("http_port") or instance.options.get("xmlrpc_port")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def pick_port(instance: Instance, requested: int | None = None, busy: set[int] | None = None) -> int:
    """Return ``requested`` if given, else the config port if free, else the next free port."""
    if requested is not None:
        return requested
    taken = set(listening_ports()) if busy is None else busy
    start = conf_port(instance) or DEFAULT_PORT
    for port in range(start, start + PORT_SCAN_LIMIT):
        if port not in taken:
            return port
    raise RpcError(INVALID_PARAMS, f"no free port from {start}")


def build_argv(installation: Installation, instance: Instance, spec: RunSpec, port: int | None = None) -> list[str]:
    """Command line for one run. The config file is never modified; the port is a flag."""
    if not installation.venv_python:
        raise RpcError(INVALID_PARAMS, f"{installation.root} has no venv")
    odoo_bin = os.path.join(installation.source, "odoo-bin")
    argv = [installation.venv_python, odoo_bin]
    if spec.shell:
        # The plain Python console is line based and works with TERM=dumb; IPython is not assumed.
        argv += ["shell", "--shell-interface=python"]
    argv += ["-c", instance.path]
    if spec.db:
        argv += ["-d", spec.db]
    if port is not None:
        argv += ["--http-port", str(port)]
    if spec.update:
        argv += ["-u", ",".join(spec.update)]
    if spec.install:
        argv += ["-i", ",".join(spec.install)]
    if spec.stop_after_init:
        argv.append("--stop-after-init")
    if spec.dev:
        argv.append("--dev=" + ",".join(spec.dev))
    argv += spec.extra
    return argv


def resolve(snapshot: dict, instance_ref: str) -> tuple[Installation, Instance]:
    """Find an instance by config path or by unique name in a discovery snapshot."""
    matches = [i for i in snapshot["instances"] if instance_ref in (i["path"], i["name"])]
    if not matches:
        raise RpcError(INVALID_PARAMS, f"no discovered instance {instance_ref}")
    if len(matches) > 1:
        paths_ = ", ".join(m["path"] for m in matches)
        raise RpcError(INVALID_PARAMS, f"{instance_ref} is ambiguous; use the config path: {paths_}")
    inst = matches[0]
    if not inst["installation"]:
        raise RpcError(INVALID_PARAMS, f"{inst['path']} is not linked to an installation")
    install = next((i for i in snapshot["installations"] if i["root"] == inst["installation"]), None)
    if install is None:
        raise RpcError(INVALID_PARAMS, f"installation {inst['installation']} not found")
    known = {f for f in Installation.__dataclass_fields__}
    inst_known = {f for f in Instance.__dataclass_fields__}
    return (
        Installation(**{k: v for k, v in install.items() if k in known}),
        Instance(**{k: v for k, v in inst.items() if k in inst_known}),
    )


def plan(snapshot: dict, instance_ref: str, params: dict | None, busy: set[int] | None = None) -> dict:
    """Everything ``session.start`` needs: run-as user, argv, cwd, label and metadata."""
    installation, instance = resolve(snapshot, instance_ref)
    if not installation.owner:
        raise RpcError(INVALID_PARAMS, f"{installation.root} has no run-as user")
    spec = RunSpec.from_params(params)
    port = None  # the shell and one-shot runs start no HTTP server unless a port is asked for
    if not spec.shell and not spec.stop_after_init:
        port = pick_port(instance, spec.http_port, busy)
    elif spec.http_port is not None:
        port = spec.http_port
    argv = build_argv(installation, instance, spec, port)
    return {
        "user": installation.owner,
        "argv": argv,
        "cwd": installation.root,
        "name": f"{instance.name} {spec.kind}" + (f" {spec.db}" if spec.db else ""),
        "pty": spec.shell,
        "meta": {"instance": instance.path, "installation": installation.root, "db": spec.db,
                 "kind": spec.kind, "port": port, "version": installation.version},
    }
