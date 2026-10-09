"""W5-W9: safe Git operations as plan/run pairs.

A plan is read-only: it inspects each repository, decides per repository whether the operation can run,
and lists the exact commands. Running a plan executes those commands one repository at a time; a failure
stops nothing but that repository, and cancelling takes effect between repositories, never inside Git.

Commands that can lose work are never produced: no reset, clean, push, force, rebase or merge other than
a fast-forward.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import explain, registry, runner, state, urls
from .discover import _dot_git, enclosing_repo

OPS = ("fetch", "pull", "switch", "checkout", "clone", "bundle")
OK, WARN, FAIL = "ok", "warn", "fail"
_REF = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9][A-Za-z0-9_./+-]{0,199}(?<![./])$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_CHANGED = 2000

Inspect = Callable[[str, str | None], "state.RepoState"]


class OpError(ValueError):
    pass


@dataclass
class Item:
    repo: str
    title: str
    commands: list[list[str]] = field(default_factory=list)
    skip: str | None = None             # why this repository is left out
    problems: list[dict] = field(default_factory=list)
    level: str = OK                     # ok | warn | fail (fail = skipped)
    register: dict | None = None        # {installation, repo, fields}: recorded after a clone, or for a kept repo
    keep: bool = False                  # an existing repository the plan only records

    def as_dict(self) -> dict:
        return {"repo": self.repo, "title": self.title, "commands": [runner.display(c) for c in self.commands],
                "skip": self.skip, "problems": self.problems, "level": self.level}


@dataclass
class Plan:
    op: str
    items: list[Item] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)   # global: {id, status, detail}
    register: dict | None = None                        # clone: what to record afterwards

    @property
    def ok(self) -> bool:
        return not any(c["status"] == FAIL for c in self.checks) and any(i.skip is None or i.keep for i in self.items)

    def as_dict(self) -> dict:
        runnable = [i for i in self.items if i.skip is None]
        steps = [{"id": f"{self.op}-{n}", "phase": n, "actor": "dev", "title": i.title,
                  "commands": [runner.display(c) for c in i.commands]} for n, i in enumerate(runnable, 1)]
        checks = list(self.checks) + [{"id": i.repo, "status": i.level, "detail": f"{i.repo}: {i.skip}"}
                                      for i in self.items if i.skip]
        return {"op": self.op, "ok": self.ok, "items": [i.as_dict() for i in self.items], "checks": checks,
                "steps": steps, "register": self.register,
                "counts": {"run": len(runnable), "skip": len(self.items) - len(runnable)}}


def check_ref(ref: str, what: str = "branch") -> str:
    if not isinstance(ref, str) or not _REF.match(ref) or ref.endswith(".lock"):
        raise OpError(f"invalid {what} name {ref!r}")
    return ref


def _blocked(s: state.RepoState, *, dirty: bool = True, detached: bool = False, remote: bool = False) -> str | None:
    """The first reason a write operation cannot run on this repository, or None."""
    if not s.ok:
        return s.problems[0]["title"] if s.problems else (s.error or "unreadable")
    if s.foreign:
        return f"owned by {s.owner}; write operations run only on repositories you own"
    if s.conflicted:
        return "has conflicted files"
    if dirty and (s.staged or s.modified):
        return "has uncommitted changes"
    if detached and s.detached:
        return "detached HEAD (not on a branch)"
    if remote and not s.remotes:
        return "has no remote"
    return None


def _remote(s: state.RepoState) -> str | None:
    if s.upstream and "/" in s.upstream:
        name = s.upstream.split("/", 1)[0]
        if name in s.remotes:
            return name
    if "origin" in s.remotes:
        return "origin"
    return next(iter(s.remotes), None)


def _git(repo: str, *args: str) -> list[str]:
    return runner.argv(repo, *args, read_only=False)


# -- plans --------------------------------------------------------------------

def plan_fetch(repos: list[tuple[str, str | None]], inspect: Inspect = state.inspect) -> Plan:
    plan = Plan("fetch")
    for path, gitdir in repos:
        s = inspect(path, gitdir)
        item = Item(path, f"Fetch {os.path.basename(path)}", problems=s.problems)
        item.skip = _blocked(s, dirty=False, remote=True)
        if item.skip is None:
            item.commands = [_git(path, "fetch", "--prune", _remote(s) or "origin")]
        else:
            item.level = WARN
        plan.items.append(item)
    return plan


def plan_pull(repos: list[tuple[str, str | None]], inspect: Inspect = state.inspect) -> Plan:
    """Fast-forward only. Repositories with local changes, no upstream, a detached HEAD or a diverged branch
    are skipped with the reason; nothing is merged or rebased."""
    plan = Plan("pull")
    for path, gitdir in repos:
        s = inspect(path, gitdir)
        item = Item(path, f"Pull {os.path.basename(path)} ({s.branch or 'detached'})", problems=s.problems)
        item.skip = _blocked(s, detached=True, remote=True)
        if item.skip is None and not s.upstream:
            item.skip = f"{s.branch} has no upstream branch"
        if item.skip is None and s.ahead and s.behind:
            item.skip = f"diverged from {s.upstream} ({s.ahead} ahead, {s.behind} behind)"
        if item.skip is None:
            item.commands = [_git(path, "pull", "--ff-only", "--no-rebase")]
        else:
            item.level = WARN
        plan.items.append(item)
    return plan


def plan_switch(path: str, gitdir: str | None, branch: str, inspect: Inspect = state.inspect,
                fetch_branch: bool = False, run=runner.run) -> Plan:
    check_ref(branch)
    plan = Plan("switch")
    s = inspect(path, gitdir)
    item = Item(path, f"Switch {os.path.basename(path)} to {branch}", problems=s.problems)
    plan.items.append(item)
    item.skip = _blocked(s)
    if item.skip is None and s.branch == branch:
        item.skip = f"already on {branch}"
    if item.skip:
        item.level = WARN if item.skip.startswith("already") else FAIL
        return plan

    def has(ref: str) -> bool:
        return run(runner.argv(path, "rev-parse", "--verify", "--quiet", ref)).ok

    remote = _remote(s)
    if has(f"refs/heads/{branch}"):
        item.commands = [_git(path, "switch", branch)]
    elif remote and has(f"refs/remotes/{remote}/{branch}"):
        item.commands = [_git(path, "switch", "--track", f"{remote}/{branch}")]
    elif remote and fetch_branch:
        # A shallow single-branch clone knows one branch; teach it this one, fetch it, then switch.
        depth = ["--depth=1"] if s.shallow else []
        item.commands = [_git(path, "remote", "set-branches", "--add", remote, branch),
                         _git(path, "fetch", *depth, remote, branch),
                         _git(path, "switch", "--track", f"{remote}/{branch}")]
        item.level = WARN
    else:
        item.skip = (f"no branch {branch} locally or on {remote}; fetch first"
                     + (" (a single-branch clone needs 'fetch this branch')" if s.shallow else "") if remote
                     else f"no branch {branch} and no remote")
        item.level = FAIL
    return plan


def plan_checkout(path: str, gitdir: str | None, ref: str, confirm: str | None, inspect: Inspect = state.inspect,
                  run=runner.run) -> Plan:
    """Detached checkout of a tag or commit. Explicit: the developer types the ref again to confirm."""
    check_ref(ref, "tag or commit")
    plan = Plan("checkout")
    s = inspect(path, gitdir)
    item = Item(path, f"Check out {ref} in {os.path.basename(path)} (detached)", problems=s.problems)
    plan.items.append(item)
    item.skip = _blocked(s)
    if item.skip is None and not run(runner.argv(path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")).ok:
        item.skip = f"{ref} is not a known tag or commit; fetch first"
    if item.skip:
        item.level = FAIL
        return plan
    item.commands = [_git(path, "switch", "--detach", ref)]
    plan.checks.append({"id": "confirm", "status": OK if confirm == ref else FAIL,
                        "detail": "confirmed" if confirm == ref else f"type {ref} to confirm a detached checkout"})
    return plan


def plan_clone(root: str, url: str, destination: str, ref: str | None = None, shallow: bool = True,
               fields: dict | None = None) -> Plan:
    """Clone into ``root/destination``. The destination keeps its nested relative path (custom/cms/admissions)
    and must stay inside the installation root, be missing or an empty folder, and be writable by you."""
    plan = Plan("clone")
    checks = plan.checks

    def check(cid: str, ok: bool, detail: str, warn: bool = False) -> bool:
        checks.append({"id": cid, "status": OK if ok else (WARN if warn else FAIL), "detail": detail})
        return ok

    try:
        urls.validate(url)
        check("url", True, urls.redact(url))
    except urls.UrlError as exc:
        check("url", False, str(exc))
    target = _destination(root, destination, check)
    detached = None
    if ref:
        try:
            check_ref(ref, "branch, tag or commit")
            if _SHA.match(ref):
                detached, shallow = ref, False
                check("ref", True, f"commit {ref}: full clone, then a detached checkout", warn=True)
            else:
                check("ref", True, f"branch or tag {ref}")
        except OpError as exc:
            check("ref", False, str(exc))
    check("git", shutil.which("git") is not None, "git is installed" if shutil.which("git") else "git is not installed")
    if target is None or any(c["status"] == FAIL for c in checks):
        plan.items.append(Item(destination, "Clone", skip="checks failed", level=FAIL))
        return plan
    item = Item(target, f"Clone {urls.short_name(url)} into {destination}")
    item.commands = [runner.clone_argv(url, target, None if detached else ref, shallow)]
    if detached:
        item.commands.append(_git(target, "switch", "--detach", detached))
    plan.items.append(item)
    plan.register = {"installation": root, "repo": target,
                     "fields": registry.clean_assoc({**(fields or {}), "destination": os.path.normpath(destination)})}
    return plan


def plan_bundle(root: str, repos: list[dict]) -> Plan:
    """T5: the repositories of a profile on an existing installation. Missing ones are cloned (each with its own
    checks); ones already at their folder are kept and only recorded; anything else at a folder is reported."""
    plan = Plan("bundle")
    for repo in repos:
        dest = repo["destination"]
        target = os.path.join(root, dest)
        fields = {k: repo[k] for k in ("purpose", "group", "addons") if repo.get(k) is not None}
        if repo.get("branch"):
            fields["preferred_branch"] = repo["branch"]
        fields = registry.clean_assoc({**fields, "destination": os.path.normpath(dest)})
        if _dot_git(target) is not False:
            item = Item(target, f"Keep {repo['name']} at {dest}", skip="already there: kept, not fetched or switched",
                        level=OK, keep=True, register={"installation": root, "repo": target, "fields": fields})
            plan.items.append(item)
            continue
        sub = plan_clone(root, repo["url"], dest, repo.get("branch"), repo.get("shallow", True) is not False, fields)
        plan.checks += [{**c, "id": f"{repo['name']}:{c['id']}"} for c in sub.checks if c["status"] != OK]
        item = sub.items[0]
        item.title = f"Clone {repo['name']} into {dest}"
        if item.skip is None:
            item.register = sub.register
        else:
            item.skip = "; ".join(c["detail"] for c in sub.checks if c["status"] == FAIL) or item.skip
        plan.items.append(item)
    # One failing repository does not block the others: its item is skipped with the reason.
    plan.checks = [{**c, "status": WARN if c["status"] == FAIL else c["status"]} for c in plan.checks]
    return plan


def _destination(root: str, destination: str, check) -> str | None:
    if not isinstance(destination, str) or not destination.strip():
        check("destination", False, "a destination folder is required")
        return None
    rel = os.path.normpath(destination)
    if destination.startswith("/") or rel == "." or rel.startswith("..") or ".." in Path(rel).parts:
        check("destination", False, f"{destination!r} must be a folder relative to the installation root, without ..")
        return None
    if any(part.startswith("-") or part == ".git" for part in Path(rel).parts):
        check("destination", False, f"{destination!r} has an invalid folder name")
        return None
    target = os.path.join(root, rel)
    # The deepest folder that exists must resolve inside the root: a symbolic link may not lead elsewhere.
    existing = target
    while not os.path.lexists(existing):
        existing = os.path.dirname(existing)
    real_root = os.path.realpath(root)
    real = os.path.realpath(existing)
    if not (real == real_root or real.startswith(real_root + "/")):
        check("destination", False, f"{existing} leads outside {root}")
        return None
    if os.path.lexists(target):
        if _dot_git(target) is not False:
            check("destination", False, f"{target} is already a repository; add it as an existing repository")
            return None
        if not os.path.isdir(target) or os.listdir(target):
            check("destination", False, f"{target} already exists and is not empty")
            return None
    check("destination", True, target)
    parent = enclosing_repo(os.path.dirname(target))
    if parent and os.path.realpath(parent[0]) != real_root:
        check("nested", False, f"inside the repository {parent[0]}; it will show as untracked files there", warn=True)
    writable = os.access(existing if os.path.isdir(existing) else os.path.dirname(existing), os.W_OK | os.X_OK)
    check("writable", writable, f"{existing} is writable" if writable else f"you cannot create folders in {existing}")
    return target


# -- run ----------------------------------------------------------------------

def changed_paths(repo: str, old: str | None, new: str | None, run=runner.run) -> list[str]:
    if not old or not new or old == new:
        return []
    out = run(runner.argv(repo, "diff", "--name-only", f"{old}..{new}"), timeout=30)
    return out.stdout.splitlines()[:MAX_CHANGED] if out.ok else []


def modules_of(repo: str, files: list[str]) -> list[str]:
    """Addon folders (holding __manifest__.py) that contain the changed files. Guidance only: a changed file
    does not always need a module upgrade."""
    found: set[str] = set()
    for rel in files:
        parts = Path(rel).parts
        for depth in range(len(parts) - 1, 0, -1):
            folder = os.path.join(repo, *parts[:depth])
            if os.path.isfile(os.path.join(folder, "__manifest__.py")):
                found.add(os.path.relpath(folder, repo))
                break
    if any(len(Path(rel).parts) == 1 for rel in files) and os.path.isfile(os.path.join(repo, "__manifest__.py")):
        found.add(".")
    return sorted(found)


def _head(repo: str, run=runner.run) -> str | None:
    out = run(runner.argv(repo, "rev-parse", "HEAD"))
    return out.stdout.strip() if out.ok else None


async def run_plan(plan: Plan, report: runner.Report, cancelled: Callable[[], bool] = lambda: False,
                   registry_file: Path | None = None) -> dict:
    """Execute a plan repository by repository. Returns {results, counts}; never raises for one repository."""
    if any(c["status"] == FAIL for c in plan.checks):
        raise OpError("; ".join(c["detail"] for c in plan.checks if c["status"] == FAIL))
    results = []
    for item in plan.items:
        row = {"repo": item.repo, "commands": [runner.display(c) for c in item.commands], "status": "ok",
               "output": "", "problem": None, "reason": item.skip}
        if item.skip is not None and item.keep and item.register:
            reg = item.register
            registry.register(reg["repo"], reg["installation"], reg["fields"], registry_file)
            row.update(status="kept", addons_path_entry=addons_entry(reg["repo"]))
        elif item.skip is not None:
            row["status"] = "skipped"
        elif cancelled():
            row["status"], row["reason"] = "cancelled", "cancelled before it started"
        else:
            report({"step": item.repo, "status": "start", "text": item.title})
            before = _head(item.repo) if plan.op == "pull" else None
            for cmd in item.commands:
                result = await runner.stream(cmd, report, item.repo)
                row["output"] = result.stdout
                if not result.ok:
                    row["status"] = "failed"
                    row["problem"] = explain.from_stderr(result.stdout, item.repo) or explain.problem(
                        "failed", explain.ERROR, f"git exited with code {result.code}", result.stdout[-500:])
                    break
            if row["status"] == "ok" and plan.op == "pull":
                files = changed_paths(item.repo, before, _head(item.repo))
                row.update(changed_files=len(files), changed_modules=modules_of(item.repo, files))
            reg = item.register or (plan.register if plan.op == "clone" else None)
            if row["status"] == "ok" and reg:
                registry.register(reg["repo"], reg["installation"], reg["fields"], registry_file)
                row["addons_path_entry"] = addons_entry(reg["repo"])
            report({"step": item.repo, "status": "ok" if row["status"] == "ok" else "fail",
                    "text": row["problem"]["title"] if row["problem"] else ""})
        results.append(row)
    counts = {k: sum(r["status"] == k for r in results) for k in ("ok", "kept", "failed", "skipped", "cancelled")}
    return {"op": plan.op, "results": results, "counts": counts}


def addons_entry(repo: str) -> str | None:
    """The folder to put on addons_path for a cloned repository: the repository itself when it holds modules,
    its parent when it is a single module, None when it holds no module at depth 1."""
    if os.path.isfile(os.path.join(repo, "__manifest__.py")):
        return os.path.dirname(repo)
    try:
        with os.scandir(repo) as entries:
            if any(e.is_dir() and os.path.isfile(os.path.join(e.path, "__manifest__.py")) for e in entries):
                return repo
    except OSError:
        pass
    return None
