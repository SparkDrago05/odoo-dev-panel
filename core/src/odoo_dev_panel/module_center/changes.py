"""M3: changed modules from uncommitted work (staged, modified, untracked, deleted files against HEAD).

Each changed file is mapped to the nearest folder with a ``__manifest__.py`` (on disk, or in HEAD for a
removed module). Per module: what kinds of files changed, whether it is new or removed, and guidance. A module
whose direct dependency changed is listed as "dependency changed". All of it is a heuristic, marked as such.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

from ..git import runner, state

MAX_FILES = 5000
MANIFEST = "__manifest__.py"

KINDS = ("manifest", "python", "data", "assets", "i18n", "tests", "other")


def kind_of(rel_in_module: str) -> str:
    parts = PurePosixPath(rel_in_module).parts
    name = parts[-1]
    if rel_in_module == MANIFEST:
        return "manifest"
    if parts[0] == "tests":
        return "tests"
    if parts[0] == "static":
        return "assets"
    if parts[0] == "i18n" or name.endswith((".po", ".pot")):
        return "i18n"
    if name.endswith(".py"):
        return "python"
    if name.endswith((".xml", ".csv", ".yml", ".sql")):
        return "data"
    return "other"


def status_files(repo: str, run=runner.run) -> tuple[list[tuple[str, str]], str | None]:
    """[(two-letter status, path)] of uncommitted work, untracked files listed one by one. (files, error)."""
    foreign = state.owner_of(repo)[1]
    res = run(runner.argv(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", foreign=foreign), timeout=20)
    if not res.ok:
        return [], res.stderr.strip() or f"git status failed ({res.code})"
    out: list[tuple[str, str]] = []
    records = iter(res.stdout.split("\0"))
    for rec in records:
        if len(rec) < 4:
            continue
        code, path = rec[:2], rec[3:]
        if code[0] in "RC":
            next(records, None)  # the original path of a rename
        out.append((code, path))
        if len(out) >= MAX_FILES:
            break
    return out, None


def _module_root(repo: str, rel: str, deleted: bool) -> str | None:
    """Repository-relative folder of the module that holds ``rel`` ('.' when the repository is one module)."""
    parts = PurePosixPath(rel).parts
    for depth in range(len(parts) - 1, -1, -1):
        folder = os.path.join(repo, *parts[:depth])
        if os.path.isfile(os.path.join(folder, MANIFEST)):
            return "/".join(parts[:depth]) or "."
        if deleted and depth == len(parts) - 1 and parts[-1] == MANIFEST:
            return "/".join(parts[:depth]) or "."
    return None


def guidance(entry: dict) -> tuple[str, str]:
    """(action, why). Actions: install, uninstall-first, upgrade, restart, reload, none, review."""
    kinds = set(entry["kinds"])
    if entry["new"]:
        return "install", "a new module: install it (-i) where it is needed"
    if entry["removed"]:
        return "uninstall-first", "the module folder lost its manifest: uninstall it from databases before removing it, or restore it"
    if kinds & {"manifest", "data"}:
        return "upgrade", "manifest or data files (XML/CSV) changed: they load on upgrade (-u)"
    if "i18n" in kinds:
        return "upgrade", "translation files changed: they load on upgrade (-u)"
    if "python" in kinds:
        return "review", "Python changed: a restart loads code; upgrade only if fields or models changed (check the diff)"
    if "assets" in kinds:
        return "reload", "static assets changed: reload the browser (with --dev=all they rebuild)"
    if kinds == {"tests"}:
        return "none", "only tests changed: run the tests, no upgrade needed"
    return "none", "no file Odoo loads changed"


def changed_modules(repos: list[str], graph: dict | None = None, run=runner.run) -> dict:
    """{modules: {name: entry}, errors: {repo: text}}. ``graph`` (modules.for_config output) adds dependents and
    tells which changed folders are known modules."""
    by_path = {}
    if graph:
        by_path = {os.path.realpath(info["path"]): name for name, info in graph["modules"].items()}
    modules: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for repo in repos:
        files, error = status_files(repo, run)
        if error:
            errors[repo] = error
            continue
        for code, rel in files:
            deleted = "D" in code
            root = _module_root(repo, rel, deleted)
            if root is None:
                continue
            folder = os.path.normpath(os.path.join(repo, root))
            name = by_path.get(os.path.realpath(folder)) or os.path.basename(folder)
            entry = modules.setdefault(name, {
                "name": name, "path": folder, "repo": repo, "files": [], "kinds": [], "new": False, "removed": False,
                "dependency_changed": [],
            })
            inner = os.path.relpath(os.path.join(repo, rel), folder)
            kind = kind_of(inner)
            entry["files"].append({"path": inner, "status": code.strip() or code, "kind": kind})
            if kind not in entry["kinds"]:
                entry["kinds"].append(kind)
            if inner == MANIFEST and code in ("??", "A ", "AM"):
                entry["new"] = True
            if inner == MANIFEST and deleted:
                entry["removed"] = True
    for entry in modules.values():
        entry["kinds"].sort(key=KINDS.index)
        entry["action"], entry["why"] = guidance(entry)
    if graph:
        for name in list(modules):
            for dependent in graph["modules"].get(name, {}).get("required_by", []):
                if dependent in modules:
                    continue
                info = graph["modules"][dependent]
                modules[dependent] = {
                    "name": dependent, "path": info["path"], "repo": info.get("repo"), "files": [], "kinds": [],
                    "new": False, "removed": False, "dependency_changed": [name], "action": "review",
                    "why": f"depends on changed {name}: upgrade only if it relies on what changed",
                }
            for dependent in graph["modules"].get(name, {}).get("required_by", []):
                dep = modules.get(dependent)
                if dep is not None and name not in dep["dependency_changed"] and dep["files"]:
                    dep["dependency_changed"].append(name)
    return {"modules": modules, "errors": errors, "heuristic": True}
