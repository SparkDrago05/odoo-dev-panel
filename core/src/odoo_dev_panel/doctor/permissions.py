"""H13 repair: give the configs of one installation the standard owner, group and mode (one sudo call).

Standard: config owned by the developer, group = the run-as user's group, 0640 (0600 when the developer is the
run-as user). A folder that holds only this installation's configs: developer, run-as group, 2750, so files
created later get the run-as group. Shared folders (/etc, /etc/odoo, homes) are never changed. ``chown`` and
``chmod`` only; the receipt keeps the old owner, group and mode of every path.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import shlex
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .. import paths
from ..provision.execute import ProvisionError, Report, _emit

SHARED = ("/", "/etc", "/etc/odoo", "/opt", "/srv", "/home", "/usr", "/usr/local", "/var", "/var/lib")


class PermissionsError(ProvisionError):
    pass


@dataclass
class Change:
    path: str
    kind: str  # file | folder
    current: str  # owner:group mode
    target_owner: str
    target_group: str
    target_mode: str

    @property
    def target(self) -> str:
        return f"{self.target_owner}:{self.target_group} {self.target_mode}"


@dataclass
class PermsPlan:
    root: str
    run_as: str
    dev_user: str
    changes: list[Change] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # paths already right
    notes: list[str] = field(default_factory=list)  # folders left alone, and why

    @property
    def script(self) -> str:
        lines = ["#!/bin/bash", f"# Odoo Dev Panel: standard permissions for the configs of {self.root}", "set -e"]
        for c in self.changes:
            q = shlex.quote(c.path)
            lines.append(f"chown -- {shlex.quote(c.target_owner + ':' + c.target_group)} {q}")
            lines.append(f"chmod -- {c.target_mode} {q}")
        lines.append('echo "permissions applied"')
        return "\n".join(lines) + "\n"

    def as_dict(self) -> dict:
        return {**asdict(self), "changes": [{**asdict(c), "target": c.target} for c in self.changes],
                "script": self.script}


def _state(path: str) -> tuple[str, str, str]:
    st = os.stat(path)
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return owner, group, f"{stat.S_IMODE(st.st_mode) | (st.st_mode & stat.S_ISGID):04o}"


def _homes(*users: str) -> set[str]:
    out = set()
    for u in users:
        try:
            out.add(os.path.normpath(pwd.getpwnam(u).pw_dir))
        except KeyError:
            pass
    return out


def plan_config_perms(snapshot: dict, root: str, dev_user: str | None = None) -> PermsPlan:
    root = os.path.normpath(root)
    inst = next((i for i in snapshot["installations"] if i["root"] == root), None)
    if inst is None:
        raise PermissionsError(f"{root} is not a discovered Odoo installation")
    run_as = inst.get("owner")
    if not run_as:
        raise PermissionsError(f"{root} has no run-as user")
    dev = dev_user or pwd.getpwuid(os.getuid()).pw_name
    same = run_as == dev
    try:
        group = grp.getgrgid(pwd.getpwnam(run_as).pw_gid).gr_name
    except KeyError as exc:
        raise PermissionsError(f"user {run_as} or its group does not exist") from exc
    plan = PermsPlan(root, run_as, dev)
    mine = sorted(i["path"] for i in snapshot["instances"] if i.get("installation") == root)
    file_mode = "0600" if same else "0640"
    for path in mine:
        _add(plan, path, "file", dev, group, file_mode)
    if same:
        plan.notes.append("the run-as user is you: folders stay as they are")
        return plan
    # Folders: only when every config in them belongs to this installation.
    others = {os.path.normpath(i["path"]) for i in snapshot["instances"] if i.get("installation") != root}
    shared = set(SHARED) | _homes(dev, run_as)
    for folder in sorted({os.path.dirname(p) for p in mine}):
        if folder in shared:
            plan.notes.append(f"{folder}: shared folder, left as it is")
            continue
        try:
            confs = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".conf")]
        except OSError:
            plan.notes.append(f"{folder}: cannot be listed, left as it is")
            continue
        own = {os.path.normpath(m) for m in mine}
        foreign = [c for c in confs if os.path.normpath(c) in others or os.path.normpath(c) not in own]
        if foreign:
            plan.notes.append(f"{folder}: also holds {os.path.basename(foreign[0])} of another installation, left as it is")
            continue
        _add(plan, folder, "folder", dev, group, "2750")
    return plan


def _add(plan: PermsPlan, path: str, kind: str, owner: str, group: str, mode: str) -> None:
    try:
        cur_owner, cur_group, cur_mode = _state(path)
    except OSError as exc:
        plan.notes.append(f"{path}: {exc.strerror}, left as it is")
        return
    if (cur_owner, cur_group, cur_mode) == (owner, group, mode):
        plan.kept.append(path)
        return
    plan.changes.append(Change(path, kind, f"{cur_owner}:{cur_group} {cur_mode}", owner, group, mode))


def write_receipt(p: PermsPlan, status: str, state_dir: Path | None = None) -> str:
    directory = (state_dir or paths.agent_state_dir()) / "repairs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    path = directory / f"{os.path.basename(p.root.rstrip('/'))}-config-perms-{stamp.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({
        "tool": "odoo-dev-panel", "repair": "config-perms", "status": status,
        "updated_at": stamp.isoformat(timespec="seconds"), "root": p.root, "run_as": p.run_as,
        # what to put back to undo: chown <current owner:group>, chmod <current mode>
        "changes": [asdict(c) for c in p.changes],
    }, indent=2) + "\n")
    return str(path)


async def apply_config_perms(p: PermsPlan, report: Report, root_runner: Callable[[str, Report], Awaitable[int]],
                             state_dir: Path | None = None) -> dict:
    if not p.changes:
        _emit(report, "permissions", "ok", "nothing to change")
        return {"changed": 0, "receipt": None}
    receipt = write_receipt(p, "started", state_dir)
    _emit(report, "permissions", "start", f"{len(p.changes)} path(s), one sudo call")
    with tempfile.TemporaryDirectory(prefix="odp-perms-") as tmp:
        script = os.path.join(tmp, "perms.sh")
        fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(p.script)
        os.chmod(tmp, 0o755)  # root reads it; nothing secret in it
        os.chmod(script, 0o644)
        code = await root_runner(script, report)
    if code != 0:
        write_receipt(p, "failed", state_dir)
        _emit(report, "permissions", "fail", f"exit code {code}")
        raise PermissionsError(f"the permission script ended with exit code {code}; receipt {receipt}")
    wrong = [c.path for c in p.changes if _state(c.path) != (c.target_owner, c.target_group, c.target_mode)]
    if wrong:
        raise PermissionsError("not applied to: " + ", ".join(wrong))
    _emit(report, "permissions", "ok")
    return {"changed": len(p.changes), "receipt": write_receipt(p, "complete", state_dir)}
