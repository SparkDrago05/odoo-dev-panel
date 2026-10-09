"""T2: installation profiles. TOML files that hold provision defaults and a list of repositories.

A "bundle" is a profile that only lists repositories. Layers, lowest first:

    built-in defaults  <  org overlay (one local file)  <  the chosen profile  <  what the wizard or CLI sets

Maps merge key by key, ``repos`` replaces the list below it, ``repos+`` appends to it. ``{version}`` (19) and
``{series}`` (19.0) are replaced in text values, so one profile can serve several Odoo versions. Profiles never
hold passwords: the PostgreSQL password is generated at run time and config options are a fixed safe list.

Files: ``~/.config/odoo-dev-panel/profiles/<name>.toml`` and the org overlay, by default
``~/.config/odoo-dev-panel/org.toml`` (another path can be set; the app never fetches it).
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

from .. import paths
from .spec import CONFIG_OPTIONS, DEFAULT_ODOO_GIT, SUPPORTED_VERSIONS, SpecError, spec_from_dict

INSTALL_KEYS = ("run_as", "root", "python", "odoo_git", "odoo_branch", "enterprise_git", "enterprise_branch",
                "enterprise_archive", "config_name", "pg_host", "pg_port", "http_port")
REPO_KEYS = ("name", "url", "branch", "destination", "addons", "purpose", "group", "shallow")
TOP_KEYS = ("name", "description", "odoo_version", "install", "config", "repos", "repos+")
BUILTIN = {"name": "built-in", "install": {"odoo_git": DEFAULT_ODOO_GIT, "config_name": "default",
                                           "pg_host": "localhost", "pg_port": 5432}}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]*)\}")


class ProfileError(ValueError):
    pass


# -- locations -------------------------------------------------------------------

def config_dir() -> Path:
    if "ODP_CONFIG_DIR" in os.environ:
        return Path(os.environ["ODP_CONFIG_DIR"])
    return paths._home() / ".config" / "odoo-dev-panel"


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def _settings_file() -> Path:
    return config_dir() / "settings.json"


def org_path() -> Path:
    try:
        value = json.loads(_settings_file().read_text()).get("org_profile")
    except (OSError, ValueError, AttributeError):
        value = None
    return Path(value) if isinstance(value, str) and value.startswith("/") else config_dir() / "org.toml"


def set_org_path(path: str | None) -> str:
    """Remember where the org overlay lives (None: back to the default). Only the path is stored."""
    if path is not None and (not isinstance(path, str) or not path.startswith("/")):
        raise ProfileError("the org overlay path must be absolute")
    try:
        data = json.loads(_settings_file().read_text())
    except (OSError, ValueError):
        data = {}
    if path is None:
        data.pop("org_profile", None)
    else:
        data["org_profile"] = os.path.normpath(path)
    _write(_settings_file(), json.dumps(data, indent=2) + "\n")
    return str(org_path())


def profile_path(name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ProfileError(f"bad profile name {name!r}: letters, digits, _ . - (up to 61)")
    return profiles_dir() / f"{name}.toml"


# -- reading and checking --------------------------------------------------------

def parse(text: str, source: str) -> dict:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{source}: {exc}") from exc
    check(data, source)
    return data


def read(path: Path) -> dict:
    try:
        return parse(path.read_text(), str(path))
    except OSError as exc:
        raise ProfileError(f"cannot read {path}: {exc.strerror}") from exc


def check(data: dict, source: str) -> None:
    """Shape and types only; full validation happens when the spec is built for a version."""
    def bad(msg: str):
        raise ProfileError(f"{source}: {msg}")

    for key in data:
        if key not in TOP_KEYS:
            bad(f"unknown key {key!r} (allowed: {', '.join(TOP_KEYS)})")
    for key in ("name", "description"):
        if key in data and not isinstance(data[key], str):
            bad(f"{key} must be text")
    if "odoo_version" in data and data["odoo_version"] not in SUPPORTED_VERSIONS:
        bad(f"odoo_version must be one of {', '.join(map(str, SUPPORTED_VERSIONS))}")
    install = data.get("install", {})
    if not isinstance(install, dict):
        bad("[install] must be a table")
    for key, value in install.items():
        if key not in INSTALL_KEYS:
            bad(f"[install] unknown key {key!r}")
        if not isinstance(value, int if key in ("pg_port", "http_port") else str) or isinstance(value, bool):
            bad(f"[install] {key} has the wrong type")
    config = data.get("config", {})
    if not isinstance(config, dict):
        bad("[config] must be a table")
    for key, value in config.items():
        if key not in CONFIG_OPTIONS:
            bad(f"[config] {key!r} cannot be set by a profile (passwords, ports and paths never are)")
        if not isinstance(value, (str, int, bool)):
            bad(f"[config] {key} must be text, a number or true/false")
    for list_key in ("repos", "repos+"):
        repos = data.get(list_key, [])
        if not isinstance(repos, list) or not all(isinstance(r, dict) for r in repos):
            bad(f"{list_key} must be an array of tables ([[{list_key}]])")
        for n, repo in enumerate(repos, 1):
            for key, value in repo.items():
                if key not in REPO_KEYS:
                    bad(f"{list_key} #{n}: unknown key {key!r}")
                if not isinstance(value, bool if key in ("addons", "shallow") else str):
                    bad(f"{list_key} #{n}: {key} has the wrong type")
            if not repo.get("url"):
                bad(f"{list_key} #{n}: url is required")
            if re.match(r"^[a-z+]+://[^/@]*:[^/@]*@", repo["url"]):
                bad(f"{list_key} #{n}: the URL contains a password; use an SSH key or a credential helper")


def list_profiles() -> list[dict]:
    out = []
    folder = profiles_dir()
    for file in sorted(f for f in folder.glob("*.toml") if not f.name.startswith(".")) if folder.is_dir() else []:
        row = {"name": file.stem, "path": str(file), "error": None, "title": None, "description": None,
               "odoo_version": None, "repos": 0, "has_install": False}
        try:
            data = read(file)
            row.update(title=data.get("name"), description=data.get("description"), odoo_version=data.get("odoo_version"),
                       repos=len(data.get("repos", [])) + len(data.get("repos+", [])), has_install=bool(data.get("install")))
        except ProfileError as exc:
            row["error"] = str(exc)
        out.append(row)
    return out


# -- layering --------------------------------------------------------------------

def layers(profile: str | None, overrides: dict | None = None) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = [("built-in", BUILTIN)]
    org = org_path()
    if org.is_file():
        out.append(("org", read(org)))
    if profile:
        out.append((f"profile:{profile}", read(profile_path(profile))))
    if overrides:
        check(overrides, "wizard")
        out.append(("wizard", overrides))
    return out


def merge(stack: list[tuple[str, dict]]) -> tuple[dict, dict]:
    """(merged data, origin of each value: {"install.root": "org", "repos.2": "profile:x", ...})."""
    merged: dict = {"install": {}, "config": {}, "repos": []}
    origin: dict[str, str] = {}
    repo_origin: list[str] = []
    for label, data in stack:
        for key in ("name", "description", "odoo_version"):
            if key in data:
                merged[key] = data[key]
                origin[key] = label
        for table in ("install", "config"):
            for key, value in data.get(table, {}).items():
                merged[table][key] = value
                origin[f"{table}.{key}"] = label
        if "repos" in data:
            merged["repos"] = copy.deepcopy(data["repos"])
            repo_origin = [label] * len(data["repos"])
        if "repos+" in data:
            merged["repos"] += copy.deepcopy(data["repos+"])
            repo_origin += [label] * len(data["repos+"])
    origin.update({f"repos.{n}": label for n, label in enumerate(repo_origin)})
    return merged, origin


def _fill(value, version: int, where: str):
    if not isinstance(value, str):
        return value

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key == "version":
            return str(version)
        if key == "series":
            return f"{version}.0"
        raise ProfileError(f"{where}: unknown placeholder {{{key}}} (use {{version}} or {{series}})")
    return _PLACEHOLDER.sub(sub, value)


def resolve(profile: str | None, version: int | None = None, overrides: dict | None = None) -> dict:
    """Merged, placeholder-filled values and the spec dict they give. Raises ProfileError with the layer at fault."""
    merged, origin = merge(layers(profile, overrides))
    pinned = merged.get("odoo_version")
    version = version or pinned
    if version is None:
        raise ProfileError("choose an Odoo version (the profile does not pin one)")
    if pinned and version != pinned and origin.get("odoo_version") != "built-in":
        raise ProfileError(f"{origin['odoo_version']} is for Odoo {pinned}, not {version}")
    install = {k: _fill(v, version, f"install.{k}") for k, v in merged["install"].items()}
    repos = [{k: _fill(v, version, f"repos #{n + 1}.{k}") for k, v in r.items()} for n, r in enumerate(merged["repos"])]
    config = {k: ("True" if v is True else "False" if v is False else str(v)) for k, v in merged["config"].items()}
    spec = {"version": version, **install, "custom": repos, "config_options": config}
    try:
        built = spec_from_dict(spec)
    except SpecError as exc:
        raise ProfileError(str(exc)) from exc
    return {"version": version, "pinned": pinned, "install": install, "config": config, "repos": repos,
            "origin": origin, "spec": spec, "root": built.root, "run_as": built.run_as,
            "name": merged.get("name"), "description": merged.get("description")}


# -- writing ---------------------------------------------------------------------

def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = re.sub(r"[\x00-\x1f\x7f]", lambda m: f"\\u{ord(m.group()):04x}", escaped)
    return f'"{escaped}"'


def dump(data: dict) -> str:
    """TOML for the profile schema (flat tables and arrays of tables of scalars)."""
    check(data, "profile")
    lines = []
    for key in ("name", "description", "odoo_version"):
        if key in data:
            lines.append(f"{key} = {_toml_value(data[key])}")
    for table in ("install", "config"):
        if data.get(table):
            lines += ["", f"[{table}]"] + [f"{k} = {_toml_value(v)}" for k, v in data[table].items()]
    for list_key in ("repos", "repos+"):
        for repo in data.get(list_key, []):
            header = "[[repos]]" if list_key == "repos" else '[["repos+"]]'
            lines += ["", header] + [f"{k} = {_toml_value(repo[k])}" for k in REPO_KEYS if k in repo]
    text = "\n".join(lines).lstrip("\n") + "\n"
    if tomllib.loads(text) != {k: v for k, v in data.items() if v not in ({}, [])}:
        raise ProfileError("internal: the profile does not survive a TOML round trip")
    return text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)  # may name private repositories
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(name: str, data: dict, overwrite: bool = False) -> str:
    path = profile_path(name)
    if path.exists() and not overwrite:
        raise ProfileError(f"profile {name} exists; choose another name or overwrite it")
    _write(path, dump(data))
    return str(path)


def import_file(source: str, name: str | None = None, overwrite: bool = False) -> str:
    """Copy a profile file (for example one a colleague exported) into the profiles folder after checking it."""
    data = read(Path(source))
    return save(name or Path(source).stem, data, overwrite)


def delete(name: str) -> str | None:
    """Move a saved profile aside to ``.trash-<name>-<time>.toml`` in the same folder (never deleted). Returns where."""
    from datetime import datetime

    path = profile_path(name)
    if not path.exists():
        return None
    aside = path.with_name(f".trash-{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.toml")
    os.replace(path, aside)
    return str(aside)
