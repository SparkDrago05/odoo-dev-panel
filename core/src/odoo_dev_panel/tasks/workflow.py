"""K1: workflow files. A workflow is a TOML file of typed steps with named parameters.

    name = "Safe upgrade"
    description = "Snapshot the database, then upgrade the modules"

    [params.config]
    kind = "config"            # installation, config, database, new_database, modules, text
    description = "Odoo config of the instance"

    [[steps]]
    op = "db.snapshot"
    database = "{database}"

``{name}`` is replaced by a parameter value in text fields; a list field set to exactly ``"{modules}"`` takes the
list. With one config parameter, ``{installation}`` is its installation unless declared. Steps never hold
passwords; a command step is an argument list, never a shell line.

Files: ``~/.config/odoo-dev-panel/workflows/<name>.toml``. Built-in recipes are read-only and listed alongside.
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

from ..provision import profiles
from . import builtin, steps

TOP_KEYS = ("name", "description", "params", "steps")
PARAM_KEYS = ("kind", "description", "default")
PARAM_KINDS = ("installation", "config", "database", "new_database", "modules", "text")
COMMON_KEYS = ("op", "title", "auto", "confirm")
MAX_STEPS = 30
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_DBNAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,62}$")
_MODULE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")


class WorkflowError(ValueError):
    pass


# -- locations -------------------------------------------------------------------

def workflows_dir() -> Path:
    return profiles.config_dir() / "workflows"


def workflow_path(name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise WorkflowError(f"bad workflow name {name!r}: letters, digits, _ . - (up to 61)")
    return workflows_dir() / f"{name}.toml"


# -- parsing and checking --------------------------------------------------------

def parse(text: str, source: str) -> dict:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise WorkflowError(f"{source}: {exc}") from exc
    check(data, source)
    return data


def _placeholders(value) -> list[str]:
    if isinstance(value, str):
        return _PLACEHOLDER.findall(value)
    if isinstance(value, list):
        return [n for v in value for n in _placeholders(v)]
    return []


def known_names(data: dict) -> set[str]:
    params = data.get("params", {})
    names = set(params)
    if "installation" not in names and sum(1 for p in params.values() if p.get("kind") == "config") == 1:
        names.add("installation")
    return names


def check(data: dict, source: str) -> None:
    """Shape, types and placeholders. Values are checked when a run is planned."""
    def bad(msg: str):
        raise WorkflowError(f"{source}: {msg}")

    for key in data:
        if key not in TOP_KEYS:
            bad(f"unknown key {key!r} (allowed: {', '.join(TOP_KEYS)})")
    for key in ("name", "description"):
        if key in data and not isinstance(data[key], str):
            bad(f"{key} must be text")
    params = data.get("params", {})
    if not isinstance(params, dict):
        bad("[params] must be a table")
    for name, spec in params.items():
        if not _PARAM.match(name):
            bad(f"bad parameter name {name!r}: lower case letters, digits and _")
        if not isinstance(spec, dict):
            bad(f"[params.{name}] must be a table")
        for key in spec:
            if key not in PARAM_KEYS:
                bad(f"[params.{name}] unknown key {key!r}")
        if spec.get("kind") not in PARAM_KINDS:
            bad(f"[params.{name}] kind must be one of {', '.join(PARAM_KINDS)}")
        if "description" in spec and not isinstance(spec["description"], str):
            bad(f"[params.{name}] description must be text")
        if "default" in spec:
            try:
                check_value(spec["kind"], spec["default"])
            except WorkflowError as exc:
                bad(f"[params.{name}] default: {exc}")
    rows = data.get("steps")
    if not isinstance(rows, list) or not rows or not all(isinstance(s, dict) for s in rows):
        bad("steps must be a non-empty array of tables ([[steps]])")
    if len(rows) > MAX_STEPS:
        bad(f"at most {MAX_STEPS} steps")
    names = known_names(data)
    for n, step in enumerate(rows, 1):
        op = step.get("op")
        if op not in steps.OPS:
            bad(f"step {n}: op must be one of {', '.join(steps.OPS)}")
        fields = steps.OPS[op].fields
        for key, value in step.items():
            if key in COMMON_KEYS:
                want = str if key in ("op", "title") else bool
                if not isinstance(value, want):
                    bad(f"step {n}: {key} has the wrong type")
                continue
            if key not in fields:
                bad(f"step {n} ({op}): unknown key {key!r} (allowed: {', '.join(fields) or 'none'})")
            kind = fields[key]
            ok = {"text": isinstance(value, str),
                  "list": isinstance(value, list) and all(isinstance(v, str) for v in value)
                  or isinstance(value, str) and value.startswith("{") and value.endswith("}"),
                  "bool": isinstance(value, bool),
                  "int": isinstance(value, int) and not isinstance(value, bool)}[kind]
            if not ok:
                bad(f"step {n} ({op}): {key} must be {'a list of text' if kind == 'list' else kind}")
        for key in steps.OPS[op].required:
            if key not in step:
                bad(f"step {n} ({op}): {key} is required")
        for name in _placeholders(list(step.values())):
            if name not in names:
                bad(f"step {n}: unknown parameter {{{name}}}")
        if op == "command" and step.get("as", "me") not in ("me", "run-as"):
            bad(f"step {n} (command): as is \"me\" or \"run-as\"")
        if op == "command" and not (isinstance(step.get("argv"), list) and step["argv"]):
            bad(f"step {n} (command): argv is a non-empty list such as [\"git\", \"status\"]")


def check_value(kind: str, value):
    """Shape of one parameter value. Returns the normalized value (modules: a list)."""
    if kind == "modules":
        if isinstance(value, str):
            value = [m.strip() for m in value.split(",") if m.strip()]
        if not isinstance(value, list) or not value or len(value) > 100:
            raise WorkflowError("a list of 1 to 100 module names")
        for m in value:
            if not isinstance(m, str) or not _MODULE.match(m):
                raise WorkflowError(f"bad module name {m!r}")
        return list(dict.fromkeys(value))
    if not isinstance(value, str) or not value:
        raise WorkflowError("must be text")
    if kind in ("database", "new_database") and not _DBNAME.match(value):
        raise WorkflowError(f"bad database name {value!r}")
    if kind in ("installation", "config") and not value.startswith("/"):
        raise WorkflowError("must be an absolute path")
    if kind == "text" and (len(value) > 200 or any(ord(c) < 32 for c in value)):
        raise WorkflowError("one line of at most 200 characters")
    return os.path.normpath(value) if kind in ("installation", "config") else value


# -- listing, reading, saving ----------------------------------------------------

def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load(name: str) -> dict:
    """{name, source, path, text, data, digest}. Saved files come before built-ins of the same name."""
    path = workflow_path(name)
    if path.is_file():
        try:
            text = path.read_text()
        except OSError as exc:
            raise WorkflowError(f"cannot read {path}: {exc.strerror}") from exc
        return {"name": name, "source": "saved", "path": str(path), "text": text, "data": parse(text, str(path)),
                "digest": digest(text)}
    if name in builtin.RECIPES:
        text = builtin.RECIPES[name]
        return {"name": name, "source": "built-in", "path": None, "text": text, "data": parse(text, f"built-in {name}"),
                "digest": digest(text)}
    raise LookupError(f"no workflow {name}")


def _row(name: str, source: str, path: str | None, text: str) -> dict:
    row = {"name": name, "source": source, "path": path, "title": None, "description": None, "steps": 0,
           "params": [], "error": None}
    try:
        data = parse(text, path or f"built-in {name}")
        row.update(title=data.get("name"), description=data.get("description"), steps=len(data["steps"]),
                   params=list(data.get("params", {})))
    except WorkflowError as exc:
        row["error"] = str(exc)
    return row


def list_workflows() -> list[dict]:
    out = {}
    for name, text in builtin.RECIPES.items():
        out[name] = _row(name, "built-in", None, text)
    folder = workflows_dir()
    for file in sorted(f for f in folder.glob("*.toml") if not f.name.startswith(".")) if folder.is_dir() else []:
        try:
            text = file.read_text()
        except OSError as exc:
            out[file.stem] = {**_row(file.stem, "saved", str(file), ""), "error": f"cannot read: {exc.strerror}"}
            continue
        out[file.stem] = _row(file.stem, "saved", str(file), text)
    return sorted(out.values(), key=lambda r: (r["source"] != "saved", r["name"]))


def save(name: str, text: str, overwrite: bool = False) -> str:
    """Check the text and write it as is (comments stay). Built-in names are kept for the recipes."""
    path = workflow_path(name)
    if name in builtin.RECIPES:
        raise WorkflowError(f"{name} is a built-in recipe; save the copy under another name")
    if not isinstance(text, str) or len(text) > 64 * 1024:
        raise WorkflowError("the workflow text is missing or larger than 64 KB")
    parse(text, name)
    if path.exists() and not overwrite:
        raise WorkflowError(f"workflow {name} exists; choose another name or overwrite it")
    profiles._write(path, text)
    return str(path)


def delete(name: str) -> str | None:
    """Move a saved workflow aside to ``.trash-<name>-<time>.toml`` (never deleted). Returns where."""
    path = workflow_path(name)
    if not path.exists():
        return None
    aside = path.with_name(f".trash-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.toml")
    os.replace(path, aside)
    return str(aside)


# -- parameter values ------------------------------------------------------------

def bind(data: dict, given: dict | None, snapshot: dict) -> dict:
    """Parameter values for one run: given, else default; checked against discovery. Adds the derived installation."""
    given = given or {}
    if not isinstance(given, dict):
        raise WorkflowError("params is an object")
    params = data.get("params", {})
    for key in given:
        if key not in params:
            raise WorkflowError(f"unknown parameter {key}")
    values = {}
    for name, spec in params.items():
        raw = given.get(name)
        if raw in (None, "", []):
            raw = spec.get("default")
        if raw in (None, "", []):
            raise WorkflowError(f"parameter {name} is required")
        try:
            values[name] = check_value(spec["kind"], raw)
        except WorkflowError as exc:
            raise WorkflowError(f"{name}: {exc}") from exc
        kind = spec["kind"]
        if kind == "installation" and not any(i["root"] == values[name] for i in snapshot["installations"]):
            raise WorkflowError(f"{name}: {values[name]} is not a discovered Odoo installation")
        if kind == "config":
            inst = next((i for i in snapshot["instances"] if i["path"] == values[name]), None)
            if inst is None:
                raise WorkflowError(f"{name}: {values[name]} is not a discovered Odoo config")
            if not inst.get("installation"):
                raise WorkflowError(f"{name}: {values[name]} is not linked to an installation")
    if "installation" in known_names(data) and "installation" not in params:
        config = next(values[n] for n, s in params.items() if s["kind"] == "config")
        values["installation"] = next(i["installation"] for i in snapshot["instances"] if i["path"] == config)
    return values


def fill(value, values: dict):
    """Replace placeholders in one step field."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and isinstance(values.get(whole.group(1)), list):
            return list(values[whole.group(1)])
        return _PLACEHOLDER.sub(lambda m: ",".join(v) if isinstance(v := values[m.group(1)], list) else str(v), value)
    if isinstance(value, list):
        out = []
        for v in value:
            filled = fill(v, values)
            out.extend(filled if isinstance(filled, list) else [filled])
        return out
    return value
