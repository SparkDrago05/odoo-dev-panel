"""D11: the registry. A small JSON file of tool-owned metadata. Adopting writes here and nowhere else.

Discovered facts (paths, versions, processes) are never copied as truth: the filesystem stays the source of
truth. The registry keeps what only the tool knows: which installations the user adopted and the names given to them.
Secrets from configs are never stored.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import paths


class RegistryError(Exception):
    pass


def _empty() -> dict:
    return {"version": 1, "adopted": {}}


def load(path: Path | None = None) -> dict:
    path = path or paths.registry_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("adopted"), dict):
        raise RegistryError(f"registry {path} has an unexpected format")
    return data


def save(data: dict, path: Path | None = None) -> None:
    path = path or paths.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".registry-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def adopt(installation: dict, name: str | None = None, path: Path | None = None) -> dict:
    """Record a discovered installation (a dict from the scan). Returns the registry entry."""
    data = load(path)
    root = installation["root"]
    entry = data["adopted"].get(root, {})
    entry.update({
        "name": name or entry.get("name") or os.path.basename(root.rstrip("/")),
        "version": installation.get("version"),
        "adopted_at": entry.get("adopted_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    data["adopted"][root] = entry
    save(data, path)
    return entry


def release(root: str, path: Path | None = None) -> bool:
    data = load(path)
    if data["adopted"].pop(root, None) is None:
        return False
    save(data, path)
    return True


def annotate(snapshot: dict, data: dict) -> None:
    """Add ``adopted`` and ``name`` to installations, and list adopted ones that discovery no longer finds."""
    adopted = data["adopted"]
    present = set()
    for inst in snapshot["installations"]:
        entry = adopted.get(inst["root"])
        inst["adopted"] = entry is not None
        inst["name"] = entry["name"] if entry else os.path.basename(inst["root"].rstrip("/"))
        present.add(inst["root"])
    snapshot["missing"] = [{"root": r, **e} for r, e in sorted(adopted.items()) if r not in present]
