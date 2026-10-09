"""U8: the log of an Odoo process started outside the app. Its stdout is not reachable, so the log comes from the
config's ``logfile`` (read as the developer, only when readable) or, for a systemd unit, its journal.
Read-only; nothing is changed to make a log readable.
"""

from __future__ import annotations

import os
import pwd
import stat

MAX_BYTES = 2 * 1024 * 1024


class ExtLogError(ValueError):
    pass


def _owner(st: os.stat_result) -> str:
    try:
        return pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        return str(st.st_uid)


def source(snapshot: dict, config: str) -> dict:
    """Where the log of ``config`` can be read: {kind: logfile|journal|None, path, unit, reason}."""
    inst = next((i for i in snapshot["instances"] if i["path"] == os.path.normpath(config or "")), None)
    if inst is None:
        raise ExtLogError(f"{config} is not a discovered Odoo config")
    out = {"kind": None, "path": None, "unit": None, "reason": None}
    raw = (inst.get("options") or {}).get("logfile")
    unit = next((u["name"] for u in snapshot.get("units", []) if u.get("config") == inst["path"]), None)
    reasons = []
    if raw and raw.strip() not in ("", "False", "None"):
        path = raw.strip()
        if not path.startswith("/"):
            reasons.append(f"logfile {path} is relative to the directory Odoo was started in")
        else:
            try:
                st = os.stat(path)
            except OSError as exc:
                reasons.append(f"logfile {path}: {exc.strerror}")
            else:
                if not stat.S_ISREG(st.st_mode):
                    reasons.append(f"logfile {path} is not a regular file")
                elif not os.access(path, os.R_OK):
                    reasons.append(f"logfile {path} is not readable by you (owner {_owner(st)}, mode "
                                   f"{stat.S_IMODE(st.st_mode):o}); make it group-readable to see it here")
                else:
                    out.update(kind="logfile", path=path)
                    return out
    else:
        reasons.append("the config sets no logfile, so Odoo writes to the stdout of whoever started it")
    if unit:
        out.update(kind="journal", unit=unit)
        return out
    out["reason"] = "; ".join(reasons)
    return out


def read(path: str, offset: int | None = None, max_bytes: int = MAX_BYTES) -> dict:
    """New text of a log file from ``offset`` (None: its last ``max_bytes``, from a line start).
    {text, offset, start, size, rotated}. A file shorter than ``offset`` was rotated: read its tail again."""
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            rotated = offset is not None and offset > size
            if offset is None or rotated:
                start = max(0, size - max_bytes)
                fh.seek(start)
                data = fh.read(size - start)
                if start and b"\n" in data:
                    cut = data.index(b"\n") + 1
                    data, start = data[cut:], start + cut
            else:
                start = offset
                fh.seek(offset)
                data = fh.read(min(size - offset, max_bytes))
    except OSError as exc:
        raise ExtLogError(f"cannot read {path}: {exc.strerror}") from exc
    return {"text": data.decode("utf-8", "replace"), "offset": start + len(data), "start": start, "size": size,
            "rotated": rotated}
