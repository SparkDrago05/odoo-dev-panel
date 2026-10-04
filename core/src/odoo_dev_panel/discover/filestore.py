"""D6: where Odoo keeps attachments for a database, given the OS user that runs it."""

from __future__ import annotations

import os


def filestore_base(options: dict[str, str], home: str | None) -> str | None:
    """data_dir from the config, else the default of the run-as user."""
    data_dir = options.get("data_dir")
    if data_dir and data_dir != "False":
        return os.path.join(data_dir, "filestore")
    if home:
        return os.path.join(home, ".local", "share", "Odoo", "filestore")
    return None


def filestore_path(base: str | None, database: str) -> str | None:
    return os.path.join(base, database) if base else None
