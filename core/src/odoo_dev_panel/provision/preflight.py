"""Read-only checks run as the dev user before anything is changed."""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

from .spec import GROUP, MIN_FREE_BYTES, ProvisionSpec

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    id: str
    status: str
    detail: str


class SystemFacts:
    """Everything preflight reads from the machine. Tests replace this class."""

    def path_exists(self, path: str) -> bool:
        return os.path.lexists(path)

    def user_exists(self, name: str) -> bool:
        try:
            pwd.getpwnam(name)
            return True
        except KeyError:
            return False

    def group_exists(self, name: str) -> bool:
        try:
            grp.getgrnam(name)
            return True
        except KeyError:
            return False

    def in_group(self, group: str) -> bool:
        """Is the current process a member of the group (effective, not just listed in /etc/group)?"""
        try:
            return grp.getgrnam(group).gr_gid in os.getgroups()
        except KeyError:
            return False

    def listed_in_group(self, user: str, group: str) -> bool:
        try:
            return user in grp.getgrnam(group).gr_mem
        except KeyError:
            return False

    def which(self, command: str) -> str | None:
        return shutil.which(command)

    def free_bytes(self, path: str) -> int:
        probe = Path(path)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        return usage.free

    def port_open(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    def file_readable(self, path: str) -> bool:
        return os.path.isfile(path) and os.access(path, os.R_OK)

    def current_user(self) -> str:
        return pwd.getpwuid(os.getuid()).pw_name


def run_preflight(spec: ProvisionSpec, facts: SystemFacts | None = None) -> list[Check]:
    f = facts or SystemFacts()
    checks: list[Check] = []

    def add(check_id: str, ok: bool, detail_ok: str, detail_bad: str, bad: str = FAIL) -> None:
        checks.append(Check(check_id, OK if ok else bad, detail_ok if ok else detail_bad))

    add("dev-user", f.current_user() == spec.dev_user,
        f"running as {spec.dev_user}", f"running as {f.current_user()}, but the dev user is {spec.dev_user}")
    # Existing parts are reused, never overwritten: a warning tells the user what will be kept.
    add("root-path", not f.path_exists(spec.root),
        f"{spec.root} does not exist", f"{spec.root} already exists: it is reused, existing files are not overwritten", bad=WARN)
    add("run-as-user", not f.user_exists(spec.run_as),
        f"Linux user {spec.run_as} does not exist", f"Linux user {spec.run_as} already exists: it is reused", bad=WARN)
    if f.path_exists(spec.conf_path):
        add("config-file", f.file_readable(spec.conf_path), "",
            f"{spec.conf_path} exists but you cannot read it. Fix its group or mode, then retry")
        if checks[-1].status == OK:
            checks[-1] = Check("config-file", WARN,
                               f"{spec.conf_path} exists: kept as is, its db_password is reused for the PostgreSQL role")
    else:
        add("config-file", True, f"{spec.conf_path} does not exist", "")
    add("group", f.group_exists(GROUP),
        f"group {GROUP} exists", f"group {GROUP} is missing. Install the Odoo Dev Panel .deb, which creates it")
    add("group-member", f.in_group(GROUP),
        f"{spec.dev_user} is in {GROUP}",
        f"{spec.dev_user} is not an active member of {GROUP}. Run: sudo usermod -aG {GROUP} {spec.dev_user}, then log out and back in")
    add("python-dir", f.path_exists(spec.python_dir),
        f"{spec.python_dir} exists", f"{spec.python_dir} is missing. Install the Odoo Dev Panel .deb")
    add("git", bool(f.which("git")), "git found", "git is not installed (sudo apt install git)")
    add("sudo", bool(f.which("sudo")), "sudo found", "sudo is not installed")
    add("postgres", f.port_open(spec.pg_host, spec.pg_port),
        f"PostgreSQL answers on {spec.pg_host}:{spec.pg_port}",
        f"nothing listens on {spec.pg_host}:{spec.pg_port}. Install and start PostgreSQL first")
    free = f.free_bytes(os.path.dirname(spec.root) or "/")
    add("disk", free >= MIN_FREE_BYTES, f"{free / 1024**3:.1f} GiB free",
        f"{free / 1024**3:.1f} GiB free, at least {MIN_FREE_BYTES / 1024**3:.0f} GiB needed")
    if spec.enterprise_archive:
        add("enterprise-archive", f.file_readable(spec.enterprise_archive),
            f"{spec.enterprise_archive} is readable", f"cannot read {spec.enterprise_archive}")
    if spec.http_port is not None:
        add("http-port", not f.port_open("127.0.0.1", spec.http_port),
            f"port {spec.http_port} is free", f"port {spec.http_port} is in use", bad=WARN)
    add("wkhtmltopdf", bool(f.which("wkhtmltopdf")), "wkhtmltopdf found",
        "wkhtmltopdf not found. Odoo needs the patched-Qt build from wkhtmltopdf.org, not the Ubuntu package. PDF reports will fail without it",
        bad=WARN)
    add("node-rtlcss", bool(f.which("node")) and bool(f.which("rtlcss")), "node and rtlcss found",
        "node or rtlcss missing. Right-to-left languages need rtlcss (npm install -g rtlcss)", bad=WARN)
    return checks


def has_failures(checks: list[Check]) -> bool:
    return any(c.status == FAIL for c in checks)
