"""W3: turn repository state and Git error text into stable problem codes with plain explanations.

Each problem: {code, level (info|warn|error), title, detail, commands, heuristic}. ``commands`` are
suggestions to copy; the app never runs them by itself. ``heuristic`` marks a guess, not a measured fact.
"""

from __future__ import annotations

import re
import shlex

INFO, WARN, ERROR = "info", "warn", "error"

# (code, level, title, detail, patterns) checked in order against Git's stderr.
_ERRORS = [
    ("dubious-ownership", ERROR, "Git refuses this folder (dubious ownership)",
     "The repository is owned by another user. Git will not change it as you.", ("dubious ownership",)),
    ("host-key", ERROR, "SSH host key problem",
     "The server's SSH key is unknown or changed. Verify it before trusting it; the app never skips this check.",
     ("Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED")),
    ("auth-failed", ERROR, "Authentication failed",
     "The remote refused your credentials. Check that your SSH agent has the right key, or that a Git credential "
     "helper is configured for this host.",
     ("Permission denied (publickey", "Authentication failed", "could not read Username", "terminal prompts disabled",
      "could not read Password", "Repository not found", "access denied", "HTTP Basic: Access denied")),
    ("network", ERROR, "Remote not reachable",
     "Git could not reach the remote host.",
     ("Could not resolve host", "Connection timed out", "Connection refused", "Network is unreachable",
      "unable to access")),
    ("remote-missing", ERROR, "Remote repository not found or not accessible",
     "The remote URL does not lead to a repository you can read. Check the URL and your access rights.",
     ("does not appear to be a git repository", "Could not read from remote repository")),
    ("local-changes", ERROR, "Local changes are in the way",
     "Your uncommitted changes would be overwritten. Commit or stash them yourself first.",
     ("would be overwritten", "Please commit your changes or stash them")),
    ("diverged", ERROR, "Branch has diverged",
     "Local and upstream both have commits the other lacks. A fast-forward is not possible; merge or rebase by hand.",
     ("Not possible to fast-forward", "have diverged", "diverging branches")),
    ("no-upstream", WARN, "No upstream branch",
     "The current branch does not track a remote branch.", ("no tracking information", "no upstream configured")),
    ("unknown-ref", ERROR, "Branch, tag or commit not found",
     "The remote or the local repository has no such ref. A shallow single-branch clone only knows one branch.",
     ("Remote branch", "not found in upstream", "invalid reference", "did not match any", "couldn't find remote ref",
      "unknown revision")),
    ("not-a-repo", ERROR, "Not a Git repository", "The folder is not (or no longer) a Git repository.",
     ("not a git repository",)),
]


def problem(code: str, level: str, title: str, detail: str = "", commands: list[str] | None = None,
            heuristic: bool = False) -> dict:
    return {"code": code, "level": level, "title": title, "detail": detail, "commands": commands or [],
            "heuristic": heuristic}


def from_stderr(text: str, path: str | None = None) -> dict | None:
    for code, level, title, detail, patterns in _ERRORS:
        if any(p.lower() in text.lower() for p in patterns):
            commands = []
            if code == "dubious-ownership" and path:
                commands = [f"# as the owner, or trust it for your user:\ngit config --global --add safe.directory {shlex.quote(path)}"]
            if code == "auth-failed":
                commands = ["ssh-add -l", "ssh -T git@<host>"]
            return problem(code, level, title, detail, commands)
    return None


def for_state(s) -> list[dict]:
    """Problems visible in a RepoState (see state.py)."""
    out: list[dict] = []
    if not s.ok:
        if s.worktree and s.error and "does not exist" in s.error:
            out.append(problem("broken-worktree", ERROR, "Broken worktree", s.error,
                               [f"# keep the files as a plain folder:\nrm {shlex.quote(s.path + '/.git')}"]))
        else:
            out.append(from_stderr(s.error or "", s.path) or problem("unreadable", ERROR, "Git cannot read this repository",
                                                                     s.error or ""))
        return out
    if s.foreign:
        out.append(problem("not-owner", INFO, f"Owned by {s.owner}",
                           "Shown read-only. Fetch, pull and switch run only on repositories you own."))
    if s.conflicted:
        out.append(problem("conflict", ERROR, f"{s.conflicted} conflicted file(s)", "Resolve the conflicts first."))
    if s.staged or s.modified:
        out.append(problem("dirty", WARN, "Uncommitted changes",
                           f"{s.staged} staged, {s.modified} modified. Pull and switch are blocked until you commit or stash."))
    if s.untracked:
        out.append(problem("untracked", INFO, f"{s.untracked} untracked file(s)", "They are never deleted by the app."))
    if s.detached:
        out.append(problem("detached", WARN, "Detached HEAD", "Not on a branch; pull is not possible."))
    elif s.head is None:
        out.append(problem("empty", INFO, "No commits yet"))
    if not s.remotes:
        out.append(problem("no-remote", WARN, "No remote", "This repository has no remote to fetch from."))
    elif not s.detached and s.branch and not s.upstream:
        out.append(problem("no-upstream", WARN, "No upstream branch", f"{s.branch} does not track a remote branch.",
                           [f"git -C {shlex.quote(s.path)} branch --set-upstream-to=origin/{s.branch}"]))
    if s.ahead and s.behind:
        out.append(problem("diverged", WARN, "Diverged from upstream",
                           f"{s.ahead} local and {s.behind} upstream commits. Fast-forward is not possible."))
    if s.shallow:
        out.append(problem("shallow", INFO, "Shallow clone", "History is truncated; ahead/behind counts may be partial."))
    return out


_MAJOR = re.compile(r"(?<!\d)(1[0-9]|2[0-9])(?:\.0)?(?!\d)")


def branch_alignment(branch: str | None, purpose: str, version: str | None, preferred: str | None = None) -> dict | None:
    """Is the branch right for the installation's Odoo version? Community/Enterprise/themes follow NN.0 (fact);
    custom branches are only guessed from a number in their name (heuristic)."""
    if not branch or not version:
        return None
    major = version.split(".")[0]
    if preferred:
        if branch != preferred:
            return problem("branch-mismatch", WARN, f"On {branch}, expected {preferred}",
                           "The association names a preferred branch.")
        return None
    if purpose in ("community", "enterprise", "themes"):
        if not (branch == f"{major}.0" or branch.startswith(f"saas-{major}.")):
            return problem("branch-mismatch", WARN, f"On {branch}, installation is Odoo {major}",
                           f"{purpose.title()} branches for Odoo {major} are named {major}.0.")
        return None
    numbers = _MAJOR.findall(branch)
    if numbers and major not in numbers:
        return problem("branch-mismatch", INFO, f"{branch} may not match Odoo {major}",
                       f"The branch name mentions {', '.join(sorted(set(numbers)))}. This is a guess from the name.",
                       heuristic=True)
    return None
