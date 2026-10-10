"""Requirement files of an installation: the ones found on disk, and the ones the developer added.

Doctor, the venv repair and the Python tab read the files ``checks.requirement_files`` returns: Odoo's own
``requirements.txt``, the ones of ``custom/`` (up to two folders deep) and the ones added here. Every other
``requirements*.txt`` under the installation root is *detected* and offered with an Add button.

The added paths live in ``requirement-files.json`` in the state directory of the dev user, keyed by root.
"""

from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from pathlib import Path

from .. import paths

PATTERN = "requirements*.txt"
MAX_DEPTH = 6
MAX_FILES = 300
# Folders that never hold a requirement file of an addon repository (and are large).
SKIP_DIRS = {"node_modules", "__pycache__", "static", "i18n", "i18n_extra", "tests", "migrations", "site-packages"}


class ReqFilesError(ValueError):
    pass


def state_path() -> Path:
    if "ODP_REQFILES" in os.environ:
        return Path(os.environ["ODP_REQFILES"])
    return paths.agent_state_dir() / "requirement-files.json"


def _load() -> dict:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    file = state_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".requirement-files-")
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


def added(root: str) -> list[str]:
    """Added files of an installation that still exist."""
    entry = _load().get(os.path.normpath(root))
    files = entry.get("added", []) if isinstance(entry, dict) else []
    return [f for f in files if isinstance(f, str) and os.path.isfile(f)]


def detect(root: str, skip: tuple[str, ...] = ()) -> list[str]:
    """Every requirements*.txt under root, sorted. Hidden folders, venvs and ``skip`` are not entered."""
    root = os.path.normpath(root)
    found: list[str] = []
    base_depth = root.count(os.sep)
    for current, dirs, files in os.walk(root, followlinks=False):
        depth = current.count(os.sep) - base_depth
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not d.startswith("venv") and d not in SKIP_DIRS
                         and os.path.join(current, d) not in skip and depth < MAX_DEPTH)
        for name in sorted(files):
            if fnmatch.fnmatch(name, PATTERN):
                found.append(os.path.join(current, name))
        if len(found) >= MAX_FILES:
            break
    return sorted(found[:MAX_FILES])


def _check(root: str, path: object) -> str:
    if not isinstance(path, str) or not path:
        raise ReqFilesError("path is required")
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    if not (real == real_root or real.startswith(real_root + os.sep)):
        raise ReqFilesError(f"{path} is outside {root}")
    if not os.path.isfile(real) or not fnmatch.fnmatch(os.path.basename(real), PATTERN):
        raise ReqFilesError(f"{path} is not a {PATTERN} file")
    return os.path.normpath(path)


def add(root: str, path: object) -> list[str]:
    root = os.path.normpath(root)
    path = _check(root, path)
    data = _load()
    entry = data.setdefault(root, {"added": []})
    if path not in entry["added"]:
        entry["added"] = sorted(entry["added"] + [path])
        _save(data)
    return entry["added"]


def remove(root: str, path: object) -> list[str]:
    root = os.path.normpath(root)
    data = _load()
    entry = data.get(root)
    if isinstance(entry, dict) and path in entry.get("added", []):
        entry["added"] = [f for f in entry["added"] if f != path]
        _save(data)
    return (entry or {}).get("added", [])
