"""Parse Odoo server output: split it into log records, filter by level, group repeated problems.

Odoo 14-19 write ``<date> <time>,<ms> <pid> <LEVEL> <db> <logger>: <message> <perf>``
(odoo/netsvc.py). A traceback, and any other line that does not start a record, belongs to the
record before it. Odoo 17+ may write ``_Traceback_`` instead of ``Traceback``. Output from a PTY
session carries colour codes around the level; they are removed first.

Pure text in, plain data out: the caller reads the session log (``read_tail``) and decides what to show.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_RANK = {name: i for i, name in enumerate(LEVELS)}
PROBLEM = "WARNING"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_RECORD = re.compile(
    r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (?P<pid>\d+) (?P<level>[A-Z_]+) (?P<db>\S+) (?P<logger>[^\s:]+): ?(?P<message>.*)$"
)
# Odoo's perf info at the end of each record: "- - -" or "<queries> <query time> <other time>",
# plus a cursor mode field when a replica is configured (18+).
_PERF = re.compile(r" (?:- - -|\d+ \d+\.\d{3} \d+\.\d{3})(?: [\w-]+)?$")
_TRACEBACK = re.compile(r"^_?Traceback_? \(most recent call last\):")
_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)')
_EXCEPTION = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<text>.*))?$")
# Parts of a message that change between repeats of the same problem.
_VOLATILE = re.compile(r"0x[0-9a-fA-F]+|\b\d+(?:\.\d+)?\b|'[^']*'|\"[^\"]*\"|\([^()]*\)")


@dataclass
class Record:
    line: int  # 1-based line number in the parsed text
    level: str | None  # None: output that is not an Odoo log record (pip, a print, a crash before logging starts)
    message: str
    time: str = ""
    pid: int = 0
    db: str = ""
    logger: str = ""
    extra: list[str] = field(default_factory=list)  # continuation lines: traceback, multi-line message

    @property
    def text(self) -> str:
        return "\n".join([self.message, *self.extra])

    def traceback(self) -> dict | None:
        """Exception type, message and innermost frame of the last traceback in the record, or None."""
        start = next((i for i, x in enumerate(self.extra) if _TRACEBACK.match(x)), None)
        if start is None:
            return None
        frame = None
        exc = None
        for raw in self.extra[start + 1:]:
            m = _FRAME.match(raw)
            if m:
                frame = {"file": m["file"], "line": int(m["line"]), "function": m["func"]}
                continue
            if raw.strip() and not raw.startswith((" ", "\t")):
                m = _EXCEPTION.match(raw.strip())
                if m and not _TRACEBACK.match(raw):
                    # Chained exceptions: the last one wins, which is the one that reached the logger.
                    exc = {"type": m["type"], "text": (m["text"] or "").strip()}
        if exc is None:
            return None
        return {**exc, "frame": frame}


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text).replace("\r", "")


def parse(text: str) -> list[Record]:
    records: list[Record] = []
    current: Record | None = None
    for number, raw in enumerate(strip_ansi(text).split("\n"), start=1):
        m = _RECORD.match(raw)
        if m and m["level"] in _RANK:
            current = Record(
                line=number, level=m["level"], message=_PERF.sub("", m["message"]), time=m["time"],
                pid=int(m["pid"]), db=m["db"], logger=m["logger"],
            )
            records.append(current)
        elif current is not None and (raw.strip() or current.extra):
            current.extra.append(raw)
        elif raw.strip():
            current = Record(line=number, level=None, message=raw)
            records.append(current)
    for record in records:
        while record.extra and not record.extra[-1].strip():
            record.extra.pop()
    return records


def at_least(level: str | None, minimum: str) -> bool:
    return level is not None and _RANK.get(level, -1) >= _RANK[minimum]


class LevelFilter:
    """Streaming filter: keep records at ``minimum`` or above, with their tracebacks.

    ``feed`` takes arbitrary chunks and returns whole lines only; ``flush`` returns the rest.
    Output that is not an Odoo record is kept when ``keep_other`` is set (a crash before logging starts).
    """

    def __init__(self, minimum: str, keep_other: bool = True):
        if minimum not in _RANK:
            raise ValueError(f"unknown level {minimum!r}; use one of {', '.join(LEVELS)}")
        self.minimum = minimum
        self.keep_other = keep_other
        self._pending = ""
        self._keep: bool | None = None  # None until the first record; then the decision for its continuation lines

    def _line(self, raw: str) -> bool:
        m = _RECORD.match(strip_ansi(raw))
        if m and m["level"] in _RANK:
            self._keep = at_least(m["level"], self.minimum)
        return self.keep_other if self._keep is None else self._keep

    def feed(self, chunk: str) -> str:
        lines = (self._pending + chunk).split("\n")
        self._pending = lines.pop()
        return "".join(raw + "\n" for raw in lines if self._line(raw))

    def flush(self) -> str:
        rest, self._pending = self._pending, ""
        if rest and self._line(rest):
            return rest
        return ""


def _short_path(path: str) -> str:
    """Path from the addon or Odoo package onward, so the same code in two checkouts groups together."""
    parts = path.replace("\\", "/").split("/")
    for anchor in ("addons", "odoo", "site-packages"):
        if anchor in parts:
            i = len(parts) - 1 - parts[::-1].index(anchor)
            return "/".join(parts[i + 1:] if anchor == "site-packages" else parts[i:])
    return "/".join(parts[-3:])


def fingerprint(record: Record) -> tuple[str, str]:
    """(key, title) that stay the same across repeats of one problem."""
    tb = record.traceback()
    if tb:
        frame = tb["frame"]
        where = f"{_short_path(frame['file'])}:{frame['function']}" if frame else ""
        key = f"tb|{tb['type']}|{where}"
        title = f"{tb['type']}: {tb['text']}" if tb["text"] else tb["type"]
    else:
        key = f"msg|{record.level}|{record.logger}|{_VOLATILE.sub('#', record.message)}"
        title = record.message
    return hashlib.sha1(key.encode()).hexdigest()[:12], title


def analyze(text: str, minimum: str = PROBLEM) -> dict:
    """Counts per level, plus problems (records at ``minimum`` or above) grouped by fingerprint.

    Groups are sorted worst level first, then most repeats, then latest.
    """
    records = parse(text)
    counts = {name: 0 for name in LEVELS}
    groups: dict[str, dict] = {}
    for record in records:
        if record.level is None:
            continue
        counts[record.level] += 1
        if not at_least(record.level, minimum):
            continue
        key, title = fingerprint(record)
        group = groups.get(key)
        if group is None:
            tb = record.traceback()
            group = groups[key] = {
                "id": key, "level": record.level, "logger": record.logger, "title": title,
                "exception": tb["type"] if tb else None,
                "frame": tb["frame"] if tb else None,
                "count": 0, "first_line": record.line, "first_time": record.time, "dbs": [],
                "sample": record.text,
            }
        group["count"] += 1
        group["last_line"] = record.line
        group["last_time"] = record.time
        if _RANK[record.level] > _RANK[group["level"]]:
            group["level"] = record.level
        if record.db not in ("?", "") and record.db not in group["dbs"]:
            group["dbs"].append(record.db)
    ordered = sorted(groups.values(), key=lambda g: (-_RANK[g["level"]], -g["count"], -g["last_line"]))
    first_error = next((r.line for r in records if at_least(r.level, "ERROR")), None)
    return {"counts": counts, "groups": ordered, "first_error_line": first_error, "lines": text.count("\n")}


async def read_tail(conn, session_id: str, max_bytes: int = 8 * 1024 * 1024) -> tuple[str, int]:
    """Read the last ``max_bytes`` of a session log through its agent. Returns (text, start offset).

    When the log is longer, the text starts at the first whole line after the cut.
    """
    session = await conn.request("session.get", {"id": session_id})
    size = int(session.get("log_size") or 0)
    start = max(0, size - max_bytes)
    offset = start
    parts: list[str] = []
    while True:
        chunk = await conn.request("session.read", {"id": session_id, "offset": offset, "limit": 256 * 1024})
        if not chunk["data"]:
            break
        parts.append(chunk["data"])
        offset = chunk["offset"]
    text = "".join(parts)
    if start > 0:
        cut = text.find("\n")
        text = text[cut + 1:] if cut >= 0 else ""
    return text, start
