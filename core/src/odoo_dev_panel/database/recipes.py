"""Neutralization recipes: SQL files with a ``{{database}}`` placeholder."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .paths import name_error

DEFAULT = "default"
DEFAULT_SQL = """\\set ON_ERROR_STOP on

DO $safety$
BEGIN
    IF current_database() <> '{{database}}' THEN
        RAISE EXCEPTION 'Wrong database: connected to %, expected {{database}}', current_database();
    END IF;
END
$safety$;

BEGIN;
UPDATE res_users SET password = 'admin' WHERE password IS NOT NULL;
DO $neutralize$
DECLARE t text;
BEGIN
    -- tables that exist in some Odoo versions only are skipped when missing
    FOREACH t IN ARRAY ARRAY['ir_cron', 'ir_mail_server', 'fetchmail_server'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('UPDATE %I SET active = false WHERE active', t);
        END IF;
    END LOOP;
END
$neutralize$;
COMMIT;
"""


def recipes_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(os.environ.get("ODP_RECIPES_DIR") or os.path.join(base, "odoo-dev-panel", "recipes"))


def available(directory: Path | None = None) -> list[str]:
    directory = directory or recipes_dir()
    found = sorted(p.stem for p in directory.glob("*.sql")) if directory.is_dir() else []
    return [DEFAULT] + [n for n in found if n != DEFAULT]


def load(name: str, directory: Path | None = None) -> str:
    if name == DEFAULT:
        return DEFAULT_SQL
    if "/" in name or name.startswith("."):
        raise ValueError(f"bad recipe name {name!r}")
    return ((directory or recipes_dir()) / f"{name}.sql").read_text()


def render(sql: str, database: str) -> str:
    error = name_error(database)
    if error:
        raise ValueError(f"database name {database!r}: {error}")
    return sql.replace("{{database}}", database)


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:12]
