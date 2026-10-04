"""D9: systemd units whose ExecStart runs Odoo."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .processes import _flag, is_odoo_argv

UNIT_DIRS = ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system")


@dataclass
class OdooUnit:
    name: str
    path: str
    user: str | None
    config: str | None
    exec_start: str
    active_state: str | None = None
    sub_state: str | None = None
    result: str | None = None


def _parse_unit(path: Path) -> OdooUnit | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    user, exec_start = None, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("User="):
            user = line[5:].strip() or None
        elif line.startswith("ExecStart=") and exec_start is None:
            exec_start = line[len("ExecStart="):].lstrip("-@+!: ").strip()
    if not exec_start:
        return None
    try:
        argv = shlex.split(exec_start)
    except ValueError:
        argv = exec_start.split()
    if not is_odoo_argv(argv):
        return None
    return OdooUnit(name=path.name, path=str(path), user=user, config=_flag(argv, "-c", "--config"), exec_start=exec_start)


def find_units(dirs: tuple[str, ...] = UNIT_DIRS) -> list[OdooUnit]:
    units: dict[str, OdooUnit] = {}
    for d in dirs:
        base = Path(d)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.service")):
            if path.name in units:  # earlier directories take precedence
                continue
            unit = _parse_unit(path)
            if unit:
                units[path.name] = unit
    return list(units.values())


def add_states(units: list[OdooUnit]) -> None:
    if not units:
        return
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "Id,ActiveState,SubState,Result", *[u.name for u in units]],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return
    by_name = {u.name: u for u in units}
    for block in out.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = by_name.get(fields.get("Id", ""))
        if unit:
            unit.active_state, unit.sub_state, unit.result = (
                fields.get("ActiveState"), fields.get("SubState"), fields.get("Result"),
            )
