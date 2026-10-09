"""Q4: debug presets. One JSON file, ``~/.config/odoo-dev-panel/debug-presets.json``, a list of presets.

A preset names an instance (its config), what to run (the server, optionally with -u/-i, or module tests in a
throwaway database), and a stable debugpy port, so the IDE's attach entry for it never changes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from ..provision import profiles

FIRST_PORT = 5678
KINDS = ("server", "test")
DEV_FLAGS = ("all", "reload", "qweb", "werkzeug", "xml", "pdb")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_DB = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,62}$")
_MODULE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
_TAGS = re.compile(r"^[A-Za-z0-9_,:/.\-+*]{1,300}$")
FIELDS = ("id", "name", "instance", "kind", "database", "http_port", "dev", "update", "install", "modules", "tags",
          "demo", "port", "wait")


class PresetError(ValueError):
    pass


def presets_file() -> Path:
    return profiles.config_dir() / "debug-presets.json"


def load(file: Path | None = None) -> list[dict]:
    try:
        data = json.loads((file or presets_file()).read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise PresetError(f"cannot read {file or presets_file()}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("presets"), list):
        raise PresetError(f"{file or presets_file()} is not a preset file")
    return [p for p in data["presets"] if isinstance(p, dict)]


def _store(rows: list[dict], file: Path | None = None) -> None:
    file = file or presets_file()
    profiles._write(file, json.dumps({"version": 1, "presets": rows}, indent=2) + "\n")


def get(preset_id: str, file: Path | None = None) -> dict:
    found = next((p for p in load(file) if p.get("id") == preset_id), None)
    if found is None:
        raise LookupError(f"no debug preset {preset_id}")
    return found


def _names(value, label: str, pattern=_MODULE) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PresetError(f"{label} is a list of names")
    for v in value:
        if pattern is not None and not pattern.match(v):
            raise PresetError(f"bad {label} name {v!r}")
    return list(dict.fromkeys(value))


def check(raw: dict) -> dict:
    """A clean preset from user input. Unknown keys are refused."""
    if not isinstance(raw, dict):
        raise PresetError("a preset is an object")
    for key in raw:
        if key not in FIELDS:
            raise PresetError(f"unknown field {key!r}")
    p = {"id": raw.get("id"), "name": raw.get("name") or raw.get("id"), "instance": raw.get("instance"),
         "kind": raw.get("kind") or "server", "database": raw.get("database") or None,
         "http_port": raw.get("http_port"), "dev": _names(raw.get("dev"), "dev", None),
         "update": _names(raw.get("update"), "update"), "install": _names(raw.get("install"), "install"),
         "modules": _names(raw.get("modules"), "modules"), "tags": raw.get("tags") or None,
         "demo": raw.get("demo", True) is not False, "port": raw.get("port"), "wait": bool(raw.get("wait"))}
    if not isinstance(p["id"], str) or not _ID.match(p["id"]):
        raise PresetError("id: lower case letters, digits, - and _ (up to 41)")
    if not isinstance(p["name"], str) or not p["name"].strip() or len(p["name"]) > 80:
        raise PresetError("name: up to 80 characters")
    if not isinstance(p["instance"], str) or not p["instance"].startswith("/"):
        raise PresetError("instance is the absolute path of an Odoo config")
    p["instance"] = os.path.normpath(p["instance"])
    if p["kind"] not in KINDS:
        raise PresetError(f"kind is one of {', '.join(KINDS)}")
    if p["database"] is not None and (not isinstance(p["database"], str) or not _DB.match(p["database"])):
        raise PresetError(f"bad database name {p['database']!r}")
    for flag in p["dev"]:
        if flag not in DEV_FLAGS:
            raise PresetError(f"unknown --dev flag {flag}")
    for key in ("http_port", "port"):
        v = p[key]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1024 <= v <= 65535):
            raise PresetError(f"{key} is a number from 1024 to 65535")
    if p["port"] is None:
        raise PresetError("port is required (the debugpy port)")
    if p["http_port"] is not None and p["http_port"] == p["port"]:
        raise PresetError("the HTTP port and the debug port must differ")
    if p["kind"] == "server":
        if (p["update"] or p["install"]) and not p["database"]:
            raise PresetError("-u and -i need a database")
        p["modules"], p["tags"] = [], None
    else:
        if not p["modules"]:
            raise PresetError("a test preset needs modules")
        if p["tags"] is not None and (not isinstance(p["tags"], str) or not _TAGS.match(p["tags"])):
            raise PresetError("test tags look like /module, :TestClass.test_method, -slow, post_install")
        p["database"], p["update"], p["install"], p["dev"], p["http_port"] = None, [], [], [], None
    return p


def save(raw: dict, overwrite: bool = False, file: Path | None = None) -> dict:
    p = check(raw)
    rows = load(file)
    if any(r.get("id") == p["id"] for r in rows) and not overwrite:
        raise PresetError(f"preset {p['id']} exists")
    clash = next((r for r in rows if r.get("port") == p["port"] and r.get("id") != p["id"]), None)
    if clash:
        raise PresetError(f"port {p['port']} is used by preset {clash['id']}")
    rows = [r for r in rows if r.get("id") != p["id"]] + [p]
    _store(rows, file)
    return p


def delete(preset_id: str, file: Path | None = None) -> bool:
    """Remove a preset; the file before the change is kept as ``debug-presets.json.bak-TIME``."""
    file = file or presets_file()
    rows = load(file)
    if not any(r.get("id") == preset_id for r in rows):
        return False
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.replace(file, file.with_name(f"{file.name}.bak-{stamp}"))
    _store([r for r in rows if r.get("id") != preset_id], file)
    return True


def next_port(busy: set[int], file: Path | None = None) -> int:
    """First port from 5678 that no preset uses and nothing listens on."""
    used = {r.get("port") for r in load(file)} | set(busy)
    port = FIRST_PORT
    while port in used:
        port += 1
    return port
