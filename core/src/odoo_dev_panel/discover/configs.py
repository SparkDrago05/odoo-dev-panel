"""D3/D4: find Odoo config files, turn them into Instances and link them to installations."""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

from .model import Installation, Instance

CONFIG_ROOTS = ("/etc/odoo",)
EXTRA_CONFIGS = ("/etc/odoo.conf", "/etc/odoo-server.conf", "~/.odoorc")
# Options kept in the registry view. Anything else, and every secret, is dropped.
KEPT_OPTIONS = (
    "addons_path", "db_host", "db_port", "db_user", "db_name", "data_dir", "http_port", "xmlrpc_port",
    "gevent_port", "longpolling_port", "logfile", "workers", "dbfilter", "proxy_mode",
)
_VERSION_DIR = re.compile(r"(?:odoo|openerp)[-_]?(\d{1,2})(?:\.0)?$", re.I)


def parse_config(path: Path) -> dict[str, str] | None:
    """Return the [options] section, or None when the file is not an Odoo config."""
    parser = configparser.RawConfigParser(strict=False, interpolation=None, inline_comment_prefixes=(";",))
    try:
        with open(path, errors="replace") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("options"):
        return None
    return dict(parser.items("options"))


def split_addons_path(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in re.split(r"[,\n]", value) if p.strip()]


def find_config_files(extra_dirs: list[Path] | None = None, system: bool = True) -> list[Path]:
    files: list[Path] = []
    for root in CONFIG_ROOTS if system else ():
        base = Path(root)
        if base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).relative_to(base).parts)
                if depth >= 2:
                    dirnames[:] = []
                files += [Path(dirpath) / f for f in sorted(filenames) if f.endswith(".conf")]
    if system:
        files += [Path(os.path.expanduser(p)) for p in EXTRA_CONFIGS if Path(os.path.expanduser(p)).is_file()]
    for base in extra_dirs or []:
        for pattern in ("*.conf", "conf/*.conf", "config/*.conf"):
            files += sorted(base.glob(pattern))
    seen, unique = set(), []
    for f in files:
        key = os.path.realpath(f)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _under(path: str, base: str) -> bool:
    path, base = os.path.normpath(path), os.path.normpath(base)
    return path == base or path.startswith(base.rstrip("/") + "/")


def link_installation(addons: list[str], installs: list[Installation]) -> tuple[Installation | None, list[str]]:
    """Pick the installation that owns most addons_path entries. Core addons break ties."""
    problems: list[str] = []
    votes: dict[str, int] = {}
    for entry in addons:
        for inst in installs:
            if _under(entry, inst.root) or _under(entry, inst.source):
                weight = 3 if os.path.normpath(entry).startswith(os.path.normpath(inst.source) + "/") else 1
                votes[inst.root] = votes.get(inst.root, 0) + weight
                break
    if not votes:
        return None, problems
    best = max(votes, key=lambda r: (votes[r], r))
    if len(votes) > 1:
        problems.append("addons_path mixes installations: " + ", ".join(sorted(votes)))
    return next(i for i in installs if i.root == best), problems


def version_hint(path: Path) -> str | None:
    for part in reversed(path.parts[:-1]):
        match = _VERSION_DIR.match(part)
        if match:
            return f"{int(match.group(1))}.0"
    return None


def build_instances(files: list[Path], installs: list[Installation]) -> list[Instance]:
    result = []
    for path in files:
        raw = parse_config(path)
        if raw is None:
            continue
        addons = split_addons_path(raw.get("addons_path"))
        inst, problems = link_installation(addons, installs)
        link = "addons_path" if inst else None
        hint = version_hint(path)
        if inst is None and hint:
            # No addons_path hit: a config folder named after a version points to that installation.
            candidates = [i for i in installs if i.version == hint]
            if len(candidates) == 1:
                inst, link = candidates[0], "path"
        missing = [a for a in addons if not os.path.isdir(a)]
        if missing:
            shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
            problems.append(f"{len(missing)} addons_path entries missing: {shown}")
        if inst and inst.version and hint and hint != inst.version and link == "addons_path":
            problems.append(f"config folder says {hint} but installation is {inst.version}")
        if inst is None:
            problems.append("no installation found for this config")
        options = {k: v for k, v in raw.items() if k in KEPT_OPTIONS and "pass" not in k}
        result.append(Instance(
            path=str(path), name=path.stem, installation=inst.root if inst else None, link=link,
            options=options, problems=problems, version_hint=hint,
        ))
        if inst is not None:
            inst.configs.append(str(path))
            role = raw.get("db_user")
            if role and role != "False" and inst.pg_role is None:
                inst.pg_role = role
    return result
