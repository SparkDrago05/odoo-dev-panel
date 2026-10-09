"""W4: repositories.json. Tool-owned facts about repositories that Git does not know.

Git stays the source of truth for branches, commits and remotes; they are read on every listing and never
stored. This file keeps what only the developer can say: repositories added by hand, extra scan roots,
and how a repository relates to an installation (purpose, addons path, preferred branch, bulk selection).

One physical repository (keyed by its real path) can be associated with several installations.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

PURPOSES = ("community", "enterprise", "themes", "custom", "other")
ASSOC_FIELDS = {
    "purpose": str, "addons": bool, "preferred_branch": str, "expected_version": str,
    "destination": str, "group": str, "bulk": bool,
}


class RegistryError(Exception):
    pass


def path() -> Path:
    if "ODP_REPOSITORIES" in os.environ:
        return Path(os.environ["ODP_REPOSITORIES"])
    return paths.agent_state_dir() / "repositories.json"


def _empty() -> dict:
    return {"version": 1, "repos": {}, "assoc": [], "scan_roots": []}


def load(file: Path | None = None) -> dict:
    file = file or path()
    try:
        data = json.loads(file.read_text())
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        raise RegistryError(f"cannot read {file}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("repos"), dict) or not isinstance(data.get("assoc"), list):
        raise RegistryError(f"{file} has an unexpected format")
    data.setdefault("scan_roots", [])
    return data


def save(data: dict, file: Path | None = None) -> None:
    file = file or path()
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".repositories-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_assoc(fields: dict) -> dict:
    """Keep known association fields with the right types. Raises RegistryError on anything else."""
    out = {}
    for key, value in fields.items():
        kind = ASSOC_FIELDS.get(key)
        if kind is None:
            raise RegistryError(f"unknown association field {key!r}")
        if value is None:
            continue
        if not isinstance(value, kind):
            raise RegistryError(f"{key} must be {kind.__name__}")
        if kind is str and (len(value) > 200 or "\n" in value):
            raise RegistryError(f"{key} is too long or has a line break")
        out[key] = value
    if out.get("purpose") not in (None, *PURPOSES):
        raise RegistryError(f"purpose must be one of {', '.join(PURPOSES)}")
    dest = out.get("destination")
    if dest is not None and (dest.startswith("/") or ".." in Path(dest).parts):
        raise RegistryError("destination is a path relative to the installation root")
    return out


def register(repo: str, installation: str | None = None, fields: dict | None = None, file: Path | None = None) -> dict:
    """Remember a repository and, with ``installation``, its association. Updates in place when present."""
    repo = os.path.realpath(repo)
    data = load(file)
    data["repos"].setdefault(repo, {"added_at": _now()})
    entry = None
    if installation:
        fields = clean_assoc(fields or {})
        entry = next((a for a in data["assoc"] if a["repo"] == repo and a["installation"] == installation), None)
        if entry is None:
            entry = {"installation": installation, "repo": repo, "bulk": True}
            data["assoc"].append(entry)
        entry.update(fields)
    save(data, file)
    return {"repo": repo, "assoc": entry}


def forget(repo: str, installation: str | None = None, file: Path | None = None) -> bool:
    """Drop one association, or with no installation the repository and all its associations.
    Only metadata goes; the folder on disk is never touched."""
    repo = os.path.realpath(repo)
    data = load(file)
    before = (len(data["assoc"]), repo in data["repos"])
    data["assoc"] = [a for a in data["assoc"]
                     if not (a["repo"] == repo and (installation is None or a["installation"] == installation))]
    if installation is None:
        data["repos"].pop(repo, None)
    if before == (len(data["assoc"]), repo in data["repos"]):
        return False
    save(data, file)
    return True


def set_scan_roots(roots: list[str], file: Path | None = None) -> list[str]:
    clean = []
    for root in roots:
        if not isinstance(root, str) or not root.startswith("/") or os.path.normpath(root) == "/":
            raise RegistryError(f"scan root {root!r} must be an absolute folder other than /")
        clean.append(os.path.normpath(root))
    data = load(file)
    data["scan_roots"] = sorted(set(clean))
    save(data, file)
    return data["scan_roots"]
