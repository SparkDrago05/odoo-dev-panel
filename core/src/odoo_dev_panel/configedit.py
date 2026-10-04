"""Raw editor for Odoo configs: read with secrets masked, validate, save in place with a backup, copy.

Secrets never leave this module unmasked unless the caller asks for ``reveal``; they never go to logs.
"""

from __future__ import annotations

import configparser
import hashlib
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .discover.configs import split_addons_path

MASK = "********"
_KEY = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*[=:]\s*)(.*?)(\s*)$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# Options of odoo-bin 15-20 (odoo/tools/config.py), plus [options] keys Odoo writes itself.
KNOWN_OPTIONS = frozenset("""
addons_path upgrade_path pre_upgrade_scripts server_wide_modules data_dir http_interface http_port http_enable
xmlrpc xmlrpc_interface xmlrpc_port longpolling_port gevent_port proxy_mode x_sendfile dbfilter test_file
test_enable test_tags screencasts screenshots logfile syslog log_handler log_web log_sql log_db log_db_level
log_level email_from from_filter smtp_server smtp_port smtp_ssl smtp_user smtp_password smtp_ssl_certificate_filename
smtp_ssl_private_key_filename db_user db_password db_host db_port db_name db_sslmode db_maxconn db_maxconn_gevent
db_template db_replica_host db_replica_port pg_path load_language overwrite_existing_translations
translate_modules list_db admin_passwd without_demo demo csv_internal_sep reportgz unaccent geoip_database
geoip_city_db geoip_country_db osv_memory_count_limit transient_age_limit max_cron_threads limit_time_worker_cron
workers limit_memory_hard limit_memory_soft limit_request limit_time_cpu limit_time_real limit_time_real_cron
stop_after_init dev_mode shell_interface shell_file import_partial root_path publisher_warranty_url
running_tests websocket_keep_alive_timeout websocket_rate_limit_burst websocket_rate_limit_delay
bin_path db_app_name db_system default_productivity_apps gevent_workers import_file_maxbytes import_file_timeout
import_url_regex limit_memory_hard_gevent limit_memory_soft_gevent log_config pidfile proxy_access_token
skip_auto_install unsafe_policy with_demo
""".split())
_PORTS = ("http_port", "xmlrpc_port", "longpolling_port", "gevent_port", "db_port", "smtp_port")
_BOOLEANS = ("proxy_mode", "list_db", "unaccent", "x_sendfile", "log_db", "test_enable")
_CHOICES = {
    "log_level": ("info", "debug_rpc", "warn", "test", "critical", "runbot", "debug_sql", "error", "debug",
                  "debug_rpc_answer", "notset"),
    "db_sslmode": ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"),
}
_INTEGERS = ("workers","max_cron_threads", "limit_memory_hard", "limit_memory_soft", "limit_request",
             "limit_time_cpu", "limit_time_real", "limit_time_real_cron", "db_maxconn", "osv_memory_count_limit")


class ConfigError(Exception):
    pass


def is_secret(key: str) -> bool:
    return "pass" in key.lower()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def mask(text: str) -> str:
    """Replace the value of every secret key that has one (``False`` is no value) with MASK."""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _KEY.match(body)
        if m and is_secret(m.group(2)) and m.group(4) not in ("", "False"):
            line = m.group(1) + m.group(2) + m.group(3) + MASK + line[len(body):]
        out.append(line)
    return "".join(out)


def unmask(edited: str, current: str) -> str:
    """Put the current values back where a secret line still holds MASK."""
    values = {}
    for line in current.splitlines():
        m = _KEY.match(line)
        if m and is_secret(m.group(2)):
            values[m.group(2)] = m.group(4)
    out = []
    for line in edited.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _KEY.match(body)
        if m and is_secret(m.group(2)) and m.group(4) == MASK:
            if m.group(2) not in values:
                raise ConfigError(f"{m.group(2)} still shows {MASK} but the file has no value for it: type the value")
            line = m.group(1) + m.group(2) + m.group(3) + values[m.group(2)] + line[len(body):]
        out.append(line)
    return "".join(out)


@dataclass
class Issue:
    level: str  # error | warning
    key: str | None
    text: str


@dataclass
class Access:
    owner: str
    group: str
    mode: str
    writable: bool  # by the caller
    others_read: bool


def access(path: str) -> Access:
    import grp
    import pwd

    st = os.stat(path)

    def name(lookup, ident: int) -> str:
        try:
            return lookup(ident)[0]
        except KeyError:
            return str(ident)

    return Access(name(pwd.getpwuid, st.st_uid), name(grp.getgrgid, st.st_gid), f"{stat.S_IMODE(st.st_mode):04o}",
                  os.access(path, os.W_OK), bool(st.st_mode & stat.S_IROTH))


def parse(text: str) -> dict[str, str]:
    parser = configparser.RawConfigParser(strict=False, interpolation=None, inline_comment_prefixes=(";",))
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ConfigError(f"not a valid config: {str(exc).splitlines()[0]}") from exc
    if not parser.has_section("options"):
        raise ConfigError("no [options] section: Odoo ignores this file")
    return dict(parser.items("options"))


def validate(text: str, snapshot: dict | None = None, path: str | None = None) -> list[Issue]:
    """Errors block saving; warnings do not. ``snapshot`` (discovery) adds the cross-config checks."""
    try:
        opts = parse(text)
    except ConfigError as exc:
        return [Issue("error", None, str(exc))]
    issues: list[Issue] = []
    for key, value in opts.items():
        if key not in KNOWN_OPTIONS:
            issues.append(Issue("warning", key, f"{key} is not an Odoo option (typo?); Odoo ignores it"))
        if value in ("", "False"):
            continue
        if key in _PORTS and not (value.isdigit() and 1 <= int(value) <= 65535):
            issues.append(Issue("error", key, f"{key} = {value} is not a port (1-65535)"))
        elif key in _INTEGERS and not re.fullmatch(r"-?\d+", value):
            issues.append(Issue("error", key, f"{key} = {value} is not a whole number"))
        elif key in _BOOLEANS and value not in ("True", "true", "false"):
            issues.append(Issue("warning", key, f"{key} = {value}: Odoo expects True or False"))
        elif key in _CHOICES and value not in _CHOICES[key]:
            issues.append(Issue("warning", key, f"{key} = {value} is not one of: {', '.join(_CHOICES[key])}"))
    for entry in split_addons_path(opts.get("addons_path")):
        if not os.path.isdir(entry):
            issues.append(Issue("warning", "addons_path", f"{entry} does not exist: Odoo refuses to start"))
    data_dir = opts.get("data_dir")
    if data_dir and data_dir != "False" and not os.path.isdir(os.path.expanduser(data_dir)):
        issues.append(Issue("warning", "data_dir", f"{data_dir} does not exist (or you cannot see it)"))
    if snapshot is not None:
        issues += _cross_checks(opts, snapshot, path)
    return issues


def _cross_checks(opts: dict[str, str], snapshot: dict, path: str | None) -> list[Issue]:
    from .discover.configs import link_installation
    from .discover.model import Installation

    issues = []
    known = set(Installation.__dataclass_fields__)
    installs = [Installation(**{k: v for k, v in i.items() if k in known}) for i in snapshot["installations"]]
    inst, problems = link_installation(split_addons_path(opts.get("addons_path")), installs)
    issues += [Issue("warning", "addons_path", p) for p in problems]
    me = next((i for i in snapshot["instances"] if i["path"] == path), None)
    if me and me.get("installation") and inst and inst.root != me["installation"]:
        issues.append(Issue("warning", "addons_path", f"addons_path now points to {inst.root}, not {me['installation']}"))
    port = opts.get("http_port") or opts.get("xmlrpc_port")
    if port and port.isdigit():
        for other in snapshot["instances"]:
            o = other.get("options", {})
            if other["path"] != path and (o.get("http_port") or o.get("xmlrpc_port")) == port:
                issues.append(Issue("warning", "http_port", f"port {port} is also used by {other['path']}"))
    return issues


def set_options(text: str, changes: dict[str, str | None]) -> str:
    """Change keys of ``[options]`` in place; comments, order and other lines stay. ``None`` removes the key (Odoo
    then uses its default). A new key goes after the last line of the section. Masked secrets pass through."""
    for key, value in changes.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f"bad option name {key!r}")
        if value is not None and ("\n" in value or "\r" in value):
            raise ConfigError(f"{key}: the value must be one line")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    section = None
    end = None  # index in out after the last non-blank line of [options]
    done: set[str] = set()
    skip_indent = None  # indent of a removed or replaced key whose continuation lines are dropped too
    for line in lines:
        body = line.rstrip("\r\n")
        stripped = body.strip()
        indent = len(body) - len(body.lstrip())
        if skip_indent is not None and stripped and indent > skip_indent and not stripped.startswith((";", "#")):
            continue
        skip_indent = None
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            out.append(line)
            if section == "options":
                end = len(out)
            continue
        m = _KEY.match(body) if section == "options" else None
        if m and m.group(2) in changes:
            key = m.group(2)
            skip_indent = indent
            done.add(key)
            if changes[key] is None:
                continue
            line = m.group(1) + key + m.group(3) + changes[key] + line[len(body):]
        out.append(line)
        if section == "options" and stripped:
            end = len(out)
    if end is None:
        raise ConfigError("no [options] section: Odoo ignores this file")
    new = [f"{k} = {v}\n" for k, v in changes.items() if v is not None and k not in done]
    if new and end > 0 and not out[end - 1].endswith("\n"):
        out[end - 1] += "\n"
    out[end:end] = new
    return "".join(out)


def addons_entries(value: str | None, snapshot: dict, path: str | None) -> list[dict]:
    """Each addons_path entry with its state: ``missing`` (H3), ``other`` (another installation than the config's,
    H5) or ``ok``; ``installation``/``version`` name the installation it lies in, if any."""
    from .discover.configs import _under

    entries = split_addons_path(value)
    installs = snapshot["installations"]
    me = next((i for i in snapshot["instances"] if i["path"] == path), None)
    home = me.get("installation") if me else None

    def owner(entry: str) -> dict | None:
        return next((i for i in installs if _under(entry, i["root"]) or _under(entry, i["source"])), None)

    if home is None:  # orphan or new config: the installation most entries belong to
        votes: dict[str, int] = {}
        for e in entries:
            if (o := owner(e)) is not None:
                votes[o["root"]] = votes.get(o["root"], 0) + 1
        home = max(votes, key=lambda r: (votes[r], r)) if votes else None
    out = []
    for e in entries:
        o = owner(e)
        state = "missing" if not os.path.isdir(e) else "other" if o and home and o["root"] != home else "ok"
        out.append({"path": e, "state": state, "installation": o["root"] if o else None,
                    "version": o.get("version") if o else None})
    return out


def form(text: str, snapshot: dict, path: str | None, changes: dict[str, str | None] | None = None) -> dict:
    """What the form view shows: the text after ``changes``, its options (``None`` when it does not parse),
    the addons_path entries and the issues."""
    if changes:
        text = set_options(text, changes)
    try:
        opts: dict[str, str] | None = parse(text)
    except ConfigError:
        opts = None
    return {"text": text, "options": opts,
            "addons": addons_entries((opts or {}).get("addons_path"), snapshot, path),
            "issues": [asdict(i) for i in validate(text, snapshot, path)]}


@dataclass
class Opened:
    path: str
    text: str  # masked unless revealed
    sha: str  # of the file as read, so save can detect a change in between
    access: Access
    issues: list[Issue] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def open_config(path: str, reveal: bool = False, snapshot: dict | None = None) -> Opened:
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    acc = access(path)
    issues = validate(text, snapshot, path)
    if acc.others_read and any(is_secret(k) and v not in ("", "False") for k, v in _safe_opts(text).items()):
        issues.append(Issue("warning", None, f"every local user can read the passwords in this file (mode {acc.mode})"))
    return Opened(path, text if reveal else mask(text), sha(text), acc, issues)


def _safe_opts(text: str) -> dict[str, str]:
    try:
        return parse(text)
    except ConfigError:
        return {}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup(path: str, text: str) -> str:
    """``<path>.bak-<time>`` next to the config; in a shared folder the developer cannot write (``/etc/odoo``),
    under the developer's state folder instead. Mode 0600: it holds the same passwords."""
    from . import paths

    name = f"{os.path.basename(path)}.bak-{_stamp()}"
    if os.access(os.path.dirname(path), os.W_OK):
        backup = os.path.join(os.path.dirname(path), name)
    else:
        folder = paths.agent_state_dir() / "config-backups" / os.path.dirname(path).strip("/").replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = str(folder / name)
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise ConfigError(f"cannot write the backup {backup}: {exc.strerror}; nothing saved") from exc
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return backup


def save(path: str, edited: str, base_sha: str, snapshot: dict | None = None) -> dict:
    """Write ``edited`` (masked or not) over ``path``. Refuses on validation errors or a changed file."""
    try:
        current = Path(path).read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    if sha(current) != base_sha:
        raise ConfigError(f"{path} changed since you opened it: reopen it and apply your change again")
    text = unmask(edited, current)
    if not text.endswith("\n"):
        text += "\n"
    errors = [i for i in validate(text, snapshot, path) if i.level == "error"]
    if errors:
        raise ConfigError("; ".join(i.text for i in errors))
    if text == current:
        return {"path": path, "changed": False, "backup": None}
    if not os.access(path, os.W_OK):
        raise ConfigError(f"you cannot write {path}: use Fix permissions first")
    backup = _backup(path, current)
    # In place: owner, group, mode and inode stay what they were (the run-as user keeps its read access).
    with open(path, "r+") as fh:
        fh.write(text)
        fh.truncate()
    if Path(path).read_text() != text:
        raise ConfigError(f"{path} reads back different from what was written; the old file is in {backup}")
    return {"path": path, "changed": True, "backup": backup}


def copy(path: str, name: str, home_layout: bool, folder: str | None = None) -> dict:
    """New config ``<name>.conf`` next to ``path`` (or in ``folder``), same content. The folder's setgid bit gives
    it the group."""
    if name.endswith(".conf"):
        name = name[:-5]
    if not _NAME.match(name):
        raise ConfigError(f"bad config name {name!r}: letters, digits, '.', '_', '-'")
    folder = folder or os.path.dirname(path)
    target = os.path.join(folder, f"{name}.conf")
    if os.path.lexists(target):
        raise ConfigError(f"{target} exists already")
    if not os.access(folder, os.W_OK):
        raise ConfigError(f"you cannot create files in {folder}: use Fix permissions first")
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    mode = 0o600 if home_layout else 0o640
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(target, mode)  # the umask may have removed bits
    warning = None
    if os.stat(target).st_gid != os.stat(path).st_gid:
        # No setgid folder: the file got the developer's group, so the run-as user cannot read it.
        warning = f"{target} did not get the group of {os.path.basename(path)}; use Fix permissions so Odoo can read it"
    return {"path": target, "warning": warning}


def copy_folder(path: str, installation_root: str | None) -> str | None:
    """Where a copy goes: next to the original, unless that is a home folder (``~/.odoorc``), where discovery
    does not look for configs; then the installation root."""
    import pwd

    folder = os.path.dirname(path)
    homes = {os.path.normpath(p.pw_dir) for p in pwd.getpwall()}
    return installation_root if installation_root and os.path.normpath(folder) in homes else None
