"""Import names -> pip distribution names, for the dependencies Odoo manifests and tracebacks name by import.

``external_dependencies`` lists import names (``ldap``, ``PIL``); a traceback says ``No module named 'x'``.
Most names equal their distribution; the well-known exceptions are listed. Anything else is passed as it is,
and the install dialog shows the exact package before anything runs.
"""

from __future__ import annotations

import re

DISTRIBUTION = {
    "ldap": "python-ldap", "PIL": "Pillow", "dateutil": "python-dateutil", "yaml": "PyYAML", "OpenSSL": "pyOpenSSL",
    "Crypto": "pycryptodome", "serial": "pyserial", "usb": "pyusb", "bs4": "beautifulsoup4", "cv2": "opencv-python",
    "magic": "python-magic", "docx": "python-docx", "jwt": "PyJWT", "sklearn": "scikit-learn", "zk": "pyzk",
    "pkg_resources": "setuptools", "attr": "attrs", "markdown": "Markdown", "OpenGL": "PyOpenGL", "gi": "PyGObject",
    "Image": "Pillow", "mx": "egenix-mx-base", "slugify": "python-slugify", "telegram": "python-telegram-bot",
    "dns": "dnspython", "ldap3": "ldap3", "faker": "Faker", "xlrd": "xlrd", "xlwt": "xlwt",
}
_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_NOT_FOUND = re.compile(r"(?:ModuleNotFoundError|ImportError): No module named '([A-Za-z_][A-Za-z0-9_.]*)'")


def split(dep: str) -> tuple[str, str]:
    """(name, specifier) of a manifest dependency such as ``marshmallow-objects>=2.0.0``."""
    match = _NAME.match(dep or "")
    if not match:
        return "", ""
    return match.group(1), match.group(3).strip()


def package_for(name: str) -> str:
    top = name.split(".")[0]
    return DISTRIBUTION.get(top, top)


def missing_in(text: str) -> str | None:
    """Package named by the last ``No module named`` of a traceback, or None."""
    found = _NOT_FOUND.findall(text or "")
    return package_for(found[-1]) if found else None


def module_in(text: str) -> str | None:
    found = _NOT_FOUND.findall(text or "")
    return found[-1] if found else None
