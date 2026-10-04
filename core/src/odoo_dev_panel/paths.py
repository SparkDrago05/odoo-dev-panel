"""Well-known locations. Each can be overridden by an environment variable for development and tests."""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

DEFAULT_GROUP = "odoo-dev"
INSTALL_PREFIX = Path("/usr/lib/odoo-dev-panel")


def socket_dir() -> Path:
    return Path(os.environ.get("ODP_SOCKET_DIR", "/run/odoo-dev-panel"))


def _home() -> Path:
    # Under sudo the HOME variable may still point to the invoking user; trust the password database.
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def agent_state_dir() -> Path:
    if "ODP_STATE_DIR" in os.environ:
        return Path(os.environ["ODP_STATE_DIR"])
    return _home() / ".local" / "state" / "odoo-dev-panel"


def registry_path() -> Path:
    if "ODP_REGISTRY" in os.environ:
        return Path(os.environ["ODP_REGISTRY"])
    return agent_state_dir() / "registry.json"


def odp_command() -> list[str]:
    """Command prefix that starts this same core as another user.

    The target user (for example odoo19) must be able to read it, so a checkout
    under a private home directory does not work; use the installed copy or ODP_EXE.
    """
    if "ODP_EXE" in os.environ:
        return [os.environ["ODP_EXE"]]
    installed = INSTALL_PREFIX / "bin" / "odp"
    if installed.exists():
        return [str(installed)]
    return [sys.executable, "-m", "odoo_dev_panel"]


def askpass_command() -> str:
    if "ODP_ASKPASS" in os.environ:
        return os.environ["ODP_ASKPASS"]
    return str(INSTALL_PREFIX / "bin" / "odp-askpass")


def uv_command() -> str:
    if "ODP_UV" in os.environ:
        return os.environ["ODP_UV"]
    return str(INSTALL_PREFIX / "bin" / "uv")
