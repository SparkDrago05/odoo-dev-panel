"""``external_dependencies: {"python": [...]}`` of the addon manifests under an installation.

Manifests are Python dict literals; they are read with ``ast.literal_eval``, never executed.
"""

from __future__ import annotations

import ast
import os

MAX_MANIFESTS = 4000
MAX_DEPTH = 7
SKIP_DIRS = {"node_modules", "__pycache__", "static", "i18n", "tests", "migrations", "site-packages"}
_cache: dict[str, tuple[float, list[str]]] = {}


def _python_deps(path: str) -> list[str]:
    try:
        stamp = os.stat(path).st_mtime
    except OSError:
        return []
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    deps: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            manifest = ast.literal_eval(fh.read().strip())
        listed = (manifest.get("external_dependencies") or {}).get("python") or []
        deps = [d for d in listed if isinstance(d, str)] if isinstance(listed, list) else []
    except (OSError, ValueError, SyntaxError, AttributeError, TypeError, MemoryError, RecursionError):
        deps = []
    _cache[path] = (stamp, deps)
    return deps


def external_python(root: str, skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """dependency string -> manifest paths that declare it."""
    root = os.path.normpath(root)
    base = root.count(os.sep)
    found: dict[str, list[str]] = {}
    seen = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        depth = current.count(os.sep) - base
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not d.startswith("venv") and d not in SKIP_DIRS
                         and os.path.join(current, d) not in skip and depth < MAX_DEPTH)
        if "__manifest__.py" in files:
            path = os.path.join(current, "__manifest__.py")
            seen += 1
            for dep in _python_deps(path):
                found.setdefault(dep, []).append(path)
            dirs[:] = []  # an addon holds no further addons
            if seen >= MAX_MANIFESTS:
                break
    return found
