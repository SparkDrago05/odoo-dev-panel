"""Remote URLs: what may be cloned, and how a URL is shown. Credentials never pass through the app."""

from __future__ import annotations

import re

# Same shape as the provision spec, plus file:// and absolute local paths (bundles, local mirrors).
_URL = re.compile(r"^(https?://|ssh://|git@|file://)[^\s'\"\\]+$")
_LOCAL = re.compile(r"^/[^\s'\"\\]*$")
_USERINFO = re.compile(r"^(?P<scheme>[a-z+]+://)(?P<user>[^/@]*)@")


class UrlError(ValueError):
    pass


def validate(url: str) -> str:
    """Return the URL, or raise UrlError. A password inside the URL is refused: it would land in .git/config,
    in plans and in logs. Use an SSH key or a Git credential helper instead."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise UrlError("a remote URL is required")
    if url.startswith("-"):
        raise UrlError(f"invalid URL {url!r}")
    if not (_URL.match(url) or _LOCAL.match(url)):
        raise UrlError(f"unsupported URL {url!r}: use https://, ssh://, git@host:path, file:// or an absolute path")
    match = _USERINFO.match(url)
    if match and ":" in match.group("user"):
        raise UrlError("the URL contains a password; use an SSH key or a Git credential helper instead")
    if match and match.group("scheme").startswith("http") and match.group("user"):
        raise UrlError("the URL contains a user or token; use a Git credential helper instead")
    return url


def redact(url: str) -> str:
    """Hide anything between scheme:// and @ except a plain SSH user (ssh://git@host stays readable)."""
    match = _USERINFO.match(url)
    if not match:
        return url
    user = match.group("user")
    if match.group("scheme") == "ssh://" and ":" not in user:
        return url
    return f"{match.group('scheme')}***@{url[match.end():]}"


def short_name(url: str) -> str:
    """Repository name from a URL: git@host:org/repo.git -> repo."""
    tail = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return re.sub(r"\.git$", "", tail)
