"""T6: export an existing installation as a sanitized profile.

What goes in: the Odoo version, the Python when it differs from the default, the root and run-as user when they
differ from the defaults, the community/enterprise source when not the defaults, every repository under the root
that has a remote (URL with any user info removed, current branch, folder, purpose, group, addons role) and the
safe config options of one config (spec.CONFIG_OPTIONS). Never: passwords, database settings, ports, paths of
data or logs, database names, client data. Every left-out item is listed in the notes.
"""

from __future__ import annotations

import os
from datetime import date

from ..discover.configs import split_addons_path
from .spec import CONFIG_OPTIONS, DEFAULT_ODOO_GIT, PYTHON_BY_VERSION, SUPPORTED_VERSIONS


class ExportError(ValueError):
    pass


def _remote(state: dict | None) -> str | None:
    remotes = (state or {}).get("remotes") or {}
    url = remotes.get("origin") or next(iter(remotes.values()), None)
    return None if url is None or "***" in url else url


def _config_text(path: str) -> dict[str, str]:
    from .. import configedit

    try:
        with open(path, encoding="utf-8") as fh:
            return configedit.parse(fh.read())
    except (OSError, configedit.ConfigError):
        return {}


def export_installation(root, snapshot: dict, listing=None) -> tuple[dict, list[str]]:
    from ..git import workspace

    if not isinstance(root, str) or not root:
        raise ExportError("root is required")
    inst = next((i for i in snapshot["installations"] if i["root"] == os.path.normpath(root)), None)
    if inst is None:
        raise ExportError(f"{root} is not a discovered Odoo installation")
    try:
        version = int((inst.get("version") or "").split(".")[0])
    except ValueError:
        version = None
    if version not in SUPPORTED_VERSIONS:
        raise ExportError(f"Odoo {inst.get('version')} cannot be provisioned, so it cannot be exported as a profile")
    notes: list[str] = []
    install: dict = {}
    python = inst.get("venv_built_for") or inst.get("python_version")
    if python and python != PYTHON_BY_VERSION[version]:
        install["python"] = python
    if inst["root"] != f"/opt/odoo{version}":
        install["root"] = inst["root"]
    if inst.get("owner") and inst["owner"] != f"odoo{version}":
        install["run_as"] = inst["owner"]

    configs = sorted(c["path"] for c in snapshot.get("instances", []) if c.get("installation") == inst["root"])
    entries = {os.path.realpath(e) for c in configs for e in split_addons_path(_config_text(c).get("addons_path"))}
    repos = []
    rows = (listing or workspace.listing)(snapshot, None, inst["root"])["repos"]
    for row in rows:
        link = next((i for i in row["installations"] if i["root"] == inst["root"]), None)
        state = row.get("state") or {}
        if link is None or link.get("relative") is None:
            notes.append(f"{row['path']}: outside the root, left out")
            continue
        url = _remote(state)
        branch = state.get("branch")
        if row["purpose"] in ("community", "enterprise"):
            key = "odoo" if row["purpose"] == "community" else "enterprise"
            if url and not (key == "odoo" and url == DEFAULT_ODOO_GIT):
                install[f"{key}_git"] = url
            if branch and branch != f"{version}.0":
                install[f"{key}_branch"] = branch
            if not url and key == "enterprise":
                notes.append("enterprise has no remote (archive install?): give the archive when you provision")
            continue
        if not url:
            notes.append(f"{link['relative']}: no remote (or a URL with credentials), left out")
            continue
        assoc = link.get("assoc") or {}
        repo = {"name": os.path.basename(row["path"]), "url": url, "destination": link["relative"]}
        if branch:
            repo["branch"] = branch
        else:
            notes.append(f"{link['relative']}: detached HEAD, exported without a branch (the default branch is cloned)")
        if row["purpose"] in ("themes", "other"):
            repo["purpose"] = row["purpose"]
        if assoc.get("group"):
            repo["group"] = assoc["group"]
        real = os.path.realpath(row["path"])
        on_path = real in entries or os.path.dirname(real) in entries and os.path.isfile(os.path.join(real, "__manifest__.py"))
        if not on_path:
            repo["addons"] = False
        if state.get("shallow") is False:
            repo["shallow"] = False
        repos.append(repo)
    if not (inst.get("source", "").startswith(inst["root"])):
        notes.append(f"the Odoo source {inst.get('source')} is outside the root; the profile clones it into odoo/")

    config: dict = {}
    if configs:
        opts = _config_text(configs[0])
        config = {k: opts[k] for k in CONFIG_OPTIONS if opts.get(k) not in (None, "")}
        notes.append(f"config options taken from {configs[0]} (safe options only; passwords, database and ports are never exported)")
    data = {"name": f"{os.path.basename(inst['root'].rstrip('/'))} setup",
            "description": f"Exported from {inst['root']} on {date.today().isoformat()}",
            "odoo_version": version}
    if install:
        data["install"] = install
    if config:
        data["config"] = config
    if repos:
        data["repos"] = repos
    return data, notes
