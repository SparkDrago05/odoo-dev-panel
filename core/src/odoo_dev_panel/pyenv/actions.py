"""Y5-Y8: package installs and import validation through the installation's venv as its run-as user, disk use.

Installs are ``uv pip install --python <venv>/bin/python ...`` in a session of the run-as agent (the venv is
owned by that user). The developer's own Python is never used for Odoo's packages. Every plan shows the exact
command; package names are validated, so no URL, path or pip option can slip in.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time

from .. import paths
from ..provision.spec import BUILD_CFLAGS, PIP_OVERRIDES
from . import env as env_mod

# name, optional extras, optional specifiers; nothing else (no URL, path, option or marker)
_PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}(\[[A-Za-z0-9._,-]{1,100}\])?"
                  r"(\s*(===|==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9.*+!_-]{1,50}(\s*,\s*(==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9.*+!_-]{1,50})*)?$")
MAX_PACKAGES = 100


class ActionError(ValueError):
    pass


def _check(cid: str, status: str, detail: str) -> dict:
    return {"id": cid, "status": status, "detail": detail}


def _base_checks(inst: dict, processes: list[dict], agent_running: bool | None) -> list[dict]:
    checks = []
    interp = env_mod.interpreter(inst)
    if interp["problem"]:
        checks.append(_check("venv", "fail", f"the venv cannot run: {interp['problem']}. Rebuild it first"))
    else:
        checks.append(_check("venv", "ok", f"{interp['python']} (Python {interp['version']})"))
    run_as = inst.get("owner")
    if not run_as or run_as == _me():
        checks.append(_check("run-as", "fail", f"no separate run-as user for {inst['root']}"))
    if agent_running is not None:
        checks.append(_check("agent", "ok" if agent_running else "fail",
                             f"agent of {run_as} is running" if agent_running else f"agent of {run_as} is not running: unlock it first"))
    running = [p for p in processes if p.get("installation") == inst["root"]]
    if running:
        checks.append(_check("running", "warn", "Odoo runs from this venv (pid " + ", ".join(str(p["pid"]) for p in running)
                             + "): restart it afterwards to load the change"))
    return checks


def _me() -> str:
    import pwd

    return pwd.getpwuid(os.getuid()).pw_name


def install_plan(inst: dict, processes: list[dict], packages=None, missing: bool = False,
                 agent_running: bool | None = None, repos: list[str] | None = None) -> dict:
    """Packages typed by the developer and/or the missing (and mismatched) requirements of the requirement files."""
    wanted: list[str] = []
    if packages:
        if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
            raise ActionError("packages is a list of package specifiers")
        for text in packages:
            text = text.strip()
            if not _PKG.match(text):
                raise ActionError(f"{text!r} is not a package specifier like name, name==1.2 or name[extra]>=2 "
                                  "(URLs, paths and pip options are not accepted)")
            wanted.append(text)
    if missing:
        described = env_mod.describe(inst, repos)
        for row in described["requirements"]:
            if row["status"] in ("missing", "mismatch"):
                spec = f"{row['name']}{row['spec']}" if row["spec"] else row["name"]
                if _PKG.match(spec) and spec not in wanted:
                    wanted.append(spec)
    if not wanted:
        raise ActionError("nothing to install: no package given and no missing requirement")
    if len(wanted) > MAX_PACKAGES:
        raise ActionError(f"at most {MAX_PACKAGES} packages at once")
    interp = env_mod.interpreter(inst)
    venv_python = f"{inst.get('venv')}/bin/python"
    argv = [paths.uv_command(), "pip", "install", "--python", venv_python]
    if PIP_OVERRIDES.get(interp["version"] or ""):
        override = f"{inst['root']}/.odp-pip-overrides.txt"
        if os.path.isfile(override):
            argv += ["--override", override]
    argv += wanted
    checks = _base_checks(inst, processes, agent_running)
    if not shutil.which(paths.uv_command()) and not os.path.isfile(paths.uv_command()):
        checks.append(_check("uv", "fail", f"{paths.uv_command()} is missing. Install the Odoo Dev Panel .deb"))
    session = {"argv": argv, "cwd": inst["root"], "name": f"pip install ({len(wanted)}) {os.path.basename(inst['root'])}",
               "meta": {"installation": inst["root"], "kind": "pip"}, "env": {"CFLAGS": BUILD_CFLAGS},
               "user": inst.get("owner")}
    return {"kind": "install", "root": inst["root"], "packages": wanted, "checks": checks,
            "ok": not any(c["status"] == "fail" for c in checks),
            "steps": [{"id": "pip", "phase": 1, "actor": inst.get("owner") or "?", "title": f"Install {len(wanted)} package(s) into {inst.get('venv')}",
                       "commands": [f"CFLAGS={shlex.quote(BUILD_CFLAGS)} {shlex.join(argv)}"]}],
            "session": session}


# Y7: import check. Run by the venv's Python as the run-as user. Reads top_level.txt of each required
# distribution (falls back to the distribution name) and imports it; prints one JSON object.
VALIDATE_SCRIPT = r"""
import importlib, json, re, sys
from importlib import metadata
source, names = sys.argv[1], sys.argv[2:]
out = {"python": sys.version.split()[0], "odoo": None, "modules": {}}
sys.path.insert(0, source)
try:
    import odoo
    out["odoo"] = {"ok": True, "version": getattr(getattr(odoo, "release", None), "version", None)}
except Exception as exc:
    out["odoo"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
SKIP = re.compile(r"(tests?$|^tests?_|^samples?$|^_)")

def tops_of(d, dist):
    tops = [t for t in (d.read_text("top_level.txt") or "").split() if t]
    if not tops:  # no top_level.txt: the packages and modules at the top of RECORD
        found = set()
        for f in d.files or []:
            parts = f.parts
            if not parts or parts[0] in ("..", "__pycache__") or parts[0].endswith((".dist-info", ".egg-info", ".data")):
                continue
            if len(parts) == 2 and parts[1] == "__init__.py":
                found.add(parts[0])
            elif len(parts) == 1 and parts[0].endswith(".py"):
                found.add(parts[0][:-3])
        tops = sorted(found)
    tops = [t for t in tops if not SKIP.search(t)]
    want = dist.replace("-", "_").lower()
    exact = [t for t in tops if t.lower() == want]
    return exact or tops[:5] or [dist.replace("-", "_")]

for dist in names:
    try:
        d = metadata.distribution(dist)
    except metadata.PackageNotFoundError:
        out["modules"][dist] = {"ok": False, "error": "not installed"}
        continue
    tops = tops_of(d, dist)
    errs = []
    for top in tops:
        try:
            importlib.import_module(top)
        except Exception as exc:
            errs.append(f"{top}: {type(exc).__name__}: {exc}")
    out["modules"][dist] = {"ok": not errs, "error": "; ".join(errs) or None, "imports": tops}
print("ODP-VALIDATE " + json.dumps(out))
"""


def validate_plan(inst: dict, processes: list[dict], agent_running: bool | None = None,
                  repos: list[str] | None = None) -> dict:
    described = env_mod.describe(inst, repos)
    names = sorted({r["installed_as"] or r["name"] for r in described["requirements"] if r["status"] in ("ok", "mismatch")})
    venv_python = f"{inst.get('venv')}/bin/python"
    argv = [venv_python, "-c", VALIDATE_SCRIPT, inst["source"], *names]
    checks = [c for c in _base_checks(inst, processes, agent_running) if c["id"] != "running"]
    session = {"argv": argv, "cwd": inst["root"], "name": f"import check {os.path.basename(inst['root'])}",
               "meta": {"installation": inst["root"], "kind": "validate"}, "env": {}, "user": inst.get("owner")}
    return {"kind": "validate", "root": inst["root"], "packages": names, "checks": checks,
            "ok": not any(c["status"] == "fail" for c in checks),
            "steps": [{"id": "validate", "phase": 1, "actor": inst.get("owner") or "?",
                       "title": f"Import odoo and the top-level modules of {len(names)} required package(s)",
                       "commands": [f"{venv_python} -c <import check script> {shlex.quote(inst['source'])} {' '.join(names)}"]}],
            "session": session}


def parse_validate(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        if line.startswith("ODP-VALIDATE "):
            try:
                return json.loads(line[len("ODP-VALIDATE "):])
            except ValueError:
                return None
    return None


async def run(plan: dict, conn, report) -> dict:
    from ..module_center.actions import run_session

    if not plan["ok"]:
        raise ActionError("; ".join(c["detail"] for c in plan["checks"] if c["status"] == "fail"))
    report({"step": plan["kind"], "status": "start", "text": plan["steps"][0]["title"]})
    code, session_id, text = await run_session(conn, plan["session"], report, plan["kind"])
    result = {"kind": plan["kind"], "exit_code": code, "session": session_id, "packages": plan["packages"]}
    if plan["kind"] == "validate":
        found = parse_validate(text)
        result["validation"] = found
        failed = [n for n, r in ((found or {}).get("modules") or {}).items() if not r["ok"]]
        result["failed"] = failed
        ok = found is not None and found["odoo"] and found["odoo"]["ok"] and not failed
    else:
        ok = code == 0
    report({"step": plan["kind"], "status": "ok" if ok else "fail", "text": "" if ok else f"exit code {code}"})
    result["ok"] = bool(ok)
    return result


# -- Y8: disk use ----------------------------------------------------------------

def _size(path: str, deadline: float) -> tuple[int, bool]:
    """(bytes, complete). Stops at the deadline; symbolic links are not followed."""
    total, stack = 0, [path]
    while stack:
        if time.monotonic() > deadline:
            return total, False
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_blocks * 512
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def disk(inst: dict, budget: float = 20.0) -> dict:
    root = inst["root"]
    parts = [("venv", inst.get("venv")), ("odoo source", inst.get("source")),
             ("enterprise", os.path.join(root, "enterprise")), ("custom", os.path.join(root, "custom"))]
    deadline = time.monotonic() + budget
    rows = []
    for label, path in parts:
        if not path or not os.path.isdir(path):
            continue
        size, complete = _size(path, deadline)
        rows.append({"label": label, "path": path, "bytes": size, "complete": complete})
    usage = shutil.disk_usage(root)
    return {"root": root, "parts": rows, "free": usage.free, "total": usage.total}
