"""H12: rebuild a venv next to the old one, validate it, then swap. The old venv stays in service until the swap.

1. ``uv python install`` the pinned Python into the shared directory.
2. ``uv venv --relocatable venv.new`` as the run-as user (through its agent), then ``uv pip install`` the
   requirements of Odoo and of the custom repositories.
3. Validate ``venv.new``: ``odoo-bin --version`` and imports of Odoo's core dependencies.
4. Swap: ``venv`` -> ``venv.bak-<time>``, ``venv.new`` -> ``venv``. Then run ``odoo-bin --version`` from the
   final path; if that fails, the swap is undone.

On a failure before the swap ``venv.new`` is removed; the old venv was never touched. Nothing is deleted after
a swap: the old venv stays as ``venv.bak-<time>`` until the user removes it.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import shlex
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import client, paths
from ..discover import venv as venv_mod
from ..provision.execute import ProvisionError, Report, _agent_session, _emit, ensure_python, run_in_agent
from ..provision.plan import Step
from ..provision.preflight import FAIL, OK, WARN, Check
from ..provision.spec import BUILD_CFLAGS, BUILD_PACKAGES, PIP_EXTRA_PACKAGES, PIP_OVERRIDES, PYTHON_BY_VERSION
from . import requirements
from .checks import requirement_files

PYTHON_DIR = "/opt/odoo-dev-panel/python"
# Never carried over from the old venv: installers and build tools.
NOT_CARRIED = {"pip", "setuptools", "wheel", "uv", "distribute", "pkg-resources"}
# Imported by every supported Odoo version at start-up; a venv without them cannot serve.
CORE_IMPORTS = ("psycopg2", "lxml", "PIL", "werkzeug", "babel", "reportlab", "dateutil")

# Swap with positional arguments, so no path is ever pasted into shell text. $1 venv, $2 venv.new, $3 backup.
SWAP_SCRIPT = 'set -e\nif [ -e "$1" ]; then mv -T -- "$1" "$3"; fi\nif ! mv -T -- "$2" "$1"; then\n' \
              '  if [ -e "$3" ]; then mv -T -- "$3" "$1"; fi\n  exit 1\nfi\n'
# Undo after a failed final check. $1 venv, $3 backup, $4 where the failed new venv goes.
UNDO_SCRIPT = 'set -e\nmv -T -- "$1" "$4"\nif [ -e "$3" ]; then mv -T -- "$3" "$1"; fi\n'


class RepairError(ProvisionError):
    pass


@dataclass
class VenvRepairPlan:
    root: str
    source: str
    version: str
    run_as: str
    python: str
    venv: str
    new: str
    old_exists: bool
    requirements: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)  # in the old venv, in no requirements file
    carry_extras: bool = False
    overrides: list[str] = field(default_factory=list)  # name+specifier lines chosen for requirement conflicts
    checks: list[Check] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}


def _writable_by(path: str, user: str) -> bool:
    """Can ``user`` create and rename entries in ``path``, judged from owner, group and mode?"""
    try:
        st, account = os.stat(path), pwd.getpwnam(user)
    except (OSError, KeyError):
        return False
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == account.pw_uid:
        return bool(mode & 0o300 == 0o300)
    groups = {account.pw_gid} | {g.gr_gid for g in grp.getgrall() if user in g.gr_mem}
    if st.st_gid in groups:
        return bool(mode & 0o030 == 0o030)
    return bool(mode & 0o003 == 0o003)


def python_dir_blockers(user: str, python_dir: str = PYTHON_DIR) -> list[str]:
    """Entries of the shared Python directory that ``user`` must write to install a Python, but cannot."""
    blocked = [python_dir] if not _writable_by(python_dir, user) else []
    temp = os.path.join(python_dir, ".temp")
    if os.path.isdir(temp) and not _writable_by(temp, user):
        blocked.append(temp)
    lock = os.path.join(python_dir, ".lock")
    if os.path.exists(lock) and not _file_writable_by(lock, user):
        blocked.append(lock)
    return blocked


def _file_writable_by(path: str, user: str) -> bool:
    try:
        st, account = os.stat(path), pwd.getpwnam(user)
    except (OSError, KeyError):
        return False
    groups = {account.pw_gid} | {g.gr_gid for g in grp.getgrall() if user in g.gr_mem}
    bit = 0o200 if st.st_uid == account.pw_uid else 0o020 if st.st_gid in groups else 0o002
    return bool(st.st_mode & bit)


def installed_python(python: str) -> str | None:
    from ..provision.execute import managed_python

    return managed_python(python, PYTHON_DIR)


def missing_build_packages() -> list[str] | None:
    """Build packages that are not installed, or None when dpkg cannot tell (not a Debian system)."""
    try:
        out = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package} ${db:Status-Abbrev}\n", *BUILD_PACKAGES],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    ok = {line.split()[0].split(":")[0] for line in out.stdout.splitlines() if line.split()[1:2] == ["ii"]}
    return [p for p in BUILD_PACKAGES if p not in ok]


def old_packages(venv: str) -> dict[str, str]:
    """Distributions of the old venv, from every lib/pythonX.Y it has (a broken venv is still readable)."""
    found: dict[str, str] = {}
    for version in venv_mod.inspect(venv).site_versions:
        found.update(venv_mod.installed(venv_mod.site_packages(venv, version)))
    return found


def plan_venv_repair(inst: dict, processes: list[dict], python: str | None = None, custom: bool = True,
                     carry_extras: bool = False, agent_running: bool | None = None,
                     build_missing: Callable[[], list[str] | None] = missing_build_packages) -> VenvRepairPlan:
    """What the rebuild will do, and why it cannot run yet (checks with status fail). Changes nothing."""
    root, version, run_as = inst["root"], inst.get("version") or "", inst.get("owner") or ""
    major = int(version.split(".")[0]) if version[:2].isdigit() else 0
    python = python or PYTHON_BY_VERSION.get(major, "")
    venv = inst.get("venv") or os.path.join(root, "venv")
    p = VenvRepairPlan(root=root, source=inst["source"], version=version, run_as=run_as, python=python,
                       venv=venv, new=venv + ".new", old_exists=os.path.lexists(venv), carry_extras=carry_extras)
    checks = p.checks

    def add(check_id: str, ok: bool, good: str, bad: str, level: str = FAIL) -> None:
        checks.append(Check(check_id, OK if ok else level, good if ok else bad))

    add("version", major in PYTHON_BY_VERSION, f"Odoo {version}",
        f"Odoo {version or '?'}: the venv rebuild supports Odoo {min(PYTHON_BY_VERSION)}-{max(PYTHON_BY_VERSION)}")
    add("python", bool(python) and python.count(".") == 1 and python.replace(".", "").isdigit(),
        f"Python {python} (pinned, managed by uv)", f"bad Python version {python!r}")
    add("run-as", bool(run_as) and run_as != pwd.getpwuid(os.getuid()).pw_name,
        f"built as {run_as}, through its agent", f"no separate run-as user for {root} ({run_as or 'unknown'})")
    add("odoo-source", os.path.isfile(os.path.join(inst["source"], "requirements.txt")),
        f"{inst['source']}/requirements.txt found", f"{inst['source']}/requirements.txt is missing")
    if run_as:
        add("root-writable", _writable_by(root, run_as), f"{run_as} can create {p.new} and rename the venv",
            f"{run_as} cannot write {root}. Run: sudo chgrp {shlex.quote(run_as)} {shlex.quote(root)} && "
            f"sudo chmod g+w {shlex.quote(root)}")
    running = [pr for pr in processes if pr.get("installation") == root or (run_as and pr.get("user") == run_as)]
    add("not-running", not running, "no Odoo process of this installation runs",
        "stop these Odoo processes first (the swap must not happen under a running server): pid "
        + ", ".join(str(pr["pid"]) for pr in running))
    if agent_running is not None:
        add("agent", agent_running, f"agent of {run_as} is running", f"agent of {run_as} is not running: unlock it first")
    add("python-dir", os.path.isdir(PYTHON_DIR), f"{PYTHON_DIR} exists",
        f"{PYTHON_DIR} is missing. Install the Odoo Dev Panel .deb")
    if python and run_as and os.path.isdir(PYTHON_DIR):
        if installed_python(python):
            checks.append(Check("python-installed", OK, f"Python {python} is already in {PYTHON_DIR}: reused"))
        else:
            blocked = python_dir_blockers(run_as)
            add("python-dir-writable", not blocked, f"{run_as} can install Python {python} in {PYTHON_DIR}",
                f"{run_as} cannot write {', '.join(blocked)} (made by another version user). "
                f"Run: sudo chmod -R g+rwX {PYTHON_DIR}")
    gone = build_missing()
    if gone:
        add("build-packages", False, "", "packages for building Python dependencies are missing; some packages may "
            f"not build. Run: sudo apt-get install {' '.join(gone)}", level=WARN)
    if os.path.lexists(p.new):
        checks.append(Check("leftover", WARN, f"{p.new} exists (left by an interrupted repair): it is removed first"))

    p.requirements = [f for f in requirement_files(inst) if custom or f == os.path.join(inst["source"], "requirements.txt")]
    if p.old_exists and python:
        wanted: set[str] = set()
        env = requirements.environment(python)
        for path in p.requirements:
            try:
                reqs = requirements.parse(Path(path).read_text(errors="replace"))
            except OSError:
                continue
            wanted |= {r.name for r in requirements.applicable(reqs, env)[0]}
        p.extras = sorted(set(old_packages(venv)) - wanted - NOT_CARRIED)
    if python:
        p.overrides, won, left = resolve_conflicts(p.requirements, os.path.join(inst["source"], "requirements.txt"),
                                                   requirements.environment(python))
        for note in won:
            checks.append(Check("conflict-resolved", WARN, note))
        for note in left:
            checks.append(Check("conflict", WARN, f"{note}: the install will fail until the files agree"))
    p.steps = build_steps(p)
    return p


def resolve_conflicts(files: list[str], core: str, env: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """(override lines, notes of what won, notes of conflicts left alone).

    Two requirement files asking one package for versions no single version satisfies make the install fail
    ("python-dateutil==2.7.3 and python-dateutil==2.8.2"). The pin of a custom repository wins over Odoo's own
    file: it was written for this installation. Conflicts between custom files are left for the developer.
    """
    from ..pyenv import specs

    asked: dict[str, list[tuple[str, str]]] = {}
    for path in files:
        try:
            reqs = requirements.parse(Path(path).read_text(errors="replace"))
        except OSError:
            continue
        for req in requirements.applicable(reqs, env)[0]:
            spec = specs.requirement_spec(req.raw)
            if spec:
                asked.setdefault(req.name, []).append((path, spec))
    lines, won, left = [], [], []
    for name, rows in sorted(asked.items()):
        distinct = sorted({spec for _, spec in rows})
        reason = specs.conflict(distinct) if len(distinct) > 1 else None
        if not reason:
            continue
        custom = sorted({spec for path, spec in rows if path != core})
        if custom and not (len(custom) > 1 and specs.conflict(custom)):
            chosen = ",".join(custom)
            lines.append(f"{name}{chosen}")
            won.append(f"{name}: {reason}; using {chosen} from the custom requirements")
        else:
            left.append(f"{name}: {reason}")
    return lines, won, left


def override_lines(p: VenvRepairPlan) -> list[str]:
    return list(PIP_OVERRIDES.get(p.python, [])) + p.overrides


def _uv_env(p: VenvRepairPlan) -> str:
    return f"UV_PYTHON_INSTALL_DIR={shlex.quote(PYTHON_DIR)}"


def pip_args(p: VenvRepairPlan) -> list[str]:
    args = ["pip", "install", "--python", f"{p.new}/bin/python"]
    if override_lines(p):
        args += ["--override", f"{p.root}/.odp-pip-overrides.txt"]
    for path in p.requirements:
        args += ["-r", path]
    args += list(PIP_EXTRA_PACKAGES)
    if p.carry_extras:
        args += p.extras
    return args


def validate_argv(p: VenvRepairPlan, venv: str) -> list[str]:
    return [f"{venv}/bin/python", "-c", f"import {', '.join(CORE_IMPORTS)}"]


def build_steps(p: VenvRepairPlan) -> list[Step]:
    uv, q = paths.uv_command(), shlex.quote
    steps = [Step("python", 1, "agent", f"Install Python {p.python} in the shared directory, unless it is there already",
                  [f"umask 002; {_uv_env(p)} {uv} python install --no-bin {p.python}"])]
    if os.path.lexists(p.new):
        steps.append(Step("cleanup", 1, "agent", f"Remove the leftover {p.new}", [f"rm -rf {q(p.new)}"]))
    steps += [
        Step("venv", 2, "agent", f"Create {p.new} (owned by {p.run_as})",
             [f"{_uv_env(p)} {uv} venv --relocatable --python {p.python} --python-preference only-managed {q(p.new)}"]),
        Step("pip", 2, "agent", "Install the requirements" + (" and the packages carried over" if p.carry_extras else ""),
             [f"CFLAGS={q(BUILD_CFLAGS)} {uv} {shlex.join(pip_args(p))}"]),
        Step("validate", 3, "agent", f"Validate {p.new}",
             [shlex.join([f"{p.new}/bin/python", f"{p.source}/odoo-bin", "--version"]), shlex.join(validate_argv(p, p.new))]),
        Step("swap", 4, "agent", "Swap: the old venv becomes venv.bak-<time>, the new one becomes venv"
             if p.old_exists else "Move the new venv into place",
             [f"mv -T {q(p.venv)} {q(p.venv)}.bak-<time>" if p.old_exists else "", f"mv -T {q(p.new)} {q(p.venv)}"]),
        Step("verify", 5, "agent", "Run odoo-bin --version from the final path (the swap is undone if it fails)",
             [shlex.join([f"{p.venv}/bin/python", f"{p.source}/odoo-bin", "--version"])]),
    ]
    for s in steps:
        s.commands = [c for c in s.commands if c]
    return steps


def write_receipt(p: VenvRepairPlan, status: str, phase: str, backup: str | None, state_dir: Path | None = None) -> str:
    """Repair receipts live in the dev user's state directory: the installation root may not be writable by them."""
    directory = (state_dir or paths.agent_state_dir()) / "repairs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    path = directory / f"{os.path.basename(p.root.rstrip('/'))}-venv-{stamp.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({
        "tool": "odoo-dev-panel", "repair": "venv", "status": status, "last_phase": phase,
        "updated_at": stamp.isoformat(timespec="seconds"), "backup": backup, "plan": p.as_dict(),
    }, indent=2, default=str) + "\n")
    return str(path)


def _read(path: str) -> str:
    try:
        return "\n".join(line for line in Path(path).read_text().splitlines() if not line.startswith("#"))
    except OSError:
        return ""


def _still_running(p: VenvRepairPlan) -> list[int]:
    from ..discover import scan

    snap = scan.scan(with_databases=False)
    return [pr["pid"] for pr in snap["processes"] if pr.get("installation") == p.root or pr.get("user") == p.run_as]


async def repair_venv(p: VenvRepairPlan, report: Report, running: Callable[[VenvRepairPlan], list[int]] = _still_running,
                      state_dir: Path | None = None) -> dict:
    """Run the plan. Raises RepairError; the old venv is in service again whenever this raises."""
    if not p.ok:
        raise RepairError("the plan has failed checks: " + "; ".join(c.detail for c in p.checks if c.status == FAIL))
    status = await client.agent_status(p.run_as)
    if status["state"] != "running":
        raise RepairError(f"agent of {p.run_as} is not running: unlock it first")
    uv, env = paths.uv_command(), {"UV_PYTHON_INSTALL_DIR": PYTHON_DIR}
    backup = f"{p.venv}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    phase, swapped = "python", False
    conn = await client.connect(p.run_as)

    async def agent(step: str, argv: list[str], extra_env: dict | None = None) -> None:
        await run_in_agent(conn, step, argv, p.root, report, {**env, **(extra_env or {})})

    try:
        _emit(report, phase, "start", f"Python {p.python}")
        await ensure_python(conn, phase, p.python, PYTHON_DIR, p.root, report)
        _emit(report, phase, "ok")
        if os.path.lexists(p.new):
            phase = "cleanup"
            _emit(report, phase, "start", f"Remove the leftover {p.new}")
            await agent(phase, ["/bin/rm", "-rf", "--", p.new])
            _emit(report, phase, "ok")
        try:
            phase = "venv"
            _emit(report, phase, "start", f"Create {p.new}")
            await agent(phase, [uv, "venv", "--relocatable", "--python", p.python, "--python-preference", "only-managed", p.new])
            _emit(report, phase, "ok")
            phase = "pip"
            _emit(report, phase, "start", "Install the requirements")
            if override_lines(p) and _read(f"{p.root}/.odp-pip-overrides.txt").split() != override_lines(p):
                # Written by the run-as user: the dev user may not be able to write the installation root.
                await agent(phase, ["/bin/sh", "-c", 'f=$1; shift; printf "%s\\n" "$@" > "$f"', "pins",
                                    f"{p.root}/.odp-pip-overrides.txt", *override_lines(p)])
            await agent(phase, [uv, *pip_args(p)], {"CFLAGS": BUILD_CFLAGS})
            _emit(report, phase, "ok")
            phase = "validate"
            _emit(report, phase, "start", f"Validate {p.new}")
            await agent(phase, [f"{p.new}/bin/python", f"{p.source}/odoo-bin", "--version"])
            await agent(phase, validate_argv(p, p.new))
            lost = sorted(set(old_packages(p.venv)) - set(venv_mod.installed(venv_mod.site_packages(p.new, p.python))) - NOT_CARRIED)
            if lost:
                _emit(report, phase, "output", f"Not in the new venv ({len(lost)}), were in the old one: {', '.join(lost)}")
            _emit(report, phase, "ok")
            busy = running(p)
            if busy:
                raise RepairError(f"Odoo of {p.root} started during the rebuild (pid {', '.join(map(str, busy))}); "
                                  "stop it and run the repair again")
        except BaseException:
            _emit(report, "cleanup", "output", f"Removing {p.new}; the old venv was not touched")
            try:
                await agent("cleanup", ["/bin/rm", "-rf", "--", p.new])
            except Exception as exc:  # noqa: BLE001
                _emit(report, "cleanup", "output", f"could not remove {p.new}: {exc}")
            raise
        phase = "swap"
        _emit(report, phase, "start", f"{p.venv} -> {backup}, {p.new} -> {p.venv}" if p.old_exists else f"{p.new} -> {p.venv}")
        await agent(phase, ["/bin/sh", "-c", SWAP_SCRIPT, "swap", p.venv, p.new, backup])
        swapped = True
        _emit(report, phase, "ok")
        phase = "verify"
        _emit(report, phase, "start", "odoo-bin --version from the final path")
        code, _ = await _agent_session(conn, phase, [f"{p.venv}/bin/python", f"{p.source}/odoo-bin", "--version"],
                                       p.root, report, env)
        if code != 0:
            failed = f"{p.venv}.failed-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            await agent("undo", ["/bin/sh", "-c", UNDO_SCRIPT, "undo", p.venv, p.new, backup, failed])
            swapped = False
            raise RepairError(f"the new venv failed after the swap; it was moved to {failed} and the old venv is back")
        _emit(report, phase, "ok")
        receipt = write_receipt(p, "complete", phase, backup if p.old_exists else None, state_dir)
        return {"venv": p.venv, "backup": backup if p.old_exists else None, "receipt": receipt}
    except Exception as exc:
        try:
            write_receipt(p, "failed", phase, backup if swapped else None, state_dir)
        except OSError:
            pass
        _emit(report, phase, "fail", str(exc))
        if isinstance(exc, RepairError):
            raise
        raise RepairError(str(exc)) from exc
    finally:
        await conn.close()
