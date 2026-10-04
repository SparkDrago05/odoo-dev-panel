"""requirements.txt files: names and environment markers, without the ``packaging`` library (the core is stdlib only).

Only what Odoo's and typical addon requirement files use is supported: one requirement per line, an optional
version specifier, an optional ``; marker``, comments. Options (``-r``, ``-e``, ``--hash``) and URLs are skipped.
"""

from __future__ import annotations

import platform
import re
import sys
from dataclasses import dataclass

from ..discover.venv import normalize

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?")
_TOKEN = re.compile(r"""\s*(?:(?P<str>'[^']*'|"[^"]*")|(?P<op>===|==|!=|<=|>=|~=|<|>|\(|\))|(?P<word>[A-Za-z_][A-Za-z0-9_.]*))""")
_VERSION_VARS = {"python_version", "python_full_version", "implementation_version", "platform_release"}

# A requirement is also met by these distributions (Odoo pins psycopg2; many venvs hold the binary wheel).
ALIASES = {"psycopg2": {"psycopg2-binary"}, "pypdf2": {"pypdf"}}


@dataclass
class Requirement:
    name: str  # normalized
    raw: str  # the line without its comment
    marker: str | None = None


class MarkerError(ValueError):
    pass


def parse(text: str) -> list[Requirement]:
    result = []
    for line in text.splitlines():
        line = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()
        if not line or line.startswith("-") or "://" in line or line.startswith((".", "/")):
            continue
        spec, _, marker = line.partition(";")
        match = _NAME.match(spec)
        if match:
            result.append(Requirement(name=normalize(match.group(1)), raw=line, marker=marker.strip() or None))
    return result


def environment(python_version: str) -> dict[str, str]:
    """Marker variables for a venv that runs ``python_version`` on this machine."""
    return {
        "python_version": python_version,
        "python_full_version": python_version + ".0",
        "implementation_version": python_version + ".0",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "sys_platform": sys.platform,
        "os_name": "posix",
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "platform_release": platform.release(),
    }


def _version(value: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.findall(r"\d+|[a-z]+", value.lower()))


def _compare(left: str, op: str, right: str, as_version: bool) -> bool:
    if op in ("in", "not in"):
        return (left in right) == (op == "in")
    a, b = (_version(left), _version(right)) if as_version else (left, right)
    if op in ("==", "==="):
        return a == b
    if op == "!=":
        return a != b
    if op == "~=":
        parts = right.split(".")
        upper = _version(".".join(parts[:-1])) if len(parts) > 1 else None
        return a >= b and (upper is None or a[: len(upper)] == upper)
    try:
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    except (KeyError, TypeError) as exc:
        raise MarkerError(f"cannot compare with {op}") from exc


def evaluate(marker: str, env: dict[str, str]) -> bool:
    """Evaluate a PEP 508 marker. Raises MarkerError for anything it does not understand."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    marker = marker.strip()
    while pos < len(marker):
        match = _TOKEN.match(marker, pos)
        if not match or match.end() == pos:
            raise MarkerError(f"cannot read marker: {marker!r}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        pos = match.end()
        while pos < len(marker) and marker[pos].isspace():
            pos += 1
    index = 0

    def peek() -> tuple[str, str] | None:
        return tokens[index] if index < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal index
        if index >= len(tokens):
            raise MarkerError(f"marker ends early: {marker!r}")
        index += 1
        return tokens[index - 1]

    def value() -> tuple[str, bool]:
        kind, text = take()
        if kind == "str":
            return text[1:-1], False
        if kind == "word" and text in env:
            return env[text], text in _VERSION_VARS
        raise MarkerError(f"unknown marker value {text!r}")

    def comparison() -> bool:
        if peek() == ("op", "("):
            take()
            result = disjunction()
            if take() != ("op", ")"):
                raise MarkerError(f"missing ')' in {marker!r}")
            return result
        left, left_version = value()
        kind, op = take()
        if kind == "word" and op == "not":
            if take() != ("word", "in"):
                raise MarkerError(f"expected 'not in' in {marker!r}")
            op = "not in"
        elif kind == "word" and op != "in":
            raise MarkerError(f"unknown operator {op!r}")
        right, right_version = value()
        return _compare(left, op, right, left_version or right_version)

    def conjunction() -> bool:
        result = comparison()
        while peek() == ("word", "and"):
            take()
            result = comparison() and result
        return result

    def disjunction() -> bool:
        result = conjunction()
        while peek() == ("word", "or"):
            take()
            result = conjunction() or result
        return result

    result = disjunction()
    if index != len(tokens):
        raise MarkerError(f"unexpected text in marker {marker!r}")
    return result


def applicable(reqs: list[Requirement], env: dict[str, str]) -> tuple[list[Requirement], list[Requirement]]:
    """(requirements that apply to env, requirements whose marker could not be read)."""
    apply, unknown = [], []
    for req in reqs:
        if req.marker is None:
            apply.append(req)
            continue
        try:
            if evaluate(req.marker, env):
                apply.append(req)
        except MarkerError:
            unknown.append(req)
    return apply, unknown


def missing(reqs: list[Requirement], installed: dict[str, str]) -> list[str]:
    """Names (normalized) of requirements without an installed distribution, in file order, no duplicates."""
    result: list[str] = []
    for req in reqs:
        if req.name in installed or ALIASES.get(req.name, set()) & installed.keys():
            continue
        if req.name not in result:
            result.append(req.name)
    return result
