"""What to provision, and the defaults that depend on the Odoo version."""

from __future__ import annotations

import os
import pwd
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

SUPPORTED_VERSIONS = (15, 16, 17, 18, 19)

# Python per Odoo version: inside the range each Odoo release is tested with.
PYTHON_BY_VERSION = {15: "3.10", 16: "3.10", 17: "3.12", 18: "3.12", 19: "3.12"}

DEFAULT_ODOO_GIT = "https://github.com/odoo/odoo.git"
GROUP = "odoo-dev"
MIN_FREE_BYTES = 4 * 1024**3

# Build-time packages for Odoo's Python dependencies (psycopg2, python-ldap, lxml, Pillow, ...).
# Names that exist on Ubuntu 24.04 and 26.04.
BUILD_PACKAGES = (
    "build-essential git postgresql-client "
    "libpq-dev libldap2-dev libsasl2-dev libxml2-dev libxslt1-dev libffi-dev libssl-dev "
    "libjpeg-dev zlib1g-dev libfreetype-dev liblcms2-dev libwebp-dev libtiff-dev "
    "libopenjp2-7-dev libharfbuzz-dev libfribidi-dev libxcb1-dev "
    "fonts-dejavu-core fonts-freefont-ttf"
).split()

# C compiler flags for building old Python packages from source (Odoo 15/16 pin reportlab 3.5.x, python-ldap, ...).
# GCC 14+ turned several warnings into errors and GCC 15 defaults to C23 (`bool` became a keyword).
# Verified: reportlab 3.5.59 fails to build on Ubuntu 26.04 (GCC 15.2) without these and builds with them.
BUILD_CFLAGS = ("-std=gnu17 -Wno-error=incompatible-pointer-types "
                "-Wno-error=implicit-function-declaration -Wno-error=int-conversion")

# Replacement pins per Python version. Odoo 15/16 pin gevent 21.8.0 for Python 3.10; that release has no wheel
# for 3.10 and its sdist needs a pre-release Cython that current build tools do not resolve. gevent 21.12.0 is
# the first release with 3.10 wheels (same API, drop-in). Applied with `uv pip install --override`.
PIP_OVERRIDES = {"3.10": ["gevent==21.12.0"]}

# Installed next to Odoo's requirements. Odoo 15-17 import pkg_resources, which setuptools 82+ removed, and a uv venv
# has no setuptools of its own. Found by running Odoo 16 on Ubuntu 26.04: `ModuleNotFoundError: pkg_resources`.
PIP_EXTRA_PACKAGES = ("setuptools<81",)

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
_CONF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
_URL = re.compile(r"^(https?://|ssh://|git@)[^\s'\"\\]+$")


class SpecError(ValueError):
    pass


@dataclass
class CustomRepo:
    url: str
    name: str            # directory under custom/
    branch: str | None = None

    @classmethod
    def parse(cls, text: str) -> "CustomRepo":
        """Parse ``[NAME=]URL[#BRANCH]``. NAME defaults to the repository name."""
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)=(.+)$", text)
        name, rest = (match.group(1), match.group(2)) if match else (None, text)
        url, _, branch = rest.partition("#")
        if name is None:
            name = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
        return cls(url=url, name=name, branch=branch or None)


@dataclass
class ProvisionSpec:
    version: int
    dev_user: str = field(default_factory=lambda: pwd.getpwuid(os.getuid()).pw_name)
    run_as: str = ""                  # default: odooNN
    root: str = ""                    # default: /opt/odooNN
    python: str = ""                  # default: PYTHON_BY_VERSION
    odoo_git: str = DEFAULT_ODOO_GIT
    odoo_branch: str = ""             # default: NN.0
    enterprise_git: str | None = None
    enterprise_branch: str | None = None
    enterprise_archive: str | None = None
    custom: list[CustomRepo] = field(default_factory=list)
    config_name: str = "default"
    pg_host: str = "localhost"
    pg_port: int = 5432
    http_port: int | None = None
    config_dir: str = ""              # default: /etc/odoo/odooNN
    python_dir: str = "/opt/odoo-dev-panel/python"

    def __post_init__(self) -> None:
        if self.version not in SUPPORTED_VERSIONS:
            raise SpecError(f"unsupported Odoo version {self.version}; supported: {', '.join(map(str, SUPPORTED_VERSIONS))}")
        self.run_as = self.run_as or f"odoo{self.version}"
        self.root = self.root or f"/opt/{self.run_as}"
        self.python = self.python or PYTHON_BY_VERSION[self.version]
        self.odoo_branch = self.odoo_branch or f"{self.version}.0"
        self.config_dir = self.config_dir or f"/etc/odoo/{self.run_as}"
        self._validate()

    def _validate(self) -> None:
        if not _NAME.match(self.run_as):
            raise SpecError(f"bad user name: {self.run_as!r}")
        if not _NAME.match(self.dev_user):
            raise SpecError(f"bad dev user name: {self.dev_user!r}")
        if self.run_as == self.dev_user:
            raise SpecError("run-as user and dev user must differ")
        for label, path in (("root", self.root), ("config dir", self.config_dir), ("python dir", self.python_dir)):
            if (not path.startswith("/") or "//" in path or path.count("/") < 2 or PurePosixPath(path).as_posix() != path
                    or ".." in path.split("/")
                    or not re.match(r"^[A-Za-z0-9_./-]+$", path)):
                raise SpecError(f"{label} must be a clean absolute path (letters, digits, _ . / -): {path!r}")
        if not re.match(r"^3\.\d{1,2}$", self.python):
            raise SpecError(f"bad Python version: {self.python!r}")
        if not _CONF_NAME.match(self.config_name):
            raise SpecError(f"bad config name: {self.config_name!r}")
        for url in filter(None, [self.odoo_git, self.enterprise_git, *[c.url for c in self.custom]]):
            if not _URL.match(url):
                raise SpecError(f"bad git URL: {url!r}")
        for branch in filter(None, [self.odoo_branch, self.enterprise_branch, *[c.branch for c in self.custom]]):
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$", branch):
                raise SpecError(f"bad branch name: {branch!r}")
        if self.enterprise_git and self.enterprise_archive:
            raise SpecError("use either an enterprise git URL or an enterprise archive, not both")
        names = [c.name for c in self.custom]
        if len(set(names)) != len(names) or not all(re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", n) for n in names):
            raise SpecError(f"custom repo names must be unique and clean: {names}")
        if not 1 <= self.pg_port <= 65535 or (self.http_port is not None and not 1024 <= self.http_port <= 65535):
            raise SpecError("bad port")
        if not re.match(r"^[A-Za-z0-9_.-]+$", self.pg_host):
            raise SpecError(f"bad PostgreSQL host: {self.pg_host!r}")

    @property
    def has_enterprise(self) -> bool:
        return bool(self.enterprise_git or self.enterprise_archive)

    @property
    def conf_path(self) -> str:
        return f"{self.config_dir}/{self.config_name}.conf"

    @property
    def receipt_path(self) -> str:
        return f"{self.root}/.odp-provision.json"


_SPEC_FIELDS = {
    "dev_user", "run_as", "root", "python", "odoo_git", "odoo_branch", "enterprise_git", "enterprise_branch",
    "enterprise_archive", "config_name", "pg_host", "pg_port", "http_port",
}


def spec_from_dict(data: dict) -> ProvisionSpec:
    """Build a spec from untrusted input (RPC). Unknown keys are an error; empty values mean default."""
    unknown = set(data) - _SPEC_FIELDS - {"version", "custom"}
    if unknown:
        raise SpecError(f"unknown field(s): {', '.join(sorted(unknown))}")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise SpecError("version must be an integer")
    custom = data.get("custom") or []
    if not isinstance(custom, list) or not all(isinstance(c, str) for c in custom):
        raise SpecError("custom must be a list of [NAME=]URL[#BRANCH] strings")
    values = {k: v for k, v in data.items() if k in _SPEC_FIELDS and v not in (None, "")}
    for key, value in values.items():
        expected = int if key in ("pg_port", "http_port") else str
        if not isinstance(value, expected) or isinstance(value, bool):
            raise SpecError(f"{key} has the wrong type")
    return ProvisionSpec(version=version, custom=[CustomRepo.parse(c) for c in custom], **values)
