"""Starting an agent as another OS user through sudo.

sudo (both sudo-rs and classic sudo) caches credentials per terminal session, so a
GUI cannot rely on the cache between runs. That is why the panel elevates only once
per user: it starts a long-lived agent, and every later operation goes through the
agent socket without sudo.

Lifetime: a process started from the desktop app lives in the app's systemd user scope
(``user@UID.service/app.slice/app-*.scope``). Logging out stops that scope and kills every
process in it, including the agent and the Odoo processes under it. So where systemd is
available the agent is started as its own *system* service (``systemd-run``, run by root).
``KillMode=process`` means that ending the agent never ends the Odoo processes it started.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import subprocess
import sys

from . import client, paths


def systemd_available() -> bool:
    return os.path.isdir("/run/systemd/system") and shutil.which("systemd-run") is not None


def agent_serve_args(socket_dir: str | None = None, foreground: bool = False) -> list[str]:
    args = [*paths.odp_command(), "agent", "serve"]
    if foreground:
        args.append("--foreground")
    return [*args, "--socket-dir", str(socket_dir or paths.socket_dir())]


def systemd_run_args(user: str, socket_dir: str | None = None) -> list[str]:
    """Command (run as root) that starts the agent of ``user`` as a transient system service.

    The unit name is unique per start: an old unit that still holds running Odoo processes
    stays loaded, and a fixed name would make the next start fail.
    """
    unit = f"odp-agent-{user}-{secrets.token_hex(3)}"
    return [
        shutil.which("systemd-run") or "/usr/bin/systemd-run",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        f"--uid={user}",
        f"--description=Odoo Dev Panel agent ({user})",
        "--property=KillMode=process",
        "--",
        *agent_serve_args(socket_dir, foreground=True),  # systemd supervises it: no double fork
    ]


def agent_command(user: str, askpass: bool, use_systemd: bool | None = None) -> list[str]:
    if use_systemd is None:
        use_systemd = systemd_available()
    cmd = ["sudo"]
    if askpass:
        cmd.append("-A")
    if use_systemd:
        return [*cmd, "--", *systemd_run_args(user)]
    return [*cmd, "-u", user, "--", *agent_serve_args()]


def start_agent_interactive(user: str) -> int:
    """CLI path: sudo prompts on the terminal."""
    code = subprocess.call(agent_command(user, askpass=False))
    if code != 0:
        return code
    status = asyncio.run(client.wait_running(user))
    if status["state"] != "running":
        print(f"agent for {user} did not start: {status.get('error', '')}", file=sys.stderr)
        return 1
    print(f"agent started: {status['info']['pid']} {status['info']['socket']}")
    return 0


def enable_command(user: str, askpass: bool) -> list[str]:
    """Add a version user to the odoo-dev group, so its agent can hand its socket to that group."""
    usermod = shutil.which("usermod") or "/usr/sbin/usermod"
    return ["sudo", *(["-A"] if askpass else []), "--", usermod, "-aG", paths.DEFAULT_GROUP, user]


async def start_agent_askpass(user: str, askpass_env: dict[str, str]) -> tuple[int, str]:
    """GUI path: sudo asks through the askpass helper. Returns (exit code, combined output)."""
    return await _sudo_askpass(agent_command(user, askpass=True), askpass_env)


async def enable_user_askpass(user: str, askpass_env: dict[str, str]) -> tuple[int, str]:
    return await _sudo_askpass(enable_command(user, askpass=True), askpass_env)


async def _sudo_askpass(cmd: list[str], askpass_env: dict[str, str]) -> tuple[int, str]:
    env = dict(os.environ)
    env.update(askpass_env)
    env["SUDO_ASKPASS"] = paths.askpass_command()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    output, _ = await proc.communicate()
    return proc.returncode, output.decode(errors="replace").strip()
