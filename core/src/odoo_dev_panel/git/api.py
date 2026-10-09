"""One entry point per operation, shared by ``odp repo`` and the sidecar so both accept and refuse the same input."""

from __future__ import annotations

import os
from pathlib import Path

from . import discover, ops, registry, workspace


class ApiError(ValueError):
    """Bad input (INVALID_PARAMS)."""


class NotFound(LookupError):
    pass


def _installation(snapshot: dict, root) -> dict:
    if not isinstance(root, str) or not root:
        raise ApiError("installation is required")
    inst = next((i for i in snapshot["installations"] if i["root"] == os.path.normpath(root)), None)
    if inst is None:
        raise NotFound(f"{root} is not a discovered Odoo installation")
    return inst


def _repo(snapshot: dict, path, registry_file: Path | None) -> dict:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ApiError("repo must be an absolute path")
    try:
        return workspace.resolve(path, snapshot, registry_file)
    except LookupError as exc:
        raise NotFound(str(exc)) from exc


def _bulk(snapshot: dict, installation: str | None, registry_file: Path | None) -> list[dict]:
    """Repositories selected for bulk operations: associations with bulk on (default), optionally of one installation."""
    rows = discover.collect(snapshot, registry.load(registry_file))
    out = []
    for row in rows:
        links = [i for i in row["installations"] if installation is None or i["root"] == installation]
        if links and not row.get("missing") and all((i.get("assoc") or {}).get("bulk", True) for i in links):
            out.append(row)
    return out


def plan(op: str, params: dict, snapshot: dict, registry_file: Path | None = None) -> ops.Plan:
    if op not in ops.OPS:
        raise ApiError(f"op must be one of {', '.join(ops.OPS)}")
    try:
        if op in ("fetch", "pull"):
            if params.get("bulk"):
                inst = params.get("installation")
                if inst is not None:
                    _installation(snapshot, inst)
                rows = _bulk(snapshot, inst, registry_file)
            else:
                repos = params.get("repos")
                if not isinstance(repos, list) or not repos or len(repos) > 200:
                    raise ApiError("repos is a list of 1 to 200 repository paths")
                rows = [_repo(snapshot, p, registry_file) for p in dict.fromkeys(repos)]
            pairs = [(r["path"], r["gitdir"]) for r in rows]
            return ops.plan_fetch(pairs) if op == "fetch" else ops.plan_pull(pairs)
        if op == "switch":
            row = _repo(snapshot, params.get("repo"), registry_file)
            return ops.plan_switch(row["path"], row["gitdir"], params.get("branch"),
                                   fetch_branch=bool(params.get("fetch_branch")))
        if op == "checkout":
            row = _repo(snapshot, params.get("repo"), registry_file)
            return ops.plan_checkout(row["path"], row["gitdir"], params.get("ref"), params.get("confirm"))
        if op == "bundle":
            return _bundle(params, snapshot)
        inst = _installation(snapshot, params.get("installation"))
        fields = params.get("fields") or {}
        if not isinstance(fields, dict):
            raise ApiError("fields is an object")
        ref = params.get("ref") or None
        return ops.plan_clone(inst["root"], params.get("url"), params.get("destination"), ref,
                              params.get("shallow", True) is not False, fields)
    except (ops.OpError, registry.RegistryError) as exc:
        raise ApiError(str(exc)) from exc


def _bundle(params: dict, snapshot: dict) -> ops.Plan:
    """Repositories of a profile (and wizard overrides) applied to an existing installation of the same version."""
    from ..provision import profiles
    from ..provision.spec import SpecError, spec_from_dict

    inst = _installation(snapshot, params.get("installation"))
    try:
        version = int((inst.get("version") or "").split(".")[0])
    except ValueError as exc:
        raise ApiError(f"{inst['root']} has no known Odoo version") from exc
    try:
        resolved = profiles.resolve(params.get("profile") or None, version, params.get("overrides") or None)
    except profiles.ProfileError as exc:
        raise ApiError(str(exc)) from exc
    try:
        built = spec_from_dict(resolved["spec"]).custom
    except SpecError as exc:
        raise ApiError(str(exc)) from exc
    repos = [{"name": r.name, "url": r.url, "branch": r.branch, "destination": r.destination, "purpose": r.purpose,
              "group": r.group, "addons": r.addons, "shallow": r.shallow} for r in built]
    if not repos:
        raise ApiError("the profile lists no repositories")
    return ops.plan_bundle(inst["root"], repos)


def addons_proposal(params: dict, snapshot: dict) -> list[dict]:
    """T5: for each config of an installation, the addons_path entries the given repositories would add.
    Nothing is written; ``addons_apply`` writes one config after the developer ticks it."""
    from .. import configedit
    from ..discover.configs import split_addons_path

    inst = _installation(snapshot, params.get("installation"))
    repos = params.get("repos")
    if not isinstance(repos, list) or not all(isinstance(r, str) and r.startswith("/") for r in repos):
        raise ApiError("repos is a list of absolute repository paths")
    wanted = []
    for repo in repos:
        entry = ops.addons_entry(repo)
        if entry and entry not in wanted:
            wanted.append(entry)
    out = []
    for conf in sorted(c["path"] for c in snapshot.get("instances", []) if c.get("installation") == inst["root"]):
        try:
            text = open(conf, encoding="utf-8").read()
        except OSError as exc:
            out.append({"path": conf, "error": f"cannot read: {exc.strerror}", "add": [], "current": [], "sha": None})
            continue
        current = split_addons_path(configedit.parse(text).get("addons_path"))
        known = {os.path.realpath(e) for e in current}
        add = [e for e in wanted if os.path.realpath(e) not in known]
        out.append({"path": conf, "current": current, "add": add, "after": current + add, "sha": configedit.sha(text),
                    "writable": os.access(conf, os.W_OK), "error": None})
    return out


def addons_apply(params: dict, snapshot: dict) -> dict:
    """Append entries to one config's addons_path through the config editor's save (backup, changed-file check)."""
    from .. import configedit
    from ..discover.configs import split_addons_path

    path, add, base = params.get("path"), params.get("add"), params.get("sha")
    if not any(c["path"] == path for c in snapshot.get("instances", [])):
        raise NotFound(f"{path} is not a discovered Odoo config")
    if not isinstance(add, list) or not add or not all(isinstance(a, str) and os.path.isdir(a) for a in add):
        raise ApiError("add is a list of existing folders")
    if not isinstance(base, str):
        raise ApiError("sha of the config as it was shown is required")
    text = open(path, encoding="utf-8").read()
    current = split_addons_path(configedit.parse(text).get("addons_path"))
    value = ",".join(current + [a for a in add if a not in current])
    try:
        return configedit.save(path, configedit.set_options(text, {"addons_path": value}), base, snapshot)
    except configedit.ConfigError as exc:
        raise ApiError(str(exc)) from exc


def register_existing(params: dict, snapshot: dict, registry_file: Path | None = None) -> dict:
    """Add a repository that already exists on disk, optionally associated with an installation. No Git command runs."""
    path = params.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ApiError("path must be an absolute folder")
    if discover._dot_git(path) is False:
        raise ApiError(f"{path} is not a Git repository (no .git)")
    root = None
    if params.get("installation"):
        root = _installation(snapshot, params["installation"])["root"]
    fields = dict(params.get("fields") or {})
    if root and "destination" not in fields:
        real_root = os.path.realpath(root)
        real = os.path.realpath(path)
        if real.startswith(real_root + "/"):
            fields["destination"] = os.path.relpath(real, real_root)
    try:
        return registry.register(path, root, fields, registry_file)
    except registry.RegistryError as exc:
        raise ApiError(str(exc)) from exc


def forget(params: dict, registry_file: Path | None = None) -> bool:
    path = params.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ApiError("path must be an absolute folder")
    return registry.forget(path, params.get("installation") or None, registry_file)
