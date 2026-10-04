"""H1-H11, H13: read-only checks over a discovery snapshot. Each check returns Findings; none changes anything.

Every finding says what is wrong, why it matters (``why``), and what to do: commands to copy (``commands``),
or ``repair`` when the app can fix it itself ("venv", "config-perms").
"""

from __future__ import annotations

import os
import pwd
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import paths
from ..discover import venv as venv_mod
from ..discover.configs import parse_config
from ..provision.spec import PYTHON_BY_VERSION
from . import requirements

ERROR, WARNING, INFO = "error", "warning", "info"

# Why each kind of finding matters. Shown with every finding; the unit tests check each code has one.
WHY = {
    "venv-broken": "Odoo cannot start: the venv's interpreter is gone or is another Python than its packages "
                   "were installed for. This happens when a venv points to /usr/bin/python3 and a distribution "
                   "upgrade moves that link to a new Python.",
    "venv-python-unsupported": "This Odoo version does not support the venv's Python. It may fail to start or "
                               "break in places that are hard to trace.",
    "venv-system-python": "The venv runs the distribution's Python. The next Ubuntu upgrade can replace that "
                          "Python and break the venv (the cause of the broken venvs on the reference machine). "
                          "A rebuild pins a Python managed by uv that upgrades do not touch.",
    "venv-packages-missing": "Odoo or an addon imports these packages. Without them a server, an upgrade or a "
                             "module install stops with ModuleNotFoundError.",
    "addons-path-missing": "Odoo refuses to start when an addons_path entry does not exist "
                           "(\"option addons_path: no such directory\").",
    "config-orphan": "No installation on this machine can run this config. It is left over or belongs to a "
                     "version that was removed.",
    "addons-path-cross-version": "Addons are tied to one Odoo version. Loading another version's addons fails "
                                 "at install or upgrade time, or silently uses the wrong code.",
    "config-folder-version": "The folder name says one Odoo version and the addons_path another. Someone "
                             "following the folder name runs the config with the wrong installation.",
    "git-dead-worktree": "This folder is a git worktree of a repository that no longer exists. The files are "
                         "still there, but every git command in it fails, so it cannot be updated.",
    "git-dubious-ownership": "git refuses to work in a repository owned by another user (\"detected dubious "
                             "ownership\"). Status, pull and branch switching fail for you here.",
    "port-conflict": "Two processes want the same port. The second Odoo cannot bind it, or the browser reaches "
                     "the wrong instance.",
    "unit-failed": "A systemd service for Odoo failed. If it is enabled it fails again at every boot.",
    "config-secrets-exposed": "These configs hold db_password or admin_passwd and every local user can read "
                              "them. Anyone on the machine can log in to PostgreSQL as the Odoo role or manage "
                              "databases through the database manager.",
    "config-permissions": "Configs should be owned by you (so you can edit them without sudo), readable by the "
                          "run-as user's group (so Odoo can read them) and by nobody else (they hold passwords). "
                          "Their folder should give new configs the run-as group.",
    "filestore-missing": "Odoo keeps attachments, images and assets in the filestore of the OS user that runs "
                         "it. Without it, images and attachments are missing and asset bundles may fail.",
}


@dataclass
class Finding:
    check: str  # "H1" .. "H11"
    code: str  # key of WHY
    severity: str  # error | warning | info
    subject: str  # the path, unit or port the finding is about
    title: str
    detail: str = ""
    commands: list[str] = field(default_factory=list)  # suggested; never run by the doctor
    repair: str | None = None  # "venv" (H12) or "config-perms" (H13): the app can repair it
    installation: str | None = None

    @property
    def why(self) -> str:
        return WHY[self.code]


@dataclass
class Context:
    """What the checks read besides the snapshot. Tests replace the callables."""

    snapshot: dict
    dev_user: str = field(default_factory=lambda: pwd.getpwuid(os.getuid()).pw_name)
    uid: int = field(default_factory=os.getuid)
    git: Callable[[str], str] = None  # path -> stderr of a git command in it  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.git is None:
            self.git = git_stderr


def git_stderr(path: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return proc.stderr if proc.returncode else ""


def q(value: str) -> str:
    return shlex.quote(value)


def uv_as(user: str, args: str) -> str:
    return f"sudo -u {q(user)} {q(paths.uv_command())} {args}"


# -- H1 / H2 ----------------------------------------------------------------

_PY_RANGE = re.compile(r"^(MIN|MAX)_PY_VERSION\s*=\s*\((\d+),\s*(\d+)", re.M)
# Odoo 14 has no MIN/MAX constants; its setup.py asks for >= 3.6 and it is not tested after 3.10.
FALLBACK_RANGE = {"14.0": ((3, 6), (3, 10))}


def python_range(source: str, version: str | None) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """(min, max) Python declared by the Odoo source itself, else a fallback per version."""
    found: dict[str, tuple[int, int]] = {}
    for rel in ("odoo/release.py", "odoo/__init__.py"):
        try:
            text = Path(source, rel).read_text(errors="replace")
        except OSError:
            continue
        for kind, major, minor in _PY_RANGE.findall(text):
            found.setdefault(kind, (int(major), int(minor)))
    if found:
        return found.get("MIN"), found.get("MAX")
    return FALLBACK_RANGE.get(version or "", (None, None))


def requirement_files(inst: dict) -> list[str]:
    """odoo/requirements.txt plus the requirements.txt of custom repositories, two folders deep."""
    files = [os.path.join(inst["source"], "requirements.txt")]
    custom = Path(inst["root"], "custom")
    for pattern in ("*/requirements.txt", "*/*/requirements.txt"):
        try:
            files += sorted(str(p) for p in custom.glob(pattern))
        except OSError:
            pass
    return [f for f in files if os.path.isfile(f)]


def check_venv(ctx: Context) -> list[Finding]:
    out = []
    for inst in ctx.snapshot["installations"]:
        venv, root, user = inst.get("venv"), inst["root"], inst.get("owner") or "?"
        if not venv:
            continue
        info = venv_mod.inspect(venv)
        major = (inst.get("version") or "").split(".")[0]
        repair = "venv" if major.isdigit() and int(major) in PYTHON_BY_VERSION else None  # Odoo 14: doctor only
        if info.problem:
            out.append(Finding("H1", "venv-broken", ERROR, venv, f"Broken venv in {root}", info.problem,
                               repair=repair, installation=root))
            continue
        low, high = python_range(inst["source"], inst.get("version"))
        now = tuple(int(p) for p in info.python_version.split(".")) if info.python_version else None
        if now and ((low and now < low) or (high and now > high)):
            span = f"{'.'.join(map(str, low)) if low else '?'} to {'.'.join(map(str, high)) if high else '?'}"
            out.append(Finding("H1", "venv-python-unsupported", ERROR, venv,
                               f"Odoo {inst.get('version')} with Python {info.python_version} in {root}",
                               f"Odoo {inst.get('version')} declares Python {span}.", repair=repair, installation=root))
        elif info.system_python:
            out.append(Finding("H1", "venv-system-python", WARNING, venv,
                               f"venv of {root} runs the system Python {info.python_version or ''}".rstrip(),
                               f"bin/python resolves to {info.target}.", repair=repair, installation=root))
        if info.python_version:
            out += _missing_packages(inst, info, user, repair)
    return out


def _missing_packages(inst: dict, info: venv_mod.VenvInfo, user: str, repair: str | None) -> list[Finding]:
    have = venv_mod.installed(venv_mod.site_packages(info.path, info.python_version))
    env = requirements.environment(info.python_version)
    out = []
    for path in requirement_files(inst):
        try:
            reqs = requirements.parse(Path(path).read_text(errors="replace"))
        except OSError:
            continue
        apply, _unknown = requirements.applicable(reqs, env)
        gone = requirements.missing(apply, have)
        if not gone:
            continue
        core = path == os.path.join(inst["source"], "requirements.txt")
        shown = ", ".join(gone[:12]) + (f" and {len(gone) - 12} more" if len(gone) > 12 else "")
        out.append(Finding(
            "H2", "venv-packages-missing", ERROR if core else WARNING, path,
            f"{len(gone)} package(s) from {path} missing in {info.path}", shown,
            commands=[uv_as(user, f"pip install --python {q(info.path + '/bin/python')} -r {q(path)}")],
            repair=repair, installation=inst["root"],
        ))
    return out


# -- H3 / H4 / H5 -------------------------------------------------------------

def check_configs(ctx: Context) -> list[Finding]:
    from ..discover.configs import split_addons_path

    out = []
    roots = {i["root"]: i for i in ctx.snapshot["installations"]}
    for conf in ctx.snapshot["instances"]:
        path = conf["path"]
        entries = split_addons_path(conf["options"].get("addons_path"))
        if conf["installation"] is None:
            hint = conf.get("version_hint")
            detail = f"The folder name says Odoo {hint}; no Odoo {hint} installation was found." if hint else \
                "No addons_path entry belongs to a discovered installation."
            out.append(Finding("H4", "config-orphan", WARNING, path, f"Orphan config {path}", detail))
            continue
        missing = [e for e in entries if not os.path.isdir(e)]
        if missing:
            out.append(Finding("H3", "addons-path-missing", ERROR, path,
                               f"{len(missing)} addons_path entr{'y' if len(missing) == 1 else 'ies'} missing in {path}",
                               ", ".join(missing), commands=[f"sudoedit {q(path)}"], installation=conf["installation"]))
        mixed = [p for p in conf["problems"] if p.startswith("addons_path mixes installations")]
        if mixed:
            owners = mixed[0].split(": ", 1)[1]
            versions = ", ".join(f"{r} ({roots[r].get('version') or '?'})" for r in owners.split(", ") if r in roots)
            out.append(Finding("H5", "addons-path-cross-version", ERROR, path,
                               f"{path} mixes addons of several installations", versions or owners,
                               commands=[f"sudoedit {q(path)}"], installation=conf["installation"]))
        folder = [p for p in conf["problems"] if p.startswith("config folder says")]
        if folder:
            out.append(Finding("H5", "config-folder-version", WARNING, path, f"{path} is in the wrong version folder",
                               folder[0], installation=conf["installation"]))
    return out


# -- H6 / H7 ------------------------------------------------------------------

REPO_SKIP = {"venv", ".venv", "node_modules", "__pycache__", ".local", ".cache", "filestore", ".npm", "static"}


def find_repos(root: str, max_depth: int = 3) -> list[tuple[str, str | None]]:
    """(repository folder, gitdir of a worktree or None for a normal clone). Does not enter repositories."""
    result: list[tuple[str, str | None]] = []

    def walk(directory: str, depth: int) -> None:
        dot_git = os.path.join(directory, ".git")
        if os.path.isdir(dot_git):
            result.append((directory, None))
            return
        if os.path.isfile(dot_git):
            try:
                text = Path(dot_git).read_text(errors="replace").strip()
            except OSError:
                return
            gitdir = text[7:].strip() if text.startswith("gitdir:") else ""
            if gitdir and not os.path.isabs(gitdir):
                gitdir = os.path.normpath(os.path.join(directory, gitdir))
            result.append((directory, gitdir or ""))
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


def check_git(ctx: Context) -> list[Finding]:
    out = []
    for inst in ctx.snapshot["installations"]:
        root = inst["root"]
        repos = find_repos(root)
        if inst["source"] != root and not any(r == inst["source"] for r, _ in repos):
            repos += find_repos(inst["source"], 0)
        for repo, gitdir in repos:
            if gitdir is not None and not os.path.isdir(gitdir):
                out.append(Finding(
                    "H6", "git-dead-worktree", WARNING, repo, f"Dead git worktree {repo}",
                    f"{repo}/.git points to {gitdir or '(nothing)'}, which does not exist.",
                    commands=[f"# keep the files as a plain folder:\nrm {q(repo + '/.git')}",
                              "# or move it away and clone the repository again"],
                    installation=root,
                ))
                continue
            try:
                owner = os.stat(repo).st_uid
            except OSError:
                continue
            if owner == ctx.uid:
                continue
            err = ctx.git(repo)
            if "dubious ownership" in err:
                out.append(Finding(
                    "H7", "git-dubious-ownership", WARNING, repo, f"git refuses {repo} (dubious ownership)",
                    f"Owned by {_user(owner)}, not by {ctx.dev_user}.",
                    commands=[f"git config --global --add safe.directory {q(repo)}"], installation=root,
                ))
    return out


def _user(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# -- H8 / H9 ------------------------------------------------------------------

def check_runtime(ctx: Context) -> list[Finding]:
    out = []
    for c in ctx.snapshot["ports"]["conflicts"]:
        pids = ", ".join(map(str, c["pids"]))
        if c["kind"] == "shared":
            detail = f"Odoo processes {pids} all want port {c['port']}."
        else:
            detail = f"Odoo process {pids} wants port {c['port']}, but pid {c['holder']} holds it."
        out.append(Finding("H8", "port-conflict", ERROR, f"port {c['port']}", f"Port {c['port']} conflict", detail,
                           commands=[f"ss -ltnp 'sport = :{c['port']}'"]))
    for unit in ctx.snapshot["units"]:
        if unit.get("active_state") == "failed":
            name = unit["name"]
            out.append(Finding(
                "H9", "unit-failed", ERROR, name, f"systemd unit {name} failed",
                f"Runs as {unit.get('user') or 'root'} with {unit.get('config') or 'no -c config'}.",
                commands=[f"systemctl status {q(name)}", f"journalctl -u {q(name)} -n 50 --no-pager",
                          f"# if it is not needed:\nsudo systemctl disable --now {q(name)}"],
            ))
    return out


# -- H10 ----------------------------------------------------------------------

SECRET_KEYS = ("db_password", "admin_passwd")


def check_secrets(ctx: Context) -> list[Finding]:
    owners = {i["root"]: i.get("owner") for i in ctx.snapshot["installations"]}
    groups: dict[str | None, list[tuple[str, int]]] = {}
    for conf in ctx.snapshot["instances"]:
        path = conf["path"]
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            continue
        if not mode & 0o004:
            continue
        raw = parse_config(Path(path)) or {}
        if not any(raw.get(k) and raw.get(k) != "False" for k in SECRET_KEYS):
            continue
        groups.setdefault(owners.get(conf["installation"]), []).append((path, mode))
    out = []
    for owner, items in sorted(groups.items(), key=lambda kv: kv[0] or ""):
        files = " ".join(q(p) for p, _ in items)
        if owner:
            # The run-as user reads it through the group; the dev user owns it so discovery still works.
            commands = [f"sudo chown {q(ctx.dev_user)}:{q(owner)} {files}", f"sudo chmod 0640 {files}"]
        else:
            commands = [f"sudo chown {q(ctx.dev_user)} {files}", f"sudo chmod 0600 {files}"]
        listed = ", ".join(f"{p} ({m:04o})" for p, m in items)
        root = next((c["installation"] for c in ctx.snapshot["instances"] if c["path"] == items[0][0]), None)
        out.append(Finding("H10", "config-secrets-exposed", WARNING, items[0][0] if len(items) == 1 else os.path.dirname(items[0][0]),
                           f"{len(items)} config(s) with passwords readable by every user"
                           + (f" (run as {owner})" if owner else " (orphans)"),
                           listed, commands=commands, repair="config-perms" if owner and root else None,
                           installation=root if owner else None))
    return out


# -- H13 ----------------------------------------------------------------------

def check_config_permissions(ctx: Context) -> list[Finding]:
    from .permissions import PermissionsError, plan_config_perms

    out = []
    for inst in ctx.snapshot["installations"]:
        if not inst.get("owner") or not any(i.get("installation") == inst["root"] for i in ctx.snapshot["instances"]):
            continue
        try:
            plan = plan_config_perms(ctx.snapshot, inst["root"], ctx.dev_user)
        except PermissionsError:
            continue
        if not plan.changes:
            continue
        listed = "; ".join(f"{c.path}: {c.current} -> {c.target}" for c in plan.changes)
        out.append(Finding("H13", "config-permissions", WARNING, inst["root"],
                           f"{len(plan.changes)} config path(s) of {inst['root']} differ from the standard permissions",
                           listed, commands=[f"sudo {line}" for line in plan.script.splitlines()[3:-1]],
                           repair="config-perms", installation=inst["root"]))
    return out


# -- H11 ----------------------------------------------------------------------

def check_filestores(ctx: Context) -> list[Finding]:
    owners = {i["root"]: i.get("owner") for i in ctx.snapshot["installations"]}
    out = []
    for entry in ctx.snapshot.get("databases", []):
        for db in entry["databases"]:
            if db.get("filestore_exists") is False:
                user = owners.get(entry["installation"]) or "?"
                out.append(Finding(
                    "H11", "filestore-missing", WARNING, db["filestore"] or db["name"],
                    f"Database {db['name']} has no filestore for {user}",
                    f"Expected {db['filestore']}. Restore it from the backup that matches the database, or copy "
                    "it from the user that created the database.",
                    installation=entry["installation"],
                ))
    return out


CHECKS = (check_venv, check_configs, check_git, check_runtime, check_secrets, check_filestores, check_config_permissions)
