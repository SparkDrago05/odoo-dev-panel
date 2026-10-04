"""Phase 4 Doctor: read-only checks over discovery, and repairs (only the venv rebuild so far)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from .checks import CHECKS, ERROR, INFO, WARNING, Context, Finding

_logger = logging.getLogger(__name__)
_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


def run(snapshot: dict | None = None, roots: list[Path] | None = None, with_databases: bool = True,
        context: Context | None = None) -> dict:
    """Scan (unless a snapshot is given), run every check, return findings sorted by severity."""
    if context is None:
        if snapshot is None:
            from ..discover import scan

            snapshot = scan.scan(roots, with_databases)
        context = Context(snapshot)
    findings: list[Finding] = []
    failed: list[str] = []
    for check in CHECKS:
        try:
            findings += check(context)
        except Exception as exc:  # noqa: BLE001  one broken check must not hide the others
            _logger.exception("doctor check %s failed", check.__name__)
            failed.append(f"{check.__name__}: {exc}")
    findings.sort(key=lambda f: (_ORDER[f.severity], f.check, f.subject))
    db_errors = [f"{d['installation']}: {d['error']}" for d in context.snapshot.get("databases", []) if d.get("error")]
    return {
        "findings": [{**asdict(f), "why": f.why} for f in findings],
        "counts": {s: sum(f.severity == s for f in findings) for s in (ERROR, WARNING, INFO)},
        # What the doctor could not look at, so "no finding" is not read as "all fine".
        "not_checked": failed + [f"databases of {e}" for e in db_errors]
                       + [f"unreadable: {u}" for u in context.snapshot.get("unreadable", [])],
    }
