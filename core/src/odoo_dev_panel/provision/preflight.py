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

    def tree(self, path: str, marker: str) -> str:
        from .execute import tree_state

        return tree_state(path, marker)

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)


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
    checks += destination_checks(spec, f)
    add("wkhtmltopdf", bool(f.which("wkhtmltopdf")), "wkhtmltopdf found",
        "wkhtmltopdf not found. Odoo needs the patched-Qt build from wkhtmltopdf.org, not the Ubuntu package. PDF reports will fail without it",
        bad=WARN)
    add("node-rtlcss", bool(f.which("node")) and bool(f.which("rtlcss")), "node and rtlcss found",
        "node or rtlcss missing. Right-to-left languages need rtlcss (npm install -g rtlcss)", bad=WARN)
    return checks


def destination_checks(spec: ProvisionSpec, f: SystemFacts | None = None) -> list[Check]:
    """T3: where each source tree lands. Existing clones are reused (no fetch); a non-empty folder that is not one
    stops the run; a folder reached through a symbolic link must stay inside the root."""
    f = f or SystemFacts()
    out: list[Check] = []
    real_root = f.realpath(spec.root)
    targets = [("odoo", f"{spec.root}/odoo", "odoo-bin")]
    if spec.has_enterprise:
        targets.append(("enterprise", f"{spec.root}/enterprise", "web_enterprise"))
    targets += [(f"repo:{r.name}", f"{spec.root}/{r.destination}", "__manifest__.py") for r in spec.custom]
    for cid, path, marker in targets:
        existing = path
        while not f.path_exists(existing) and existing != "/":
            existing = os.path.dirname(existing)
        real = f.realpath(existing)
        if f.path_exists(spec.root) and not (real == real_root or real.startswith(real_root + "/")):
            out.append(Check(cid, FAIL, f"{path}: {existing} leads outside {spec.root}"))
            continue
        state = f.tree(path, marker)
        if state == "present":
            out.append(Check(cid, WARN, f"{path} exists: kept as is, not fetched or switched"))
        elif state == "foreign":
            out.append(Check(cid, FAIL, f"{path} exists, is not empty and is not a source tree; move it away or choose another destination"))
        else:
            out.append(Check(cid, OK, f"{path} will be created"))
    return out


REMOTE_TIMEOUT = 15


def ls_remote(url: str, ref: str | None, timeout: float = REMOTE_TIMEOUT) -> tuple[str, str]:
    """(status, detail) for one remote: ok, missing (no such branch or tag), auth, network or error.
    Runs as the developer with the developer's SSH agent and credential helpers; never prompts."""
    from ..git import explain, runner

    cmd = ["git", "ls-remote", "--heads", "--tags", "--", url, ref] if ref else \
        ["git", "ls-remote", "--symref", "--", url, "HEAD"]
    res = runner.run(cmd, timeout=timeout)
    if res.ok:
        if ref and not res.stdout.strip():
            return "missing", f"no branch or tag {ref} on the remote"
        return "ok", f"{ref or 'default branch'} found"
    if res.code == 124:
        return "network", f"no answer within {timeout:g}s"
    problem = explain.from_stderr(res.stderr) or {"code": "error", "title": res.stderr.strip()[:200] or f"exit {res.code}"}
    kind = {"auth-failed": "auth", "host-key": "auth", "network": "network", "remote-missing": "auth"}.get(problem["code"], "error")
    return kind, problem["title"]


def remote_checks(spec: ProvisionSpec, check=ls_remote, facts: SystemFacts | None = None) -> list[Check]:
    """T3: does each remote answer and have the wanted branch? In parallel; trees that exist already are skipped.
    Unreachable (offline) is a warning; refused credentials or a missing branch fail, as the clone would."""
    from concurrent.futures import ThreadPoolExecutor

    from ..git import urls

    jobs = [("remote:odoo", spec.odoo_git, spec.odoo_branch, f"{spec.root}/odoo", "odoo-bin")]
    if spec.enterprise_git:
        jobs.append(("remote:enterprise", spec.enterprise_git, spec.enterprise_branch or f"{spec.version}.0",
                     f"{spec.root}/enterprise", "web_enterprise"))
    jobs += [(f"remote:{r.name}", r.url, r.branch, f"{spec.root}/{r.destination}", "__manifest__.py") for r in spec.custom]
    f = facts or SystemFacts()
    jobs = [j for j in jobs if f.tree(j[3], j[4]) == "missing"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda j: check(j[1], j[2]), jobs))
    out = []
    for (cid, url, _ref, _path, _marker), (status, detail) in zip(jobs, results):
        level = OK if status == "ok" else WARN if status == "network" else FAIL
        out.append(Check(cid, level, f"{urls.redact(url)}: {detail}"))
    return out


def has_failures(checks: list[Check]) -> bool:
    return any(c.status == FAIL for c in checks)
