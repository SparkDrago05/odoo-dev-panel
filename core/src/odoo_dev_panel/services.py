"""Odoo systemd services: start, stop, restart, enable, disable, journal, unit file.

Only units found by discovery (a ``.service`` whose ExecStart runs Odoo) are accepted. Any other name is
refused, so this never becomes a general systemd manager.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from .discover import units as units_mod

ACTIONS = ("start", "stop", "restart", "enable", "disable")
MAX_LINES = 5000


class ServiceError(Exception):
    pass


def list_services(dirs: tuple[str, ...] = units_mod.UNIT_DIRS) -> list[dict]:
    found = units_mod.find_units(dirs)
    units_mod.add_states(found)
    enabled = _enabled_states([u.name for u in found])
    out = []
    for u in found:
        row = u.__dict__.copy()
        row["enabled"] = enabled.get(u.name)
        out.append(row)
    return out


def find_unit(name: str, dirs: tuple[str, ...] = units_mod.UNIT_DIRS) -> units_mod.OdooUnit:
    """The discovered Odoo unit called ``name``; ServiceError for anything else."""
    for unit in units_mod.find_units(dirs):
        if unit.name == name:
            return unit
    raise ServiceError(f"{name} is not an Odoo systemd unit known to Odoo Dev Panel")


def _enabled_states(names: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        try:
            out = subprocess.run(["systemctl", "is-enabled", name], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        result[name] = (out.stdout.strip() or out.stderr.strip() or "unknown").splitlines()[0]
    return result


def action_command(action: str, name: str, askpass: bool = False) -> list[str]:
    """The sudo command for one action. ``name`` must already be checked with ``find_unit``."""
    if action not in ACTIONS:
        raise ServiceError(f"unknown action {action!r}; use one of {', '.join(ACTIONS)}")
    return ["sudo", *(["-A"] if askpass else []), "--", "systemctl", action, name]


def journal_command(name: str, lines: int = 200, since: str | None = None, sudo: bool = False, askpass: bool = False) -> list[str]:
    lines = max(1, min(int(lines), MAX_LINES))
    cmd = ["journalctl", "-u", name, "-n", str(lines), "--no-pager", "-o", "short-iso"]
    if since:
        cmd += ["--since", since]
    if sudo:
        return ["sudo", *(["-A"] if askpass else []), "--", *cmd]
    return cmd


def read_unit(name: str, dirs: tuple[str, ...] = units_mod.UNIT_DIRS) -> dict:
    unit = find_unit(name, dirs)
    try:
        text = Path(unit.path).read_text(errors="replace")
    except OSError as exc:
        raise ServiceError(f"cannot read {unit.path}: {exc}") from exc
    return {"name": unit.name, "path": unit.path, "text": text}


def journal(name: str, lines: int = 200, since: str | None = None) -> str:
    """Read the unit's journal. Tries without privilege first (members of systemd-journal), then through sudo."""
    find_unit(name)
    plain = subprocess.run(journal_command(name, lines, since), capture_output=True, text=True, timeout=30)
    if plain.returncode == 0 and "No journal files were opened" not in plain.stderr:
        return plain.stdout
    elevated = subprocess.run(journal_command(name, lines, since, sudo=True), capture_output=True, text=True, timeout=60)
    if elevated.returncode != 0:
        raise ServiceError(elevated.stderr.strip() or f"journalctl exit {elevated.returncode}")
    return elevated.stdout


def run_action(action: str, name: str) -> str:
    """CLI path: sudo prompts on the terminal."""
    find_unit(name)
    result = subprocess.run(action_command(action, name), capture_output=True, text=True)
    if result.returncode != 0:
        raise ServiceError((result.stderr or result.stdout).strip() or f"systemctl exit {result.returncode}")
    return (result.stdout + result.stderr).strip()


async def run_action_askpass(action: str, name: str, askpass_env: dict[str, str]) -> str:
    """GUI path: sudo asks through the askpass helper."""
    from . import privilege

    find_unit(name)
    code, output = await privilege._sudo_askpass(action_command(action, name, askpass=True), askpass_env)
    if code != 0:
        raise ServiceError(output or f"systemctl exit {code}")
    return output


async def journal_async(name: str, lines: int = 200, since: str | None = None) -> str:
    return await asyncio.to_thread(journal, name, lines, since)
