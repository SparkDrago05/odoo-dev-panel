"""The repository list the CLI and the app show: discovery + registry + live Git state + branch alignment."""

from __future__ import annotations

import os
from pathlib import Path

from . import discover, explain, registry, runner, state


def listing(snapshot: dict, registry_file: Path | None = None, installation: str | None = None,
            with_state: bool = True, run=runner.run) -> dict:
    data = registry.load(registry_file)
    rows = discover.collect(snapshot, data)
    if installation:
        rows = [r for r in rows if any(i["root"] == installation for i in r["installations"])]
    versions = {i["root"]: i.get("version") for i in snapshot.get("installations", [])}
    if with_state:
        live = [r for r in rows if not r.get("missing")]
        states = state.inspect_many([(r["path"], r["gitdir"]) for r in live], run)
        for row, s in zip(live, states):
            row["state"] = s.as_dict()
    for row in rows:
        row.setdefault("state", None)
        row["name"] = os.path.basename(row["path"].rstrip("/"))
        _align(row, versions)
    return {"repos": rows, "scan_roots": data.get("scan_roots", [])}


def _align(row: dict, versions: dict) -> None:
    s = row["state"]
    for link in row["installations"]:
        assoc = link.get("assoc") or {}
        found = explain.branch_alignment(s["branch"] if s else None, assoc.get("purpose") or row["purpose"],
                                         assoc.get("expected_version") or versions.get(link["root"]),
                                         assoc.get("preferred_branch"))
        link["alignment"] = found
        if found and s is not None and not any(p["code"] == "branch-mismatch" for p in s["problems"]):
            s["problems"].append(found)


def resolve(path: str, snapshot: dict, registry_file: Path | None = None) -> dict:
    """The known repository at ``path``. Only discovered or registered repositories are acted on."""
    real = os.path.realpath(path)
    for row in discover.collect(snapshot, registry.load(registry_file)):
        if row["real"] == real and not row.get("missing"):
            return row
    raise LookupError(f"{path} is not a known repository of a discovered installation")


def show(path: str, snapshot: dict, registry_file: Path | None = None, run=runner.run) -> dict:
    row = resolve(path, snapshot, registry_file)
    row["state"] = state.inspect(row["path"], row["gitdir"], run).as_dict()
    row["name"] = os.path.basename(row["path"].rstrip("/"))
    _align(row, {i["root"]: i.get("version") for i in snapshot.get("installations", [])})
    return row


MAX_DIFF = 256 * 1024


def diff(path: str, snapshot: dict, file: str | None = None, registry_file: Path | None = None,
         run=runner.run) -> dict:
    """Changed files, the diff of one file (bounded) and the commits between HEAD and its upstream."""
    row = resolve(path, snapshot, registry_file)
    repo, foreign = row["path"], state.owner_of(row["path"])[1]

    def git(*args: str) -> runner.GitResult:
        return run(runner.argv(repo, *args, foreign=foreign), timeout=20)

    files = []
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=normal")
    records = iter(status.stdout.split("\0")) if status.ok else iter(())
    for rec in records:
        if len(rec) < 4:
            continue
        code, name = rec[:2], rec[3:]
        if code[0] in "RC":
            next(records, None)
        files.append({"path": name, "index": code[0].strip(), "worktree": code[1].strip()})
    out = {"repo": repo, "files": files[:2000], "truncated": len(files) > 2000, "diff": None,
           "incoming": [], "outgoing": []}
    if file is not None:
        if file not in {f["path"] for f in files}:
            raise LookupError(f"{file} is not a changed file of {repo}")
        text = git("diff", "--no-color", "--no-ext-diff", "HEAD", "--", file).stdout
        out["diff"] = text[:MAX_DIFF]
        out["diff_truncated"] = len(text) > MAX_DIFF
    if git("rev-parse", "--verify", "--quiet", "@{u}").ok:
        fmt = "--format=%h%x00%an%x00%cI%x00%s"
        for key, rng in (("incoming", "HEAD..@{u}"), ("outgoing", "@{u}..HEAD")):
            log = git("log", "--max-count=50", fmt, rng)
            out[key] = [dict(zip(("sha", "author", "date", "subject"), line.split("\0", 3)))
                        for line in log.stdout.splitlines() if line.count("\0") == 3]
    return out
