"""W1: find Git repositories that belong to Odoo installations. Bounded: only installation roots, their
addons paths, registered repositories and scan roots the developer configured. Never the whole disk."""

from __future__ import annotations

import os
from pathlib import Path

from ..discover.configs import split_addons_path

REPO_SKIP = {"venv", ".venv", "node_modules", "__pycache__", ".local", ".cache", "filestore", ".npm", "static",
             ".git", "site-packages"}
MAX_DEPTH = 4         # covers custom/<area>/<group>/<repo>
MAX_REPOS = 500       # per root; a larger tree is not an Odoo installation
UP_LEVELS = 4         # how far an addons_path entry may sit inside a repository


def find_repos(root: str, max_depth: int = 3, limit: int = MAX_REPOS) -> list[tuple[str, str | None]]:
    """(repository folder, gitdir of a worktree or None for a normal clone). Does not enter repositories
    and does not follow symbolic links."""
    result: list[tuple[str, str | None]] = []

    def walk(directory: str, depth: int) -> None:
        if len(result) >= limit:
            return
        found = _dot_git(directory)
        if found is not False:
            result.append((directory, found))
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            try:
                if entry.name not in REPO_SKIP and entry.is_dir(follow_symlinks=False):
                    walk(entry.path, depth + 1)
            except OSError:
                continue

    walk(root, 0)
    return result


def _dot_git(directory: str) -> str | None | bool:
    """None: a normal clone. A path ('' if unreadable): a worktree or submodule gitdir. False: not a repository."""
    dot_git = os.path.join(directory, ".git")
    if os.path.isdir(dot_git):
        return None
    if os.path.isfile(dot_git):
        try:
            text = Path(dot_git).read_text(errors="replace").strip()
        except OSError:
            return ""
        gitdir = text[7:].strip() if text.startswith("gitdir:") else ""
        if gitdir and not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(directory, gitdir))
        return gitdir
    return False


def enclosing_repo(path: str, levels: int = UP_LEVELS) -> tuple[str, str | None] | None:
    """The repository that contains ``path`` (itself included), looking at most ``levels`` folders up."""
    current = os.path.normpath(path)
    for _ in range(levels + 1):
        found = _dot_git(current)
        if found is not False:
            return current, found
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def classify(repo: str) -> str:
    """community, enterprise, themes, custom or other, from the files in the work tree."""
    if os.path.isfile(os.path.join(repo, "odoo-bin")) and os.path.isfile(os.path.join(repo, "odoo", "release.py")):
        return "community"
    if os.path.isfile(os.path.join(repo, "web_enterprise", "__manifest__.py")):
        return "enterprise"
    modules = _module_names(repo)
    if modules and sum(m.startswith("theme_") for m in modules) * 2 > len(modules):
        return "themes"
    return "custom" if modules else "other"


def _module_names(folder: str, depth: int = 2) -> list[str]:
    names: list[str] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return names
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith(".") or entry.name in REPO_SKIP:
                continue
        except OSError:
            continue
        if os.path.isfile(os.path.join(entry.path, "__manifest__.py")):
            names.append(entry.name)
        elif depth > 1:
            names += _module_names(entry.path, depth - 1)
    return names


def _addons_entries(snapshot: dict, root: str) -> list[str]:
    out: list[str] = []
    for inst in snapshot.get("instances", []):
        if inst.get("installation") == root:
            out += split_addons_path((inst.get("options") or {}).get("addons_path"))
    return out


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def repos_for_installation(inst: dict, snapshot: dict, max_depth: int = MAX_DEPTH) -> dict[str, dict]:
    """{real path: {path, gitdir, sources}} of the repositories one installation uses."""
    root, source = inst["root"], inst["source"]
    found: dict[str, dict] = {}

    def add(repo: str, gitdir: str | None, why: str) -> None:
        real = os.path.realpath(repo)
        entry = found.setdefault(real, {"path": repo, "gitdir": gitdir, "sources": []})
        if why not in entry["sources"]:
            entry["sources"].append(why)

    for repo, gitdir in find_repos(root, max_depth):
        add(repo, gitdir, "root")
    if not _inside(source, root):
        hit = enclosing_repo(source)
        if hit:
            add(*hit, "source")
    for entry in _addons_entries(snapshot, root):
        if not os.path.isdir(entry) or _inside(entry, root) and any(_inside(entry, f["path"]) for f in found.values()):
            continue
        hit = enclosing_repo(entry)
        if hit:
            add(*hit, "addons_path")
            continue
        for repo, gitdir in find_repos(entry, 2):  # a plain folder of repositories
            add(repo, gitdir, "addons_path")
    return found


def collect(snapshot: dict, registry: dict) -> list[dict]:
    """Every known repository once, with the installations that use it.

    Rows: {path, real, gitdir, purpose, installations: [{root, relative, sources, assoc}], registered}."""
    rows: dict[str, dict] = {}
    for inst in snapshot.get("installations", []):
        for real, hit in repos_for_installation(inst, snapshot).items():
            row = rows.setdefault(real, {"path": hit["path"], "real": real, "gitdir": hit["gitdir"], "installations": []})
            row["installations"].append({"root": inst["root"], "relative": _relative(real, inst["root"]),
                                         "sources": hit["sources"]})
    for scan_root in registry.get("scan_roots", []):
        for repo, gitdir in find_repos(scan_root, MAX_DEPTH):
            real = os.path.realpath(repo)
            rows.setdefault(real, {"path": repo, "real": real, "gitdir": gitdir, "installations": []})
    for real in registry.get("repos", {}):
        found = _dot_git(real)
        rows.setdefault(real, {"path": real, "real": real, "gitdir": found if found is not False else "",
                               "installations": [], "missing": found is False})
    for assoc in registry.get("assoc", []):
        row = rows.get(assoc["repo"])
        if row is None:
            continue
        link = next((i for i in row["installations"] if i["root"] == assoc["installation"]), None)
        if link is None:
            link = {"root": assoc["installation"], "relative": _relative(assoc["repo"], assoc["installation"]),
                    "sources": ["registry"]}
            row["installations"].append(link)
        link["assoc"] = {k: v for k, v in assoc.items() if k not in ("repo", "installation")}
    for row in rows.values():
        row["registered"] = row["real"] in registry.get("repos", {})
        row["purpose"] = "other" if row.get("missing") else classify(row["path"])
        for link in row["installations"]:
            link.setdefault("assoc", None)
            if link["assoc"] and link["assoc"].get("purpose"):
                row["purpose"] = link["assoc"]["purpose"]
    return sorted(rows.values(), key=lambda r: r["path"])


def _relative(path: str, root: str) -> str | None:
    root = os.path.realpath(root)
    return os.path.relpath(path, root) if _inside(path, root) else None
