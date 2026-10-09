"""M6: manifest validation. Reads ``__manifest__.py`` with ast (never imports module code).

Each problem: {code, level (error|warn|info), text}. Only modules outside Odoo's own source and Enterprise are
checked by default; their manifests are Odoo's business.
"""

from __future__ import annotations

import os
import re

from ..modules import read_manifest

LICENSES = {
    "GPL-2", "GPL-2 or any later version", "GPL-3", "GPL-3 or any later version", "AGPL-3", "LGPL-3",
    "Other OSI approved licence", "OEEL-1", "OPL-1", "Other proprietary",
}
_VERSION = re.compile(r"^\d+(\.\d+){1,4}$")
FILE_KEYS = ("data", "demo", "qweb")


def check_module(name: str, path: str, series: str | None, known: set[str]) -> list[dict]:
    out: list[dict] = []

    def add(code: str, level: str, text: str) -> None:
        out.append({"code": code, "level": level, "text": text})

    manifest = read_manifest(os.path.join(path, "__manifest__.py"))
    if manifest is None:
        add("unreadable", "error", "__manifest__.py is not a valid Python dict literal")
        return out
    if not os.path.isfile(os.path.join(path, "__init__.py")):
        add("no-init", "error", "__init__.py is missing: Odoo cannot import the module")
    if not isinstance(manifest.get("name"), str) or not manifest.get("name"):
        add("no-name", "warn", "no name in the manifest")
    version = manifest.get("version")
    if version is None:
        add("no-version", "info", "no version: Odoo uses the series version, upgrades cannot be tracked")
    elif not isinstance(version, str) or not _VERSION.match(version):
        add("bad-version", "error", f"version {version!r} is not like 1.0.0 or {series or 'NN.0'}.1.0.0")
    elif series and version.count(".") == 4 and not version.startswith(series + "."):
        add("series-version", "warn", f"version {version} does not start with the installation's series {series}")
    license_ = manifest.get("license")
    if license_ is None:
        add("no-license", "info", "no license: Odoo assumes LGPL-3 and logs a warning")
    elif license_ not in LICENSES:
        add("bad-license", "warn", f"license {license_!r} is not one Odoo knows")
    depends = manifest.get("depends", [])
    if not isinstance(depends, (list, tuple)) or not all(isinstance(d, str) for d in depends):
        add("bad-depends", "error", "depends must be a list of module names")
    else:
        missing = [d for d in depends if d not in known]
        if missing:
            add("missing-depends", "error", f"depends on modules that are not on the addons_path: {', '.join(missing)}")
        if name in depends:
            add("self-depends", "error", "the module depends on itself")
    for key in FILE_KEYS:
        files = manifest.get(key, [])
        if not isinstance(files, (list, tuple)):
            add("bad-files", "error", f"{key} must be a list of file paths")
            continue
        absent = [f for f in files if isinstance(f, str) and not os.path.isfile(os.path.join(path, f))]
        if absent:
            add("missing-files", "error", f"{key} lists files that do not exist: {', '.join(absent[:5])}"
                + (f" and {len(absent) - 5} more" if len(absent) > 5 else ""))
    if manifest.get("installable", True) is False:
        add("not-installable", "info", "installable is False: Odoo ignores the module")
    return out


def own_modules(graph: dict, snapshot: dict) -> list[str]:
    """Modules not in the installation's Odoo source or Enterprise folder."""
    inst = next((i for i in snapshot.get("installations", []) if i["root"] == graph.get("installation")), None)
    skip = []
    if inst:
        skip = [inst["source"].rstrip("/") + "/", inst["root"].rstrip("/") + "/enterprise/"]
    return [n for n, info in graph["modules"].items() if not any(info["path"].startswith(s) for s in skip)]


def check_graph(graph: dict, snapshot: dict, names: list[str] | None = None) -> dict[str, list[dict]]:
    known = set(graph["modules"])
    series = graph.get("series")
    targets = names if names is not None else own_modules(graph, snapshot)
    result = {}
    for name in targets:
        info = graph["modules"].get(name)
        if info is not None:
            result[name] = check_module(name, info["path"], series, known)
    for cycle in graph.get("cycles", []):
        for name in cycle:
            if name in result:
                result[name].append({"code": "cycle", "level": "error", "text": "circular dependency: " + " → ".join(cycle + [cycle[0]])})
    for row in graph.get("shadowed", []):
        if row["name"] in result:
            result[row["name"]].append({"code": "shadowed", "level": "warn",
                                        "text": f"another copy at {row['path']} is ignored (this one comes first on the addons_path)"})
    return result
