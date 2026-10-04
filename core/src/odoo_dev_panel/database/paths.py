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
