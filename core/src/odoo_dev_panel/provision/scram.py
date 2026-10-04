"""PostgreSQL SCRAM-SHA-256 password verifiers (RFC 5802 / 7677).

CREATE ROLE ... PASSWORD accepts a ready-made verifier, so the plaintext password
never has to appear in a command line or in the root script.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

ITERATIONS = 4096  # PostgreSQL default for scram_iterations


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def salted_password(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


def keys(password: str, salt: bytes, iterations: int) -> tuple[bytes, bytes]:
    """Return (StoredKey, ServerKey)."""
    salted = salted_password(password, salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return hashlib.sha256(client_key).digest(), server_key


def verifier(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16) if salt is None else salt
    stored_key, server_key = keys(password, salt, iterations)
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"
