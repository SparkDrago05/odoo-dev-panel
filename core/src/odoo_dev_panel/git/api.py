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
        inst = _installation(snapshot, params.get("installation"))
        fields = params.get("fields") or {}
        if not isinstance(fields, dict):
            raise ApiError("fields is an object")
        ref = params.get("ref") or None
        return ops.plan_clone(inst["root"], params.get("url"), params.get("destination"), ref,
                              params.get("shallow", True) is not False, fields)
    except (ops.OpError, registry.RegistryError) as exc:
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
