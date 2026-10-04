"""Run a provision plan. Every action reports progress through one callback, so the CLI and the app share this code."""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .. import client, paths, rpc
from . import plan, preflight
from .spec import BUILD_CFLAGS, PIP_EXTRA_PACKAGES, PIP_OVERRIDES, ProvisionSpec

# report(event): {"step": id, "status": "start"|"output"|"ok"|"fail", "text": str}
Report = Callable[[dict], None]
# root_runner(script_path, report) -> exit code of the one sudo call
RootRunner = Callable[[str, Report], Awaitable[int]]


class ProvisionError(Exception):
    pass


def _emit(report: Report, step: str, status: str, text: str = "") -> None:
    report({"step": step, "status": status, "text": text})


async def _stream(proc: asyncio.subprocess.Process, report: Report, step: str) -> int:
    assert proc.stdout is not None
    async for line in proc.stdout:
        _emit(report, step, "output", line.decode(errors="replace").rstrip("\n"))
    return await proc.wait()


async def sudo_runner(script_path: str, report: Report, askpass_env: dict[str, str] | None = None) -> int:
    """Run the root script with one sudo call. With askpass_env, sudo asks through the app; else on the terminal."""
    cmd = ["sudo"]
    env = dict(os.environ)
    if askpass_env:
        cmd.append("-A")
        env.update(askpass_env)
        env["SUDO_ASKPASS"] = paths.askpass_command()
    proc = await asyncio.create_subprocess_exec(
        *cmd, "bash", script_path, stdin=None if not askpass_env else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    return await _stream(proc, report, "root-script")


async def run_local(step: str, argv: list[str], report: Report, env: dict[str, str] | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
    )
    code = await _stream(proc, report, step)
    if code != 0:
        raise ProvisionError(f"{shlex.join(argv[:2])} failed with exit code {code}")


async def run_in_agent(conn: rpc.Connection, step: str, argv: list[str], cwd: str, report: Report,
                       env: dict[str, str] | None = None) -> None:
    """Start a session in the agent, stream its log until it ends, fail on a non-zero exit code."""
    code, session_id = await _agent_session(conn, step, argv, cwd, report, env)
    if code != 0:
        raise ProvisionError(f"{step} ended with exit code {code} (session {session_id})")


async def _agent_session(conn: rpc.Connection, step: str, argv: list[str], cwd: str, report: Report,
                         env: dict[str, str] | None = None) -> tuple[int | None, str]:
    session = await conn.request("session.start", {"argv": argv, "cwd": cwd, "env": env or {}, "name": f"provision-{step}"})
    offset = 0
    while True:
        chunk = await conn.request("session.read", {"id": session["id"], "offset": offset})
        offset = chunk["offset"]
        for line in chunk["data"].splitlines():
            _emit(report, step, "output", line)
        if chunk["data"]:
            continue
        state = await conn.request("session.get", {"id": session["id"]})
        if state["state"] not in ("running", "stopping"):
            # one last read: output written between the read and the state check
            chunk = await conn.request("session.read", {"id": session["id"], "offset": offset})
            for line in chunk["data"].splitlines():
                _emit(report, step, "output", line)
            return state["exit_code"], session["id"]
        await asyncio.sleep(0.5)


def managed_python(python: str, python_dir: str) -> str | None:
    """Path of an already installed uv-managed Python, or None. Read-only, runs as the caller."""
    try:
        proc = subprocess.run(
            [paths.uv_command(), "python", "find", "--managed-python", "--no-python-downloads", python],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, cwd="/",
            env={**os.environ, "UV_PYTHON_INSTALL_DIR": python_dir},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found = proc.stdout.strip()
    return found if proc.returncode == 0 and found.startswith(python_dir.rstrip("/") + "/") else None


def python_install_argv(python: str) -> list[str]:
    """`uv python install` with umask 002: the shared directory must stay writable for the other version users
    (a Python installed by odoo16 is otherwise read-only for odoo15, and uv rewrites files in it on every install)."""
    return ["/bin/sh", "-c", 'umask 002; exec "$@"', "uv", paths.uv_command(), "python", "install", "--no-bin", python]


async def ensure_python(conn: rpc.Connection, step: str, python: str, python_dir: str, cwd: str, report: Report) -> None:
    found = await asyncio.to_thread(managed_python, python, python_dir)
    if found:
        _emit(report, step, "output", f"Python {python} already installed: {found}")
        return
    await run_in_agent(conn, step, python_install_argv(python), cwd, report, {"UV_PYTHON_INSTALL_DIR": python_dir})


def extract_archive(archive: str, dest: str) -> None:
    """Extract a .zip or .tar.* into dest. One top-level folder is stripped. Refuses paths that escape dest."""
    target = Path(dest).resolve()
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        tmp_path = Path(tmp)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                for name in zf.namelist():
                    if not (tmp_path / name).resolve().is_relative_to(tmp_path.resolve()):
                        raise ProvisionError(f"unsafe path in archive: {name}")
                zf.extractall(tmp_path)
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tf:
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(tmp_path, filter="data")
                else:
                    for member in tf.getmembers():
                        if not (tmp_path / member.name).resolve().is_relative_to(tmp_path.resolve()) or member.issym() or member.islnk():
                            raise ProvisionError(f"unsafe entry in archive: {member.name}")
                    tf.extractall(tmp_path)
        else:
            raise ProvisionError(f"not a .zip or .tar archive: {archive}")
        entries = list(tmp_path.iterdir())
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp_path
        for item in source.iterdir():
            shutil.move(str(item), target / item.name)


def write_private(path: str, text: str, mode: int = 0o640) -> None:
    """Create a file with the final mode from the start (no window with looser permissions)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(path, mode)


def read_conf_password(path: str) -> str | None:
    """db_password of an existing Odoo config (INI file, no interpolation), or None."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path)
        value = parser.get("options", "db_password", fallback="").strip()
    except (configparser.Error, OSError):
        return None
    return value if value and value.lower() != "false" else None


def tree_state(path: str, marker: str) -> str:
    """'missing' (nothing to keep), 'present' (marker file or .git found: keep it) or 'foreign' (non-empty, unknown)."""
    if not os.path.isdir(path) or not os.listdir(path):
        return "missing"
    if os.path.exists(os.path.join(path, marker)) or os.path.isdir(os.path.join(path, ".git")):
        return "present"
    return "foreign"


async def fetch_tree(report: Report, step: str, dest: str, marker: str, fetch: Callable[[], Awaitable[None]], label: str) -> None:
    state = tree_state(dest, marker)
    if state == "present":
        _emit(report, step, "output", f"{label}: {dest} already present, kept")
    elif state == "foreign":
        raise ProvisionError(f"{dest} is not empty and does not look like {label}. Move it away or empty it, then retry")
    else:
        await fetch()


def pip_overrides_args(spec: ProvisionSpec) -> list[str]:
    """`--override FILE` for the pins that cannot be built on current systems; the file lives in the install root."""
    pins = PIP_OVERRIDES.get(spec.python)
    if not pins:
        return []
    path = f"{spec.root}/.odp-pip-overrides.txt"
    Path(path).write_text("# Written by Odoo Dev Panel: replacement pins for packages that do not build on this system\n"
                          + "\n".join(pins) + "\n")
    os.chmod(path, 0o644)
    return ["--override", path]


def existing_requirements(spec: ProvisionSpec) -> list[str]:
    return [r for r in plan.requirements_files(spec) if os.path.isfile(r)]


def write_receipt(spec: ProvisionSpec, status: str, phase: str, ledger: dict | None = None) -> str:
    """``<root>/.odp-provision.json``. ``ledger``: phases completed, and what the root script created or changed.
    When the root folder is not writable (the root script failed before making it), the receipt goes to the
    dev user's state folder instead, so a failure is always recorded."""
    data = {
        "tool": "odoo-dev-panel", "status": status, "last_phase": phase,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **(ledger or {}),
        "spec": asdict(spec),
    }
    text = json.dumps(data, indent=2) + "\n"
    try:
        Path(spec.receipt_path).write_text(text)
        return spec.receipt_path
    except OSError:
        directory = paths.agent_state_dir() / "provision"
        directory.mkdir(parents=True, exist_ok=True)
        fallback = directory / f"{spec.run_as}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        fallback.write_text(text)
        return str(fallback)


def root_ledger_tee(ledger: dict, report: Report) -> Report:
    """Collect the root script's ODP: lines into ``ledger["root_script"]``; show only the human lines."""
    root = ledger.setdefault("root_script", {"last_step": None, "created": [], "changed": [], "exit_code": None})

    def tee(event: dict) -> None:
        text = event.get("text", "")
        if event.get("status") == "output" and text.startswith("ODP:"):
            kind, _, value = text[4:].partition(" ")
            if kind == "step":
                root["last_step"] = value
            elif kind in ("created", "changed"):
                root[kind].append(value)
            return
        report(event)
    return tee


async def provision(spec: ProvisionSpec, report: Report, root_runner: RootRunner = sudo_runner,
                    sec: plan.Secrets | None = None) -> None:
    """Run all phases. Raises ProvisionError on the first failure; the receipt then says `incomplete`."""
    sec = sec or plan.Secrets.generate()

    if os.path.exists(spec.conf_path):
        existing = read_conf_password(spec.conf_path)
        if not existing:
            _emit(report, "preflight", "fail", f"{spec.conf_path} exists but has no db_password. Add one or choose another config name.")
            raise ProvisionError("existing config has no db_password")
        sec.pg_password = existing

    _emit(report, "preflight", "start", "Preflight checks")
    checks = preflight.run_preflight(spec)
    for c in checks:
        _emit(report, "preflight", "output", f"{c.status.upper():4} {c.id}: {c.detail}")
    if preflight.has_failures(checks):
        _emit(report, "preflight", "fail", "Preflight failed. Nothing was changed.")
        raise ProvisionError("preflight failed")
    _emit(report, "preflight", "ok")

    phase = "root-script"
    ledger: dict = {"completed": []}

    def receipt(status: str, last: str) -> str:
        return write_receipt(spec, status, last, ledger)

    def done(name: str) -> None:
        ledger["completed"].append(name)
        receipt("incomplete", name)

    try:
        _emit(report, phase, "start", "Create user, directories, packages, PostgreSQL role and agent")
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "provision-root.sh"
            script.write_text(plan.render_root_script(spec, sec))
            script.chmod(0o600)
            code = await root_runner(str(script), root_ledger_tee(ledger, report))
        ledger.setdefault("root_script", {"last_step": None, "created": [], "changed": []})["exit_code"] = code
        if code != 0:
            raise ProvisionError(f"root script failed with exit code {code}")
        done(phase)
        _emit(report, phase, "ok")

        phase = "clone"
        _emit(report, phase, "start", "Fetch source trees")
        def clone(url: str, branch: str | None, dest: str):
            return lambda: run_local(phase, shlex.split(plan.clone_command(url, branch, dest)), report)

        odoo_dir = f"{spec.root}/odoo"
        await fetch_tree(report, phase, odoo_dir, "odoo-bin", clone(spec.odoo_git, spec.odoo_branch, odoo_dir), "Odoo community")
        ent_dir = f"{spec.root}/enterprise"
        if spec.enterprise_git:
            branch = spec.enterprise_branch or f"{spec.version}.0"
            await fetch_tree(report, phase, ent_dir, "web_enterprise", clone(spec.enterprise_git, branch, ent_dir), "Odoo enterprise")
        elif spec.enterprise_archive:
            async def extract() -> None:
                extract_archive(spec.enterprise_archive, ent_dir)
                _emit(report, phase, "output", f"extracted {spec.enterprise_archive}")
            await fetch_tree(report, phase, ent_dir, "web_enterprise", extract, "Odoo enterprise")
        for repo in spec.custom:
            dest = f"{spec.root}/custom/{repo.name}"
            await fetch_tree(report, phase, dest, "__manifest__.py", clone(repo.url, repo.branch, dest), f"custom repository {repo.name}")
        done(phase)
        _emit(report, phase, "ok")

        phase = "python"
        _emit(report, phase, "start", f"Python {spec.python}, virtual environment and dependencies (as {spec.run_as})")
        status = await client.wait_running(spec.run_as)
        if status["state"] != "running":
            raise ProvisionError(f"agent of {spec.run_as} is not running: {status.get('error', '')}")
        uv, venv = paths.uv_command(), f"{spec.root}/venv"
        uv_env = {"UV_PYTHON_INSTALL_DIR": spec.python_dir}
        conn = await client.connect(spec.run_as)
        try:
            await ensure_python(conn, "python", spec.python, spec.python_dir, spec.root, report)
            if os.path.exists(f"{venv}/bin/python"):
                code, _ = await _agent_session(
                    conn, "venv-check",
                    [f"{venv}/bin/python", "-c", "import sys; assert '%d.%d' % sys.version_info[:2] == sys.argv[1]", spec.python],
                    spec.root, report)
                healthy = code == 0
            else:
                healthy = False
            if healthy:
                _emit(report, "venv", "output", f"{venv} works with Python {spec.python}, kept")
            else:
                if os.path.lexists(venv):
                    old = f"{venv}.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                    _emit(report, "venv", "output", f"{venv} is broken or has the wrong Python: moved to {old}")
                    await run_in_agent(conn, "venv", ["/bin/mv", venv, old], spec.root, report)
                await run_in_agent(conn, "venv", [uv, "venv", "--python", spec.python, "--python-preference", "only-managed", venv],
                                   spec.root, report, uv_env)
            reqs = [arg for r in existing_requirements(spec) for arg in ("-r", r)]
            overrides = pip_overrides_args(spec)
            await run_in_agent(conn, "pip", [uv, "pip", "install", "--python", f"{venv}/bin/python", *overrides, *reqs, *PIP_EXTRA_PACKAGES],
                               spec.root, report, {**uv_env, "CFLAGS": BUILD_CFLAGS})
            done("pip")

            phase = "config"
            if os.path.exists(spec.conf_path):
                _emit(report, phase, "start", f"{spec.conf_path} exists")
                _emit(report, phase, "output", "kept as is")
            else:
                _emit(report, phase, "start", f"Write {spec.conf_path}")
                write_private(spec.conf_path, plan.render_conf(spec, sec))
            _emit(report, phase, "ok")

            phase = "verify"
            _emit(report, phase, "start", "Verify the installation")
            await run_in_agent(conn, "verify", [f"{venv}/bin/python", f"{spec.root}/odoo/odoo-bin", "--version"], spec.root, report)
        finally:
            await conn.close()
        await run_local("verify", ["psql", "-h", spec.pg_host, "-p", str(spec.pg_port), "-U", spec.run_as,
                                   "-d", "postgres", "-w", "-tAc", "select 1"], report, {"PGPASSWORD": sec.pg_password})
        ledger["completed"].append(phase)
        receipt("complete", phase)
        _emit(report, phase, "ok")
    except Exception as exc:
        try:
            where = receipt("incomplete", f"failed in {phase}")
            _emit(report, phase, "output", f"receipt: {where}")
        except OSError:
            pass
        _emit(report, phase, "fail", str(exc))
        if isinstance(exc, ProvisionError):
            raise
        raise ProvisionError(str(exc)) from exc
