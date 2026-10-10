"""Y1/Y2: what the venv of an installation holds against what its requirement files ask for.

Requirement rows: ok, missing, mismatch (installed version outside the specifier), not-applicable (its marker
excludes this Python), unknown (marker or specifier the app cannot read). Conflicts: one package asked for by
several files with specifiers no single version satisfies. Every status is measured from files, not guessed.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..discover import venv as venv_mod
from ..doctor import manifests, reqfiles, requirements
from ..doctor.checks import default_requirement_files, requirement_files
from ..provision.spec import PYTHON_BY_VERSION
from . import imports, specs

PYTHON_DIR = "/opt/odoo-dev-panel/python"


class EnvError(ValueError):
    pass


def installation(snapshot: dict, root) -> dict:
    if not isinstance(root, str) or not root:
        raise EnvError("root is required")
    inst = next((i for i in snapshot.get("installations", []) if i["root"] == os.path.normpath(root)), None)
    if inst is None:
        raise EnvError(f"{root} is not a discovered Odoo installation")
    return inst


def _major(inst: dict) -> int | None:
    try:
        return int((inst.get("version") or "").split(".")[0])
    except ValueError:
        return None


def files_for(inst: dict, repos: list[str] | None = None) -> list[str]:
    """Odoo's requirements.txt, the ones of custom repositories (doctor's two-level scan) and of every
    repository of the installation, without duplicates."""
    out = list(requirement_files(inst))
    for repo in repos or []:
        path = os.path.join(repo, "requirements.txt")
        if os.path.isfile(path) and path not in out:
            out.append(path)
    return out


def detected(inst: dict, repos: list[str] | None = None) -> list[dict]:
    """Every requirements*.txt under the installation root: used (default or repository), added, or available."""
    default = set(default_requirement_files(inst)) | set(files_for(inst, repos)) - set(reqfiles.added(inst["root"]))
    added = set(reqfiles.added(inst["root"]))
    skip = (inst["venv"],) if inst.get("venv") else ()
    out = []
    for path in reqfiles.detect(inst["root"], skip):
        state = "added" if path in added else "used" if path in default else "available"
        try:
            count = len(requirements.parse(Path(path).read_text(errors="replace")))
        except OSError:
            count = 0
        out.append({"path": path, "state": state, "count": count})
    return out


def interpreter(inst: dict) -> dict:
    venv = inst.get("venv")
    info = venv_mod.inspect(venv) if venv else None
    major = _major(inst)
    pinned = PYTHON_BY_VERSION.get(major) if major else None
    out = {"venv": venv, "python": info.python if info else None, "target": info.target if info else None,
           "version": info.python_version if info else None, "built_for": info.built_for if info else None,
           "pinned": pinned, "uv_managed": bool(info and info.target and info.target.startswith(PYTHON_DIR + "/")),
           "system_python": bool(info and info.system_python), "problem": info.problem if info else "no venv",
           "matches_pin": None}
    if pinned and out["version"]:
        out["matches_pin"] = out["version"] == pinned
    return out


def packages(inst: dict, version: str | None) -> dict[str, str]:
    venv = inst.get("venv")
    if not venv:
        return {}
    versions = [version] if version else []
    versions += [v for v in venv_mod.inspect(venv).site_versions if v not in versions]
    for v in versions:
        found = venv_mod.installed(venv_mod.site_packages(venv, v))
        if found:
            return found
    return {}


def _installed_version(name: str, pkgs: dict[str, str]) -> tuple[str | None, str | None]:
    if name in pkgs:
        return name, pkgs[name]
    for alias in requirements.ALIASES.get(name, set()):
        if alias in pkgs:
            return alias, pkgs[alias]
    return None, None


def describe(inst: dict, repos: list[str] | None = None) -> dict:
    interp = interpreter(inst)
    py = interp["version"] or interp["built_for"] or interp["pinned"]
    pkgs = packages(inst, py)
    env = requirements.environment(py) if py else None
    rows: list[dict] = []
    wanted: dict[str, list[dict]] = {}
    files = files_for(inst, repos)
    for path in files:
        try:
            reqs = requirements.parse(Path(path).read_text(errors="replace"))
        except OSError:
            continue
        for req in reqs:
            spec = specs.requirement_spec(req.raw)
            row = {"file": path, "name": req.name, "raw": req.raw, "spec": spec, "marker": req.marker,
                   "installed": None, "installed_as": None, "status": "ok", "detail": ""}
            if req.marker and env is not None:
                try:
                    if not requirements.evaluate(req.marker, env):
                        row["status"], row["detail"] = "not-applicable", f"marker excludes Python {py}"
                        rows.append(row)
                        continue
                except requirements.MarkerError as exc:
                    row["status"], row["detail"] = "unknown", str(exc)
                    rows.append(row)
                    continue
            as_name, version = _installed_version(req.name, pkgs)
            row["installed"], row["installed_as"] = version, as_name
            if version is None:
                row["status"], row["detail"] = "missing", "not installed in the venv"
            elif spec:
                ok = specs.satisfies(version, spec)
                if ok is False:
                    row["status"], row["detail"] = "mismatch", f"{version} installed, {spec} asked"
                elif ok is None:
                    row["status"], row["detail"] = "unknown", f"cannot compare {version} with {spec}"
            rows.append(row)
            wanted.setdefault(req.name, []).append(row)
    rows += _manifest_rows(inst, py, pkgs, {r["name"] for r in rows})
    conflicts = []
    for name, asked in wanted.items():
        by_file = [r for r in asked if r["spec"]]
        if len({r["file"] for r in by_file}) < 2:
            continue
        reason = specs.conflict([r["spec"] for r in by_file])
        if reason:
            conflicts.append({"name": name, "reason": reason,
                              "asked": [{"file": r["file"], "spec": r["spec"]} for r in by_file]})
    required = {r["name"] for r in rows} | {a for r in rows for a in requirements.ALIASES.get(r["name"], set())}
    counts = {s: sum(r["status"] == s for r in rows)
              for s in ("ok", "missing", "mismatch", "not-applicable", "unknown", "manifest-missing")}
    return {"root": inst["root"], "version": inst.get("version"), "run_as": inst.get("owner"), "interpreter": interp,
            "files": files, "detected": detected(inst, repos), "requirements": rows, "counts": counts, "conflicts": conflicts,
            "packages": dict(sorted(pkgs.items())), "extras": sorted(set(pkgs) - required)}


def _manifest_rows(inst: dict, py: str | None, pkgs: dict[str, str], known: set[str]) -> list[dict]:
    """Python dependencies addon manifests declare that the venv cannot import and no requirement file names.

    They are import names, so the install button shows the distribution the app maps them to. They stay out
    of "Install missing": a wrong mapping must never install a package nobody asked for.
    """
    venv = inst.get("venv")
    if not venv or not py:
        return []
    site = venv_mod.site_packages(venv, py)
    tops = venv_mod.top_levels(site)
    rows = []
    for dep, paths_ in sorted(manifests.external_python(inst["root"], (venv,)).items()):
        name, spec = imports.split(dep)
        if not name:
            continue
        dist = venv_mod.normalize(name)
        package = imports.package_for(name)
        if dist in pkgs or venv_mod.normalize(package) in pkgs or name in tops or name in known or dist in known \
                or venv_mod.normalize(package) in known:
            continue
        addons = sorted({os.path.basename(os.path.dirname(p)) for p in paths_})
        rows.append({"file": paths_[0], "name": venv_mod.normalize(package), "raw": dep, "spec": spec.replace(" ", ""),
                     "marker": None, "installed": None, "installed_as": None, "status": "manifest-missing",
                     "detail": f"import {name} fails; declared by {', '.join(addons[:5])}"
                               + (f" and {len(addons) - 5} more" if len(addons) > 5 else "")})
    return rows


def freeze(inst: dict) -> str:
    """Y4: a sanitized ``name==version`` list of the venv, with no paths, URLs or hashes."""
    interp = interpreter(inst)
    pkgs = packages(inst, interp["version"] or interp["built_for"])
    head = [f"# Odoo {inst.get('version') or '?'}, Python {interp['version'] or interp['built_for'] or '?'}",
            f"# {len(pkgs)} packages, exported by Odoo Dev Panel"]
    return "\n".join(head + [f"{n}=={v}" for n, v in sorted(pkgs.items())]) + "\n"
