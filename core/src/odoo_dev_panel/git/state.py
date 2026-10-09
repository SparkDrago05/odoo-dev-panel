"""W2: the state of one repository, read with a few bounded, read-only Git commands."""

from __future__ import annotations

import os
import pwd
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from . import explain, runner, urls

WORKERS = 4


@dataclass
class RepoState:
    path: str
    ok: bool = True                     # Git could read the repository
    owner: str | None = None
    foreign: bool = False               # owned by another user: read-only here
    branch: str | None = None           # None when detached
    head: str | None = None             # full commit id
    detached: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    conflicted: int = 0
    shallow: bool = False
    worktree: bool = False              # a linked worktree (its .git is a file)
    remotes: dict[str, str] = field(default_factory=dict)   # name -> redacted fetch URL
    subject: str | None = None          # last commit subject
    committed: str | None = None        # last commit date, ISO
    error: str | None = None
    problems: list[dict] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.modified or self.conflicted)

    def as_dict(self) -> dict:
        return {**asdict(self), "dirty": self.dirty}


def owner_of(path: str) -> tuple[str | None, bool]:
    try:
        uid = os.stat(path).st_uid
    except OSError:
        return None, False
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        name = str(uid)
    return name, uid != os.getuid()


def parse_status(text: str, state: RepoState) -> None:
    """``git status --porcelain=v2 --branch -z``."""
    records = iter(text.split("\0"))
    for record in records:
        if not record:
            continue
        if record.startswith("# branch.oid "):
            oid = record[13:]
            state.head = None if oid == "(initial)" else oid
        elif record.startswith("# branch.head "):
            head = record[14:]
            state.detached = head == "(detached)"
            state.branch = None if state.detached else head
        elif record.startswith("# branch.upstream "):
            state.upstream = record[18:]
        elif record.startswith("# branch.ab "):
            ahead, behind = record[12:].split()
            state.ahead, state.behind = int(ahead), abs(int(behind))
        elif record[0] in "12":
            if record[0] == "2":
                next(records, None)  # a rename carries the original path as the next field
            xy = record[2:4]
            state.staged += xy[0] != "."
            state.modified += xy[1] != "."
        elif record[0] == "u":
            state.conflicted += 1
        elif record[0] == "?":
            state.untracked += 1


def parse_remotes(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "(fetch)":
            out[parts[0]] = urls.redact(parts[1])
    return out


def inspect(path: str, gitdir: str | None = None, run=runner.run) -> RepoState:
    owner, foreign = owner_of(path)
    state = RepoState(path=path, owner=owner, foreign=foreign, worktree=gitdir is not None)
    if gitdir is not None and (not gitdir or not os.path.isdir(gitdir)):
        state.ok, state.error = False, f".git points to {gitdir or '(nothing)'}, which does not exist"
        state.problems = explain.for_state(state)
        return state

    def git(*args: str) -> runner.GitResult:
        return run(runner.argv(path, *args, foreign=foreign))

    status = git("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=normal")
    if not status.ok:
        state.ok, state.error = False, status.stderr.strip() or f"git status failed ({status.code})"
        state.problems = explain.for_state(state)
        return state
    parse_status(status.stdout, state)
    shallow = git("rev-parse", "--is-shallow-repository")
    state.shallow = shallow.ok and shallow.stdout.strip() == "true"
    state.remotes = parse_remotes(git("remote", "-v").stdout)
    if state.head:
        log = git("log", "-1", "--format=%cI%x00%s")
        if log.ok and "\0" in log.stdout:
            state.committed, state.subject = log.stdout.strip().split("\0", 1)
    state.problems = explain.for_state(state)
    return state


def inspect_many(items: list[tuple[str, str | None]], run=runner.run) -> list[RepoState]:
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(lambda item: inspect(item[0], item[1], run), items))
