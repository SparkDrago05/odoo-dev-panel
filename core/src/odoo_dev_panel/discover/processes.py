"""D7/D8: running Odoo processes, linked to an Instance by ``-c`` and to an Installation by path."""

from __future__ import annotations

import os
import pwd
import re
from dataclasses import dataclass

from .model import Installation, Instance

DEFAULT_PORT = 8069
_ODOO_BIN = re.compile(r"(^|/)odoo-bin$|(^|/)odoo$|(^|/)openerp-server$")


@dataclass
class OdooProcess:
    pid: int
    user: str | None
    argv: list[str]
    config: str | None = None
    database: str | None = None
    port: int | None = None
    port_source: str | None = None  # "cmdline", "config" or "default"
    instance: str | None = None
    installation: str | None = None
    link: str | None = None  # "config", "argv", "cwd", "exe"
    starttime: int | None = None
    ppid: int | None = None
    unit: str | None = None


def _flag(argv: list[str], short: str | None, long: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == long or (short and arg == short):
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith(long + "="):
            return arg.split("=", 1)[1]
        if short and arg.startswith(short) and len(arg) > 2 and not arg.startswith("--") and short == "-c":
            return arg[2:]
    return None


def is_odoo_argv(argv: list[str]) -> bool:
    """True for ``python .../odoo-bin ...``, ``odoo-bin ...``, ``python -m odoo`` and openerp-server."""
    if not argv:
        return False
    for i, arg in enumerate(argv[:4]):
        if _ODOO_BIN.search(arg):
            return i == 0 or os.path.basename(argv[0]).startswith(("python", "pypy"))
        if arg == "-m" and i + 1 < len(argv) and argv[i + 1] in ("odoo", "openerp"):
            return True
    return False


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _stat_fields(data: bytes) -> list[bytes]:
    return data[data.rfind(b")") + 2 :].split()


def list_processes(proc_root: str = "/proc") -> list[OdooProcess]:
    result = []
    try:
        names = [n for n in os.listdir(proc_root) if n.isdigit()]
    except OSError:
        return result
    for name in names:
        raw = _read(f"{proc_root}/{name}/cmdline")
        if not raw:
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if not is_odoo_argv(argv):
            continue
        proc = OdooProcess(pid=int(name), user=None, argv=argv)
        try:
            proc.user = pwd.getpwuid(os.stat(f"{proc_root}/{name}").st_uid).pw_name
        except (OSError, KeyError):
            pass
        stat = _read(f"{proc_root}/{name}/stat")
        if stat:
            fields = _stat_fields(stat)
            try:
                proc.ppid, proc.starttime = int(fields[1]), int(fields[19])
            except (IndexError, ValueError):
                pass
        result.append(proc)
    # An Odoo with workers has child processes with the same command line. Keep the parent only.
    pids = {p.pid for p in result}
    return sorted((p for p in result if p.ppid not in pids), key=lambda p: p.pid)


def _cwd_of(proc: OdooProcess, proc_root: str) -> str | None:
    try:
        return os.readlink(f"{proc_root}/{proc.pid}/cwd")
    except OSError:
        return None


def link_process(
    proc: OdooProcess, instances: list[Instance], installs: list[Installation], proc_root: str = "/proc"
) -> None:
    proc.config = _flag(proc.argv, "-c", "--config")
    proc.database = _flag(proc.argv, "-d", "--database")
    cwd = _cwd_of(proc, proc_root)
    if proc.config and not os.path.isabs(proc.config) and cwd:
        proc.config = os.path.normpath(os.path.join(cwd, proc.config))
    by_conf = {os.path.realpath(i.path): i for i in instances}
    instance = by_conf.get(os.path.realpath(proc.config)) if proc.config else None
    if instance:
        proc.instance, proc.installation, proc.link = instance.path, instance.installation, "config"
    if proc.installation is None:
        proc.installation, proc.link = _link_by_path(proc, installs, cwd, proc_root)
    cli_port = _flag(proc.argv, "-p", "--http-port") or _flag(proc.argv, None, "--xmlrpc-port")
    if cli_port and cli_port.isdigit():
        proc.port, proc.port_source = int(cli_port), "cmdline"
    else:
        conf_port = (instance.options.get("http_port") or instance.options.get("xmlrpc_port")) if instance else None
        if conf_port and conf_port.isdigit():
            proc.port, proc.port_source = int(conf_port), "config"
        else:
            proc.port, proc.port_source = DEFAULT_PORT, "default"


def _link_by_path(proc, installs, cwd, proc_root) -> tuple[str | None, str | None]:
    def match(path: str | None) -> str | None:
        if not path:
            return None
        path = os.path.normpath(path)
        for inst in sorted(installs, key=lambda i: -len(i.root)):
            if path == inst.root or path.startswith(inst.root.rstrip("/") + "/"):
                return inst.root
        return None

    for arg in proc.argv[:3]:
        if arg.startswith("/") and (hit := match(arg)):
            return hit, "argv"
    if cwd and (hit := match(cwd)):
        return hit, "cwd"
    try:
        exe = os.readlink(f"{proc_root}/{proc.pid}/exe")
    except OSError:
        exe = None
    if exe and (hit := match(exe)):
        return hit, "exe"
    return None, None


def discover_processes(
    instances: list[Instance], installs: list[Installation], proc_root: str = "/proc"
) -> list[OdooProcess]:
    procs = list_processes(proc_root)
    for proc in procs:
        link_process(proc, instances, installs, proc_root)
    return procs
