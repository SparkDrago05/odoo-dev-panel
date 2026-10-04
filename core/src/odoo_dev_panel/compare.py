"""Compare two discovered Odoo configs: Odoo version and commit, Python, venv packages, addons_path, options.

Read-only. Everything comes from the filesystem and the discovery snapshot (``options`` in an instance never
holds secrets). The venv is read the way the doctor reads it, with no import. A part that cannot be read
shows as ``None`` with a note in ``notes``, and the diff skips it.
"""

from __future__ import annotations

import os

from .configedit import split_addons_path
from .discover import venv as venv_mod

# Options that differ between any two instances on purpose; shown, but marked so the panel can dim them.
EXPECTED_TO_DIFFER = frozenset({"http_port", "xmlrpc_port", "gevent_port", "longpolling_port", "db_name", "db_user",
                                "logfile", "pidfile", "data_dir", "dbfilter"})


def git_commit(source: str) -> str | None:
    """Short commit of the checkout at ``source``, from ``.git`` files (no git process, no safe.directory trouble)."""
    git = os.path.join(source, ".git")
    try:
        if os.path.isfile(git):  # worktree or submodule: "gitdir: <path>"
            with open(git) as fh:
                target = fh.read().strip().removeprefix("gitdir:").strip()
            git = target if os.path.isabs(target) else os.path.join(source, target)
        with open(os.path.join(git, "HEAD")) as fh:
            head = fh.read().strip()
        if not head.startswith("ref:"):
            return head[:12] or None
        ref = head.removeprefix("ref:").strip()
        try:
            with open(os.path.join(git, ref)) as fh:
                return fh.read().strip()[:12] or None
        except OSError:
            with open(os.path.join(git, "packed-refs")) as fh:
                for line in fh:
                    if line.strip().endswith(" " + ref):
                        return line.split()[0][:12]
    except OSError:
        pass
    return None


def packages(venv: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for version in venv_mod.inspect(venv).site_versions:
        found.update(venv_mod.installed(venv_mod.site_packages(venv, version)))
    return found


def describe(path: str, snapshot: dict) -> dict:
    """What is compared about one config. Raises ``KeyError`` when ``path`` is not a discovered config."""
    inst = next((i for i in snapshot["instances"] if i["path"] == path), None)
    if inst is None:
        raise KeyError(path)
    install = next((i for i in snapshot["installations"] if i["root"] == inst.get("installation")), None)
    notes: list[str] = []
    out = {"path": path, "name": inst["name"], "installation": install["root"] if install else None,
           "version": install.get("version") if install else None, "commit": None, "python": None,
           "venv_ok": None, "packages": None, "options": dict(inst.get("options") or {}),
           "addons_path": split_addons_path(inst.get("options", {}).get("addons_path")), "notes": notes}
    if install is None:
        notes.append("Orphan config: no installation, so no version, commit or packages.")
        return out
    out["commit"] = git_commit(install["source"])
    out["python"] = install.get("python_version") or install.get("venv_built_for")
    out["venv_ok"] = install.get("venv_ok")
    if install.get("venv"):
        pkgs = packages(install["venv"])
        if pkgs:
            out["packages"] = pkgs
        else:
            notes.append(f"No packages readable in {install['venv']}.")
    else:
        notes.append("No venv.")
    return out


def _diff_map(a: dict[str, str], b: dict[str, str]) -> dict:
    return {"only_a": {k: a[k] for k in sorted(a.keys() - b.keys())},
            "only_b": {k: b[k] for k in sorted(b.keys() - a.keys())},
            "changed": {k: [a[k], b[k]] for k in sorted(a.keys() & b.keys()) if a[k] != b[k]}}


def diff(a: dict, b: dict) -> dict:
    """``facts`` (one row each, ``same`` flag), ``packages`` and ``options`` as only_a / only_b / changed,
    ``addons`` entries only on one side. A side whose packages are ``None`` gives ``packages: None``."""
    facts = [{"key": k, "a": a[k], "b": b[k], "same": a[k] == b[k]} for k in ("version", "commit", "python", "venv_ok")]
    pkgs = _diff_map(a["packages"], b["packages"]) if a["packages"] is not None and b["packages"] is not None else None
    opts = _diff_map({k: v for k, v in a["options"].items() if k != "addons_path"},
                     {k: v for k, v in b["options"].items() if k != "addons_path"})
    addons = {"only_a": [e for e in a["addons_path"] if e not in b["addons_path"]],
              "only_b": [e for e in b["addons_path"] if e not in a["addons_path"]]}
    return {"a": a, "b": b, "facts": facts, "packages": pkgs, "options": opts, "addons": addons,
            "expected": sorted(EXPECTED_TO_DIFFER)}


def compare(path_a: str, path_b: str, snapshot: dict) -> dict:
    return diff(describe(path_a, snapshot), describe(path_b, snapshot))


def describe_installation(root: str, snapshot: dict) -> dict:
    """The same facts for an installation as a whole: no config, so no options and no addons_path."""
    install = next((i for i in snapshot["installations"] if i["root"] == root), None)
    if install is None:
        raise KeyError(root)
    notes: list[str] = []
    out = {"path": root, "name": install.get("name") or root, "installation": root, "version": install.get("version"),
           "commit": git_commit(install["source"]) if install.get("source") else None,
           "python": install.get("python_version") or install.get("venv_built_for"), "venv_ok": install.get("venv_ok"),
           "packages": None, "options": {}, "addons_path": [], "notes": notes}
    if install.get("venv"):
        pkgs = packages(install["venv"])
        if pkgs:
            out["packages"] = pkgs
        else:
            notes.append(f"No packages readable in {install['venv']}.")
    else:
        notes.append("No venv.")
    return out


def compare_installations(root_a: str, root_b: str, snapshot: dict) -> dict:
    return diff(describe_installation(root_a, snapshot), describe_installation(root_b, snapshot))


def diff_modules(a: dict[str, dict], b: dict[str, dict]) -> dict:
    """Modules that are part of one database and not of the other, or differ in state or version. Inputs are
    ``dbquery.installed_modules`` results; modules that are only known, never installed, are left out."""
    from .modules import ACTIVE_STATES

    def active(m: dict[str, dict]) -> dict[str, str]:
        return {n: f"{r['state']} {r['version']}".strip() for n, r in m.items() if r["state"] in ACTIVE_STATES}

    return _diff_map(active(a), active(b))
