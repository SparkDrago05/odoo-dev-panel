"""Run all discovery steps and return one JSON-friendly snapshot."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from . import configs, databases, installs, ports, processes, registry, units


def scan(
    roots: list[Path] | None = None, with_databases: bool = True, proc_root: str = "/proc", registry_file: Path | None = None,
    system: bool = True,
) -> dict:
    """``system=False`` ignores /etc/odoo, ~/.odoorc and systemd units: used by tests on fixture trees."""
    unreadable: list[str] = []
    found = installs.scan_installations(roots, unreadable=unreadable)
    files = configs.find_config_files([Path(i.root) for i in found], system)
    instances = configs.build_instances(files, found)
    procs = processes.discover_processes(instances, found, proc_root)
    unit_list = units.find_units() if system else []
    units.add_states(unit_list)
    for unit in unit_list:
        for proc in procs:
            if proc.config and unit.config and Path(proc.config).resolve() == Path(unit.config).resolve():
                proc.unit = unit.name
    snapshot = {
        "installations": [asdict(i) for i in found],
        "instances": [asdict(i) for i in instances],
        "databases": databases.discover_databases(found, instances) if with_databases else [],
        "processes": [asdict(p) for p in procs],
        "units": [asdict(u) for u in unit_list],
        "ports": ports.analyse(procs, proc_root),
        "unreadable": sorted(unreadable),
    }
    try:
        data = registry.load(registry_file)
    except registry.RegistryError as exc:
        data, snapshot["registry_error"] = registry._empty(), str(exc)
    registry.annotate(snapshot, data)
    return snapshot
