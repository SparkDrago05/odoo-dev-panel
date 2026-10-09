"""How Git is started. One place builds every argv, so plans show exactly what runs."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

from . import urls

TIMEOUT = 10          # seconds for read-only inspection commands
TAIL_LINES = 40       # output lines kept per repository in results

# Never prompt on a terminal the app does not have; stable English messages for the explanations.
# The developer's SSH agent, known_hosts and credential helpers stay in effect.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

Report = Callable[[dict], None]


@dataclass
class GitResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def env() -> dict[str, str]:
    return {**os.environ, **GIT_ENV}


def argv(repo: str, *args: str, foreign: bool = False, read_only: bool = True) -> list[str]:
    """``git -C repo ...``. For a repository owned by someone else, a read-only command trusts exactly that
    folder for this one call (``-c safe.directory``); nothing is written to a Git config file."""
    cmd = ["git"]
    if foreign:
        if not read_only:
            raise ValueError("write operations never bypass Git's ownership check")
        cmd += ["-c", f"safe.directory={repo}"]
    if read_only:
        cmd.append("--no-optional-locks")
    return [*cmd, "-C", repo, *args]


def clone_argv(url: str, dest: str, ref: str | None = None, shallow: bool = True, tags: bool = False) -> list[str]:
    cmd = ["git", "clone"]
    if shallow:
        cmd += ["--depth=1", "--single-branch"]
        if not tags:
            cmd.append("--no-tags")
    if ref:
        cmd += ["--branch", ref]
    return [*cmd, "--", url, dest]


def display(cmd: list[str]) -> str:
    """Shell text for a plan or the copy button. URLs are redacted, so a stray token is never shown."""
    return shlex.join(urls.redact(part) if "://" in part else part for part in cmd)


def run(cmd: list[str], timeout: float = TIMEOUT, cwd: str | None = None) -> GitResult:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env(), cwd=cwd)
    except subprocess.TimeoutExpired:
        return GitResult(124, "", f"timed out after {timeout:g}s")
    except OSError as exc:
        return GitResult(127, "", str(exc))
    return GitResult(proc.returncode, proc.stdout, proc.stderr)


async def stream(cmd: list[str], report: Report, step: str, cwd: str | None = None) -> GitResult:
    """Run a long command (clone, fetch, pull) and send each output line as a step event."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env(), cwd=cwd,
        )
    except OSError as exc:
        return GitResult(127, "", str(exc))
    lines: list[str] = []
    assert proc.stdout is not None
    async for raw in proc.stdout:
        for line in raw.decode(errors="replace").replace("\r", "\n").splitlines():
            if line.strip():
                lines.append(line)
                report({"step": step, "status": "output", "text": line})
    code = await proc.wait()
    tail = "\n".join(lines[-TAIL_LINES:])
    return GitResult(code, tail, tail)
