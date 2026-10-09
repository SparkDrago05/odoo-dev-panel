"""W10: open a repository folder in the IDE, a terminal or the file manager, as the developer.

The command is an argv list (no shell). ODP_IDE overrides the editor, e.g. ``ODP_IDE=pycharm``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

IDES = ("code", "codium", "code-oss", "pycharm", "pycharm-professional", "pycharm-community", "subl", "zed")
TERMINALS = (
    ("gnome-terminal", ["--working-directory={path}"]),
    ("ptyxis", ["--working-directory={path}"]),
    ("konsole", ["--workdir", "{path}"]),
    ("xfce4-terminal", ["--working-directory={path}"]),
    ("kitty", ["--directory", "{path}"]),
    ("alacritty", ["--working-directory", "{path}"]),
    ("x-terminal-emulator", []),          # Debian alternative: starts in its cwd
)
TARGETS = ("ide", "terminal", "files")


class OpenError(ValueError):
    pass


def command(path: str, target: str, which=shutil.which) -> list[str]:
    if target == "ide":
        wanted = os.environ.get("ODP_IDE")
        names = [wanted] if wanted else list(IDES)
        for name in names:
            parts = shlex.split(name)
            exe = which(parts[0]) if parts else None
            if exe:
                return [exe, *parts[1:], path]
        raise OpenError("no IDE found (looked for " + ", ".join(names) + "); set ODP_IDE")
    if target == "terminal":
        for name, args in TERMINALS:
            exe = which(name)
            if exe:
                return [exe, *[a.format(path=path) for a in args]]
        raise OpenError("no terminal emulator found")
    if target == "files":
        exe = which("xdg-open")
        if exe:
            return [exe, path]
        raise OpenError("xdg-open is not installed")
    raise OpenError(f"target must be one of {', '.join(TARGETS)}")


def open_path(path: str, target: str) -> list[str]:
    """Start the program detached (it outlives the app) and return the argv that was used."""
    if not os.path.isdir(path):
        raise OpenError(f"{path} is not a folder")
    argv = command(path, target)
    subprocess.Popen(argv, cwd=path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return argv


def file_command(file: str, line: int | None, which=shutil.which) -> list[str]:
    """Q3: the IDE command that opens ``file`` at ``line``: code -g file:line, pycharm --line N file, others file:line."""
    ide = command("/", "ide", which)[:-1]  # the IDE argv without the folder
    name = os.path.basename(ide[0])
    if line is None:
        return [*ide, file]
    if name.startswith("pycharm"):
        return [*ide, "--line", str(line), file]
    if name in ("code", "codium", "code-oss"):
        return [*ide, "-g", f"{file}:{line}"]
    return [*ide, f"{file}:{line}"]


def open_file(file: str, line: int | None = None) -> list[str]:
    """Open a source file (from a traceback) in the IDE as the developer. Only existing regular files."""
    if not isinstance(file, str) or not file.startswith("/"):
        raise OpenError("file must be an absolute path")
    if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
        raise OpenError("line is a positive number")
    if not os.path.isfile(file):
        raise OpenError(f"{file} is not a file on this machine")
    argv = file_command(file, line)
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return argv
