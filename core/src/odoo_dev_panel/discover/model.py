"""Plain data records returned by discovery. All fields are JSON-friendly."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Installation:
    root: str  # directory that holds the source tree, venv and custom repos
    source: str  # directory with odoo-bin
    version: str | None  # "19.0"; None when release.py cannot be parsed
    owner: str | None  # OS user that owns the source tree
    venv: str | None = None
    venv_python: str | None = None
    venv_ok: bool | None = None  # None: no venv; False: interpreter missing, or another Python than the packages
    venv_problem: str | None = None
    python_version: str | None = None  # Python that bin/python runs today
    venv_built_for: str | None = None  # Python written in pyvenv.cfg
    pg_role: str | None = None  # filled in by linking, from configs
    configs: list[str] = field(default_factory=list)
    home: str | None = None  # home directory of owner, used for the filestore


@dataclass
class Instance:
    path: str
    name: str
    installation: str | None  # Installation.root, or None for an orphan
    link: str | None  # "addons_path", "path" or None
    options: dict[str, str] = field(default_factory=dict)  # secrets are never stored
    problems: list[str] = field(default_factory=list)
    version_hint: str | None = None  # from the config folder name, for orphans
