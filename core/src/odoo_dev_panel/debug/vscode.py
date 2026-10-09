"""Q1: VS Code attach entries in ``<installation root>/.vscode/launch.json``.

One entry per preset of the installation, named ``Odoo: <preset name>``. Entries are merged by name: other
entries are kept as they are, an entry of ours that differs is replaced only when asked (``replace``). A file with
comments or trailing commas (JSONC) is never rewritten: the plan gives the entries to paste instead. The previous
file is kept as ``launch.json.bak-TIME``. The IDE runs on this machine with the same paths, so no path mapping.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from datetime import datetime
from pathlib import Path

PREFIX = "Odoo: "


class VscodeError(ValueError):
    pass


def entry(preset: dict) -> dict:
    return {"name": f"{PREFIX}{preset['name']}", "type": "debugpy", "request": "attach",
            "connect": {"host": "127.0.0.1", "port": preset["port"]}, "justMyCode": False}


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments outside strings, then trailing commas."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def plan(root: str, presets: list[dict], replace: bool = False) -> dict:
    """What writing the entries would change: checks, before/after text, a unified diff, the snippet."""
    path = os.path.join(root, ".vscode", "launch.json")
    wanted = [entry(p) for p in presets]
    snippet = json.dumps(wanted, indent=4)
    out = {"path": path, "exists": os.path.exists(path), "added": [], "replaced": [], "unchanged": [], "conflicts": [],
           "kept": 0, "checks": [], "before": "", "after": "", "diff": "", "snippet": snippet, "ok": False, "jsonc": False}
    if not wanted:
        out["checks"].append({"id": "presets", "status": "fail", "detail": "no debug preset belongs to this installation"})
        return out
    data = {"version": "0.2.0", "configurations": []}
    if out["exists"]:
        try:
            out["before"] = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            out["checks"].append({"id": "read", "status": "fail", "detail": f"cannot read {path}: {exc.strerror}"})
            return out
        try:
            data = json.loads(out["before"]) if out["before"].strip() else data
        except ValueError:
            try:
                json.loads(_strip_jsonc(out["before"]))
                out["jsonc"] = True
                out["checks"].append({"id": "jsonc", "status": "fail", "detail": f"{path} has comments or trailing commas; "
                                      "it is not rewritten: paste the entries below into its configurations"})
            except ValueError:
                out["checks"].append({"id": "json", "status": "fail", "detail": f"{path} is not valid JSON; fix it first"})
            return out
        if not isinstance(data, dict) or not isinstance(data.get("configurations", []), list):
            out["checks"].append({"id": "shape", "status": "fail", "detail": f"{path} has no configurations list"})
            return out
    folder = os.path.dirname(path)
    target = folder if os.path.isdir(folder) else root
    if not os.access(path if out["exists"] else target, os.W_OK):
        out["checks"].append({"id": "write", "status": "fail", "detail": f"you cannot write {path if out['exists'] else target}; "
                              "paste the entries below by hand"})
    configs = list(data.get("configurations", []))
    by_name = {c.get("name"): n for n, c in enumerate(configs) if isinstance(c, dict)}
    for e in wanted:
        n = by_name.get(e["name"])
        if n is None:
            configs.append(e)
            out["added"].append(e["name"])
        elif configs[n] == e:
            out["unchanged"].append(e["name"])
        elif replace:
            configs[n] = e
            out["replaced"].append(e["name"])
        else:
            out["conflicts"].append(e["name"])
    out["kept"] = len(configs) - len(out["added"]) - len(out["replaced"]) - len(out["unchanged"])
    if out["conflicts"]:
        out["checks"].append({"id": "replace", "status": "fail",
                              "detail": f"launch.json already has a different {', '.join(out['conflicts'])}: confirm to replace"})
    data = {**data, "configurations": configs}
    data.setdefault("version", "0.2.0")
    out["after"] = json.dumps(data, indent=4) + "\n"
    out["diff"] = "".join(difflib.unified_diff(out["before"].splitlines(True), out["after"].splitlines(True),
                                               "launch.json", "launch.json (new)"))
    if not any(c["status"] == "fail" for c in out["checks"]):
        out["checks"].append({"id": "merge", "status": "ok", "detail": f"{len(out['added'])} added, {len(out['replaced'])} replaced, "
                              f"{len(out['unchanged'])} unchanged, {out['kept']} other entries kept"})
    out["ok"] = not any(c["status"] == "fail" for c in out["checks"])
    return out


def write(planned: dict) -> dict:
    """Write the plan's file (a backup of the old one first). Nothing to do when nothing changes."""
    if not planned["ok"]:
        raise VscodeError("; ".join(c["detail"] for c in planned["checks"] if c["status"] == "fail"))
    path = planned["path"]
    if not planned["added"] and not planned["replaced"]:
        return {"path": path, "backup": None, "changed": False}
    if planned["exists"] and Path(path).read_text(encoding="utf-8") != planned["before"]:
        raise VscodeError(f"{path} changed since the plan; review again")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup = None
    if planned["exists"]:
        backup = f"{path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        with open(backup, "w", encoding="utf-8") as fh:
            fh.write(planned["before"])
    tmp = f"{path}.odp-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(planned["after"])
    os.replace(tmp, path)
    return {"path": path, "backup": backup, "changed": True}
