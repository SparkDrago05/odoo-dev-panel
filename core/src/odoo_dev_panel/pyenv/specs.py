"""Y2: version specifiers (the PEP 440 subset requirement files use) without the ``packaging`` library.

``satisfies`` answers True, False, or None when it cannot tell (an unusual specifier or version). Conflicts are
only reported when they are certain from the specifiers; anything it cannot read is "unknown", never a guess.
"""

from __future__ import annotations

import re

_SPEC = re.compile(r"\s*(===|==|!=|<=|>=|~=|<|>)\s*([A-Za-z0-9.*+!_-]+)\s*")
_RELEASE = re.compile(r"^(\d+(?:\.\d+)*)(?:(a|b|rc|alpha|beta|c)(\d*))?(?:\.?post(\d*))?(?:\.?dev(\d*))?(?:\+.*)?$", re.I)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2}


def parse_version(text: str) -> tuple | None:
    """Comparable key: (release parts padded, pre, post, dev). None if not a plain PEP 440 version."""
    m = _RELEASE.match(text.strip().lstrip("vV"))
    if not m:
        return None
    release = tuple(int(p) for p in m.group(1).split("."))
    release = release + (0,) * (6 - len(release)) if len(release) < 6 else release
    pre = (_PRE_RANK[m.group(2).lower()], int(m.group(3) or 0)) if m.group(2) else (9, 0)
    post = int(m.group(4) or 0) if m.group(4) is not None else -1
    dev = int(m.group(5) or 0) if m.group(5) is not None else 10**9
    if m.group(5) is not None and not m.group(2):
        pre = (-1, 0)  # 1.0.dev1 sorts before 1.0a1
    return (release, pre, post, dev)


def split(spec: str) -> list[tuple[str, str]] | None:
    """'>=1.2,<2' -> [('>=', '1.2'), ('<', '2')]. None if any part cannot be read."""
    spec = spec.strip()
    if not spec:
        return []
    out = []
    for part in spec.split(","):
        m = _SPEC.fullmatch(part)
        if not m:
            return None
        out.append((m.group(1), m.group(2)))
    return out


def requirement_spec(raw: str) -> str:
    """The specifier of a requirement line: 'lxml==5.2.1 ; python_version > "3.10"' -> '==5.2.1'."""
    line = raw.split(";", 1)[0]
    line = re.sub(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?", "", line)
    return line.strip().strip("()").strip()


def _one(version: str, op: str, target: str) -> bool | None:
    if op == "===":
        return version == target
    if target.endswith(".*") and op in ("==", "!="):
        prefix = target[:-2].split(".")
        v = parse_version(version)
        if v is None:
            return None
        parts = [str(p) for p in v[0][:len(prefix)]]
        same = parts == [str(int(p)) for p in prefix if p.isdigit()]
        return same if op == "==" else not same
    a, b = parse_version(version), parse_version(target)
    if a is None or b is None:
        return None
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "~=":
        pieces = target.split(".")
        if len(pieces) < 2:
            return None
        upper = parse_version(".".join(pieces[:-1]))
        if upper is None:
            return None
        n = len(pieces) - 1
        return a >= b and a[0][:n] == upper[0][:n]
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def satisfies(version: str, spec: str) -> bool | None:
    parts = split(spec)
    if parts is None:
        return None
    result: bool | None = True
    for op, target in parts:
        ok = _one(version, op, target)
        if ok is None:
            result = None
        elif not ok:
            return False
    return result


def conflict(specs: list[str]) -> str | None:
    """A reason when no version can satisfy every specifier together, else None. Only certain cases:
    two different pins, a pin another specifier excludes, or a lower bound above an upper bound."""
    parsed = [split(s) for s in specs if s]
    if any(p is None for p in parsed):
        return None
    flat = [c for p in parsed for c in p]
    pins = sorted({t for op, t in flat if op in ("==", "===") and not t.endswith(".*")})
    if len(pins) > 1:
        return f"pinned to different versions: {', '.join(pins)}"
    if pins:
        bad = [f"{op}{t}" for op, t in flat if _one(pins[0], op, t) is False]
        return f"=={pins[0]} is excluded by {', '.join(bad)}" if bad else None
    lows = [(parse_version(t), op, t) for op, t in flat if op in (">", ">=", "~=") and parse_version(t)]
    highs = [(parse_version(t), op, t) for op, t in flat if op in ("<", "<=") and parse_version(t)]
    if lows and highs:
        low, high = max(lows), min(highs)
        if low[0] > high[0] or (low[0] == high[0] and (low[1] == ">" or high[1] == "<")):
            return f"{low[1]}{low[2]} and {high[1]}{high[2]} leave no version"
    return None
