"""D1/D2: find Odoo source trees on any layout and read their version."""

from __future__ import annotations

import ast
import os
import pwd
import re
from pathlib import Path

from . import venv as venv_mod
from .model import Installation

# Directories that are searched for Odoo trees. Depth is small so a scan stays fast.
DEFAULT_ROOTS = ("/opt", "/srv", "/usr/local/src", "~")
MAX_DEPTH = 3
SKIP_NAMES = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".cache", ".local", ".config", ".npm",
    ".cargo", ".rustup", "snap", "addons", "custom", "enterprise", "filestore", "Downloads",
}
_VERSION_RE = re.compile(r"^version_info\s*=\s*(\(.*\))\s*$", re.M)


def read_version(source: Path) -> str | None:
    """Parse ``odoo/release.py`` without importing it. Returns "MAJOR.MINOR" or None."""
    try:
        text = (source / "odoo" / "release.py").read_text(errors="replace")
    except OSError:
        return None
    match = _VERSION_RE.search(text)
    if not match:
        return None
    try:
        # release.py writes FINAL etc. as names; blank them so literal_eval accepts the tuple.
        raw = re.sub(r"\b[A-Z]{3,}\b", "0", match.group(1))
        parts = ast.literal_eval(raw)
        return f"{int(parts[0])}.{int(parts[1])}"
    except (ValueError, SyntaxError, IndexError, TypeError):
        return None


def is_source_tree(path: Path) -> bool:
    try:
        return (path / "odoo-bin").is_file() and (path / "odoo" / "release.py").is_file()
    except OSError:  # a directory we may not enter
        return False


def find_source_trees(
    roots: list[Path], max_depth: int = MAX_DEPTH, unreadable: list[str] | None = None
) -> list[Path]:
    """``unreadable`` collects directories that could not be listed, so the UI can say what it could not see."""
    found: list[Path] = []
    seen: set[Path] = set()

    def walk(directory: Path, depth: int) -> None:
        try:
            real = directory.resolve()
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        if is_source_tree(directory):
            found.append(directory)
            return  # do not look inside an Odoo tree
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except PermissionError:
            if unreadable is not None and depth > 0:
                unreadable.append(str(directory))
            return
        except OSError:
            return
        for entry in entries:
            if entry.name in SKIP_NAMES:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), depth + 1)
            except OSError:
                continue

    for root in roots:
        if root.is_dir():
            walk(root, 0)
    return found


def _owner(path: Path) -> str | None:
    try:
        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (OSError, KeyError):
        return None


def _venv_for(root: Path, source: Path) -> Path | None:
    for candidate in (root / "venv", root / ".venv", source / "venv", source / ".venv"):
        if (candidate / "pyvenv.cfg").is_file():
            return candidate
    return None


def build_installation(source: Path) -> Installation:
    # Nested layout: <root>/odoo is the clone and <root>/venv sits beside it.
    # Plain clone: the clone itself is the root.
    parent = source.parent
    parent_venv = any((parent / n / "pyvenv.cfg").is_file() for n in ("venv", ".venv"))
    nested = parent != Path("/") and (parent_venv or (source.name == "odoo" and (parent / "custom").is_dir()))
    root = parent if nested else source
    venv = _venv_for(root, source)
    # The venv owner is the user that runs Odoo; the root may be owned by the developer.
    owner = _owner(venv) if venv is not None else _owner(root)
    inst = Installation(root=str(root), source=str(source), version=read_version(source), owner=owner)
    if venv is not None:
        info = venv_mod.inspect(venv)
        inst.venv = str(venv)
        inst.venv_python, inst.venv_ok, inst.venv_problem = info.python, info.problem is None, info.problem
        inst.python_version, inst.venv_built_for = info.python_version, info.built_for
    try:
        inst.home = pwd.getpwnam(owner).pw_dir if owner else None
    except KeyError:
        inst.home = None
    return inst


def scan_installations(
    roots: list[Path] | None = None, max_depth: int = MAX_DEPTH, unreadable: list[str] | None = None
) -> list[Installation]:
    if roots is None:
        roots = [Path(os.path.expanduser(r)) for r in DEFAULT_ROOTS]
    result, seen = [], set()
    for source in find_source_trees(roots, max_depth, unreadable):
        inst = build_installation(source)
        if inst.root in seen:
            continue
        seen.add(inst.root)
        result.append(inst)
    return result
