"""One way to turn a provision request into a spec and a dry-run plan, shared by ``odp provision`` and the sidecar.

A request is either the flat spec fields (as before) or ``{"profile": name?, "version": N?, "overrides": {...}}``
where ``overrides`` is a profile-shaped dict from the wizard or CLI (the top layer).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .. import paths
from . import plan as plan_mod
from . import preflight, profiles
from .spec import ProvisionSpec, SpecError, spec_from_dict


class RequestError(ValueError):
    pass


def build(params: dict) -> tuple[ProvisionSpec, dict | None]:
    """(spec, resolved profile or None). Raises RequestError for any bad input."""
    if not isinstance(params, dict):
        raise RequestError("params must be an object")
    try:
        if "profile" in params or "overrides" in params:
            extra = set(params) - {"profile", "version", "overrides", "destinations"}
            if extra:
                raise RequestError(f"with a profile, use overrides for {', '.join(sorted(extra))}")
            version = params.get("version")
            if version is not None and (not isinstance(version, int) or isinstance(version, bool)):
                raise RequestError("version must be an integer")
            resolved = profiles.resolve(params.get("profile") or None, version, params.get("overrides") or None)
            moves = params.get("destinations") or {}
            if not isinstance(moves, dict) or not all(isinstance(v, str) for v in moves.values()):
                raise RequestError("destinations maps repository names to folders")
            names = {r.get("name") or r["url"].rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
                     for r in resolved["spec"]["custom"]}
            unknown = set(moves) - names
            if unknown:
                raise RequestError(f"no repository named {', '.join(sorted(unknown))} in the profile")
            for repo in resolved["spec"]["custom"]:
                name = repo.get("name") or repo["url"].rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
                if name in moves:
                    repo["destination"] = moves[name]
            return spec_from_dict(resolved["spec"]), resolved
        return spec_from_dict(params), None
    except (SpecError, profiles.ProfileError) as exc:
        raise RequestError(str(exc)) from exc


def read_receipt(spec: ProvisionSpec) -> dict | None:
    """T4: the last provision receipt for this root (or, when the root was never made, the newest fallback receipt
    of the run-as user in the state folder). Only the parts the review shows; nothing secret is in a receipt."""
    candidates = [Path(spec.receipt_path)]
    fallback = paths.agent_state_dir() / "provision"
    if fallback.is_dir():
        candidates += sorted(fallback.glob(f"{spec.run_as}-*.json"), reverse=True)
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or (data.get("spec") or {}).get("root") != spec.root:
            continue
        root_script = data.get("root_script") or {}
        return {"path": str(path), "status": data.get("status"), "last_phase": data.get("last_phase"),
                "updated_at": data.get("updated_at"), "completed": data.get("completed") or [],
                "root_exit_code": root_script.get("exit_code"), "created": root_script.get("created") or []}
    return None


PHASES = ("root-script", "clone", "pip", "verify")  # names the executor writes into receipt["completed"]


def plan(params: dict, remote: bool = True, remote_check=preflight.ls_remote) -> dict:
    spec, resolved = build(params)
    sec = plan_mod.Secrets.generate()
    checks = preflight.run_preflight(spec)
    if remote:
        checks += preflight.remote_checks(spec, remote_check)
    placeholder = plan_mod.Secrets("<generated at run time>", "<generated at run time>")
    previous = read_receipt(spec)
    if previous:
        previous["remaining"] = [p for p in PHASES if p not in previous["completed"]]
    return {
        "spec": asdict(spec),
        "preflight": [asdict(c) for c in checks],
        "ok": not preflight.has_failures(checks),
        "steps": [asdict(s) for s in plan_mod.build_steps(spec, sec)],
        "root_script": plan_mod.render_root_script(spec, sec),
        "config": plan_mod.render_conf(spec, placeholder),
        "addons_path": plan_mod.addons_path(spec),
        "tree": tree(spec),
        "profile": {k: resolved[k] for k in ("name", "description", "origin", "pinned", "version")} if resolved else None,
        "previous": previous,
        "remote_checked": remote,
    }


def tree(spec: ProvisionSpec) -> list[dict]:
    """Destination folders under the root, for the review's tree: {path, kind, label, addons}."""
    rows = [{"path": "odoo", "kind": "community", "label": f"Odoo {spec.odoo_branch}", "addons": True},
            {"path": "venv", "kind": "venv", "label": f"Python {spec.python}", "addons": False}]
    if spec.has_enterprise:
        rows.append({"path": "enterprise", "kind": "enterprise",
                     "label": "archive" if spec.enterprise_archive else spec.enterprise_branch or f"{spec.version}.0",
                     "addons": True})
    rows += [{"path": r.destination, "kind": r.purpose, "label": r.branch or "default branch", "addons": r.addons,
              "group": r.group, "name": r.name} for r in spec.custom]
    return sorted(rows, key=lambda r: r["path"])
