"""Read a virtual environment from the filesystem: which Python it runs and which packages it holds.

Nothing here imports from the venv. A broken venv (the main doctor case) cannot be asked, so every
fact comes from symlinks, ``pyvenv.cfg`` and the ``site-packages`` folders.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_PY_NAME = re.compile(r"python(\d)\.(\d{1,2})$")
_DIST = re.compile(r"^(?P<name>.+?)-(?P<version>[^-]+?)\.(?:dist-info|egg-info)$")


@dataclass
class VenvInfo:
    path: str
    python: str | None = None  # <venv>/bin/python, if the link exists
    target: str | None = None  # the interpreter it resolves to
    python_version: str | None = None  # "3.14": version of the interpreter that runs today
    built_for: str | None = None  # "3.12": version written in pyvenv.cfg when the venv was created
    site_versions: list[str] = field(default_factory=list)  # lib/pythonX.Y folders
    system_python: bool = False  # interpreter lives under /usr: a distribution upgrade can replace it
    problem: str | None = None  # why the venv cannot run Odoo, or None


def normalize(name: str) -> str:
    """PEP 503 name: lower case, runs of -_. become one dash."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_of(path: str) -> str | None:
    match = _PY_NAME.search(os.path.basename(path))
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    try:  # a plain "python3" binary: ask it. Isolated mode, no site: nothing of the venv is loaded.
        out = subprocess.run(
            [path, "-I", "-S", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and re.fullmatch(r"\d\.\d{1,2}", value) else None


def _built_for(venv: Path) -> str | None:
    try:
        text = (venv / "pyvenv.cfg").read_text(errors="replace")
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    raw = values.get("version_info") or values.get("version") or ""
    match = re.match(r"(\d)\.(\d{1,2})", raw)
    return f"{match.group(1)}.{match.group(2)}" if match else None


def inspect(venv: str | Path) -> VenvInfo:
    venv = Path(venv)
    info = VenvInfo(path=str(venv), built_for=_built_for(venv))
    try:
        info.site_versions = sorted(
            m.group(0)[6:] for m in (re.fullmatch(r"python\d\.\d{1,2}", p.name) for p in (venv / "lib").iterdir()) if m
        )
    except OSError:
        pass
    py = venv / "bin" / "python"
    if not os.path.lexists(py):
        info.problem = "bin/python is missing"
        return info
    info.python = str(py)
    try:
        target = os.path.realpath(py)
    except OSError:
        info.problem = "bin/python cannot be resolved"
        return info
    info.target = target
    if not (os.path.isfile(target) and os.access(target, os.X_OK)):
        info.problem = f"interpreter {target} is missing"
        return info
    info.system_python = target.startswith("/usr/")
    info.python_version = _version_of(target)
    now, built = info.python_version, info.built_for
    if now and info.site_versions and now not in info.site_versions:
        held = ", ".join(info.site_versions)
        info.problem = f"bin/python is now Python {now}, but the installed packages are for Python {held}"
    elif now and built and now != built:
        info.problem = f"bin/python is now Python {now}, but the venv was built with Python {built}"
    return info


def site_packages(venv: str | Path, version: str) -> Path:
    return Path(venv) / "lib" / f"python{version}" / "site-packages"


def installed(site: str | Path) -> dict[str, str]:
    """Normalized distribution name -> version, from *.dist-info and *.egg-info folders."""
    result: dict[str, str] = {}
    try:
        names = os.listdir(site)
    except OSError:
        return result
    for name in names:
        match = _DIST.match(name)
        if match:
            result[normalize(match.group("name"))] = match.group("version")
    return result


def top_levels(site: str | Path) -> set[str]:
    """Importable top-level names of the installed distributions (top_level.txt), as they are spelled."""
    result: set[str] = set()
    try:
        names = os.listdir(site)
    except OSError:
        return result
    for name in names:
        if not name.endswith((".dist-info", ".egg-info")):
            continue
        try:
            with open(os.path.join(site, name, "top_level.txt"), encoding="utf-8", errors="replace") as fh:
                result.update(t.strip() for t in fh.read().split() if t.strip())
        except OSError:
            continue
    return result
