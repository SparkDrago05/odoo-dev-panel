"""M1/M2: the module inventory of one config: every module on its addons_path plus the modules in the
installation's repositories that the config does not load, with repository, manifest problems and changes."""

from __future__ import annotations

import os

from .. import modules
from ..git import ops as git_ops
from ..git import workspace
from . import changes, checks


class CenterError(ValueError):
    pass


def _config(snapshot: dict, path) -> dict:
    if not isinstance(path, str) or not path:
        raise CenterError("config is required")
    inst = next((i for i in snapshot.get("instances", []) if i["path"] == path), None)
    if inst is None:
        raise CenterError(f"{path} is not a discovered Odoo config")
    if not inst.get("installation"):
        raise CenterError(f"{path} is not linked to an installation")
    return inst


def repo_paths(snapshot: dict, root: str, listing=workspace.listing) -> list[str]:
    rows = listing(snapshot, None, root, False)["repos"]
    return [r["path"] for r in rows if not r.get("missing")]


def inventory(path: str, snapshot: dict, with_changes: bool = True, listing=workspace.listing, run=None) -> dict:
    inst = _config(snapshot, path)
    root = inst["installation"]
    repos = repo_paths(snapshot, root, listing)
    base = modules.for_config(path, snapshot)
    loaded = {os.path.realpath(e) for e in base["addons_paths"]}
    extra = []
    for repo in repos:
        entry = git_ops.addons_entry(repo)
        if entry and os.path.realpath(entry) not in loaded and entry not in extra:
            extra.append(entry)
    graph = modules.for_config(path, snapshot, extra=extra) if extra else base
    extra_real = {os.path.realpath(e) for e in extra}
    own = set(checks.own_modules(graph, snapshot))
    problems = checks.check_graph(graph, snapshot, sorted(own))
    for name, info in graph["modules"].items():
        info["loadable"] = os.path.realpath(info["addons_path"]) not in extra_real
        info["own"] = name in own
        info["problems"] = problems.get(name, [])
        if not info["loadable"]:
            info["problems"] = info["problems"] + [{"code": "not-loaded", "level": "info",
                                                    "text": f"{info['addons_path']} is not on this config's addons_path"}]
    changed = {"modules": {}, "errors": {}, "heuristic": True}
    if with_changes:
        changed = changes.changed_modules(repos, graph, **({"run": run} if run else {}))
        for name, entry in changed["modules"].items():
            if name in graph["modules"]:
                graph["modules"][name]["change"] = entry
    graph["changed"] = changed
    graph["repos"] = repos
    graph["config"] = path
    graph["instance"] = inst["name"]
    return graph
