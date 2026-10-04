"""Module dependency graph from ``__manifest__.py`` files.

A manifest is read with ``ast`` and ``literal_eval``: it is never imported, so no module code runs.
Odoo looks up a module in the addons_path in order and the first directory that has it wins; a module
with the same name further down is reported as ``shadowed``. Odoo always adds ``<source>/odoo/addons``
(where ``base`` lives) after the configured entries, so the caller passes it as the last path.

Pure filesystem in, plain data out.
"""

from __future__ import annotations

import ast
import os

MANIFEST = "__manifest__.py"
# States of a module that is, or is becoming, part of a database.
ACTIVE_STATES = ("installed", "to upgrade", "to install", "to remove")
_FIELDS = ("name", "version", "summary", "category", "application", "installable", "auto_install")


def read_manifest(path: str) -> dict | None:
    """The manifest dict at ``path``, or ``None`` when the file is unreadable or holds no dict literal."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        value = node.value if isinstance(node, ast.Expr) else None
        if isinstance(value, ast.Dict):
            try:
                data = ast.literal_eval(value)
            except (ValueError, TypeError, MemoryError, RecursionError):
                return None
            return data if isinstance(data, dict) else None
    return None


def _names(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, (list, tuple)) else []


def scan(addons_paths: list[str]) -> dict:
    """Modules of the addons_path entries, first entry first.

    ``{"modules": {name: {...}}, "shadowed": [{"name", "path", "by"}], "unreadable": [path]}``. Each module has
    ``path``, ``addons_path`` (the entry it was found in), ``depends`` and the manifest fields in ``_FIELDS``.
    """
    modules: dict[str, dict] = {}
    shadowed: list[dict] = []
    unreadable: list[str] = []
    for entry in addons_paths:
        try:
            names = sorted(os.listdir(entry))
        except OSError:
            continue
        for name in names:
            folder = os.path.join(entry, name)
            manifest_path = os.path.join(folder, MANIFEST)
            if not os.path.isfile(manifest_path):
                continue
            if name in modules:
                shadowed.append({"name": name, "path": folder, "by": modules[name]["path"]})
                continue
            manifest = read_manifest(manifest_path)
            if manifest is None:
                unreadable.append(manifest_path)
                continue
            info = {"path": folder, "addons_path": entry, "depends": _names(manifest.get("depends", [])),
                    "installable": True}
            info.update({k: manifest[k] for k in _FIELDS if k in manifest})
            if not isinstance(info["installable"], bool):
                info["installable"] = bool(info["installable"])
            modules[name] = info
    return {"modules": modules, "shadowed": shadowed, "unreadable": unreadable}


def graph(found: dict) -> dict:
    """Adds ``required_by`` to each module and lists dependencies that exist nowhere: ``missing``
    is ``{module: [dependency, ...]}``. ``found`` is the result of ``scan``."""
    modules = {n: dict(m) for n, m in found["modules"].items()}
    for m in modules.values():
        m["required_by"] = []
    missing: dict[str, list[str]] = {}
    for name, m in sorted(modules.items()):
        for dep in m["depends"]:
            if dep in modules:
                modules[dep]["required_by"].append(name)
            else:
                missing.setdefault(name, []).append(dep)
    return {"modules": modules, "missing": missing, "shadowed": found["shadowed"], "unreadable": found["unreadable"]}


def _walk(modules: dict[str, dict], start: str, key: str, depth: int | None) -> dict[str, int]:
    """Modules reachable from ``start`` through ``key`` (``depends`` or ``required_by``) with their distance."""
    seen = {start: 0}
    queue = [start]
    while queue:
        current = queue.pop(0)
        if depth is not None and seen[current] >= depth:
            continue
        for nxt in modules[current][key]:
            if nxt in modules and nxt not in seen:
                seen[nxt] = seen[current] + 1
                queue.append(nxt)
    seen.pop(start)
    return seen


def focus(full: dict, name: str, depth: int | None = None) -> dict:
    """One module with everything it needs (``needs``) and everything that needs it (``needed_by``, what breaks
    when it goes), each as ``{module: distance}``, down to ``depth`` levels (``None``: all). Dependencies
    that exist nowhere are listed as ``missing`` for the whole closure."""
    modules = full["modules"]
    if name not in modules:
        raise KeyError(name)
    needs = _walk(modules, name, "depends", depth)
    return {"name": name, "module": modules[name], "needs": needs,
            "needed_by": _walk(modules, name, "required_by", depth),
            "missing": {m: full["missing"][m] for m in (name, *needs) if m in full["missing"]}}


def cycles(full: dict) -> list[list[str]]:
    """Dependency cycles, each as a list of module names (Odoo refuses to load these)."""
    modules = full["modules"]
    state: dict[str, int] = {}
    found: list[list[str]] = []
    stack: list[str] = []

    def visit(n: str) -> None:
        state[n] = 1
        stack.append(n)
        for d in modules[n]["depends"]:
            if d not in modules:
                continue
            if state.get(d) == 1:
                found.append(stack[stack.index(d):] + [d])
            elif d not in state:
                visit(d)
        stack.pop()
        state[n] = 2

    for n in sorted(modules):
        if n not in state:
            visit(n)
    return found


def full_version(version: str | None, series: str | None) -> str | None:
    """The version Odoo stores: a manifest version that does not start with the series ("17.0.") gets it as prefix."""
    if not version or not series:
        return version
    return version if version.startswith(series + ".") else f"{series}.{version}"


def overlay(full: dict, states: dict[str, dict], series: str | None = None) -> dict:
    """Mark each module with its state in a database (``states``: module -> {state, version}).

    ``state`` is the database's state ("installed", "to upgrade", ...) or ``None`` for a module the database has
    never seen. ``version_differs`` flags an installed module whose manifest version is not the one stored.
    ``db_only`` lists modules the database holds as active that exist nowhere in the addons_path: Odoo logs
    these at every start and the modules cannot be updated.
    """
    for name, m in full["modules"].items():
        row = states.get(name)
        m["db_state"] = row["state"] if row else None
        m["db_version"] = row["version"] if row else None
        want = full_version(m.get("version"), series)
        m["version_differs"] = bool(row and row["state"] == "installed" and want and row["version"] and want != row["version"])
    full["db_only"] = sorted(n for n, r in states.items() if n not in full["modules"] and r["state"] in ACTIVE_STATES)
    full["db_counts"] = {}
    for m in full["modules"].values():
        key = m["db_state"] or "not in database"
        full["db_counts"][key] = full["db_counts"].get(key, 0) + 1
    return full


def for_config(path: str, snapshot: dict, name: str | None = None, depth: int | None = None,
               extra: list[str] | None = None) -> dict:
    """Scan the addons_path of the discovered config at ``path`` plus ``<source>/odoo/addons``, build the graph
    and, with ``name``, add ``focus``. ``extra`` are further absolute folders to look in, after the addons_path
    (modules on disk the config does not load). Raises ``ConfigError`` when the config does not parse,
    ``KeyError`` for an unknown module."""
    from . import configedit

    with open(path, encoding="utf-8") as fh:
        opts = configedit.parse(fh.read())
    entries = [e["path"] for e in configedit.addons_entries(opts.get("addons_path"), snapshot, path)]
    me = next((i for i in snapshot["instances"] if i["path"] == path), None)
    home = next((i for i in snapshot["installations"] if me and i["root"] == me.get("installation")), None)
    core_addons = os.path.join(home["source"], "odoo", "addons") if home and home.get("source") else None
    if core_addons and os.path.isdir(core_addons) and core_addons not in entries:
        entries.append(core_addons)
    for folder in extra or []:
        if folder not in entries:
            entries.append(folder)
    full = graph(scan(entries))
    full["addons_paths"] = entries
    full["series"] = home.get("version") if home else None
    full["installation"] = home["root"] if home else None
    full["extra"] = list(extra or [])
    full["cycles"] = cycles(full)
    if name is not None:
        full["focus"] = focus(full, name, depth)
    return full
