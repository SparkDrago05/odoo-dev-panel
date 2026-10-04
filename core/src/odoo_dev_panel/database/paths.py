"""Names and paths. Pure functions: no I/O except ``tree_size``."""

from __future__ import annotations

import os
import re

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
RESERVED = ("postgres", "template0", "template1")


def name_error(name: str) -> str | None:
    """Why ``name`` cannot be a database name made by this tool, or None. Stricter than PostgreSQL on purpose:
    the name ends up in a filesystem path and in recipes."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return "use letters, digits and underscore, starting with a letter or underscore (63 characters at most)"
    if name.lower() in RESERVED:
        return f"{name} is reserved"
    return None


def filestore_dir(base: str | None, database: str) -> str | None:
    return os.path.join(base, database) if base else None


def trash_dir(base: str, database: str, stamp: str) -> str:
    return os.path.join(base, f".trash-{database}-{stamp}")


def backup_dir(dest: str, database: str, stamp: str) -> str:
    return os.path.join(dest, f"{database}-{stamp}")


def default_dest(home: str | None) -> str | None:
    """Backups go where the run-as user can write: its own home."""
    return os.path.join(home, "odp-backups") if home else None


SNAPSHOT_STAMP = re.compile(r"\d{8}-\d{6}")
SNAPSHOT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}-\d{8}-\d{6}")


def snapshot_root(home: str | None) -> str | None:
    """Snapshots are backups in their own folder, so pruning never touches a manual backup."""
    return os.path.join(home, "odp-backups", "snapshots") if home else None


def is_snapshot_of(path: str, database: str) -> bool:
    """``path`` is named like a snapshot of ``database``: ``<database>-YYYYMMDD-HHMMSS``."""
    name = os.path.basename(os.path.normpath(path))
    return name.startswith(database + "-") and bool(SNAPSHOT_STAMP.fullmatch(name[len(database) + 1:]))


def aside_name(database: str, stamp: str) -> str:
    """Name a database is renamed to when a snapshot replaces it: ``<db>_before_<stamp>``, 63 characters at most."""
    suffix = "_before_" + stamp.replace("-", "_")
    return database[:63 - len(suffix)] + suffix


def tree_size(path: str) -> int | None:
    """Bytes under ``path``; None when it cannot be read completely."""
    total, failed = 0, []
    for dirpath, _dirs, files in os.walk(path, onerror=failed.append):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                failed.append(f)
    return None if failed else total
