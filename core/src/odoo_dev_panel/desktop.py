"""Native file chooser for the app, run as the developer. zenity (GNOME, most desktops) or kdialog (KDE).

Only a convenience for typing a path: the chosen path goes back into a form field and is validated by the
operation that uses it (for example provision preflight checks the archive is readable). The CLI takes paths.
"""

from __future__ import annotations

import asyncio
import os
import shutil

ARCHIVES = ("*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.tbz2")
FILTERS = {"archive": ("Archives", ARCHIVES), "any": ("All files", ("*",))}


class PickError(Exception):
    pass


def command(title: str, kind: str, start: str, which=shutil.which) -> list[str]:
    label, patterns = FILTERS[kind]
    start_dir = start if os.path.isdir(start) else os.path.dirname(start) if os.path.isdir(os.path.dirname(start)) \
        else os.path.expanduser("~")
    if which("zenity"):
        return ["zenity", "--file-selection", f"--title={title}", f"--filename={start_dir.rstrip('/')}/",
                f"--file-filter={label} | {' '.join(patterns)}", "--file-filter=All files | *"]
    if which("kdialog"):
        return ["kdialog", "--title", title, "--getopenfilename", start_dir, f"{' '.join(patterns)}|{label}"]
    raise PickError("no file chooser found: install zenity or kdialog, or type the path")


async def pick_file(title: str = "Choose a file", kind: str = "any", start: str = "") -> str | None:
    """The chosen absolute path, or None when the developer cancels."""
    if kind not in FILTERS:
        raise PickError(f"kind must be one of {', '.join(FILTERS)}")
    cmd = command(title[:120], kind, start or os.path.expanduser("~"))
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    path = out.decode(errors="replace").strip()
    if proc.returncode != 0 or not path:
        return None  # cancelled or closed
    if not os.path.isabs(path):
        raise PickError(f"the file chooser returned {path!r}, not an absolute path")
    return path
