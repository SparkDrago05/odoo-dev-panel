"""``odp-askpass``: the SUDO_ASKPASS helper.

sudo runs it with the prompt as its only argument and reads the password from its
stdout. The helper asks the sidecar that started sudo (socket in ODP_ASKPASS_SOCK),
the sidecar asks the UI, and the password goes back on stdout. It is never stored.
"""

from __future__ import annotations

import asyncio
import os
import sys

from . import rpc


async def _ask(sock_path: str, prompt: str) -> str | None:
    conn = await rpc.open_unix(sock_path, name="askpass")
    try:
        result = await conn.request("askpass", {"prompt": prompt}, timeout=300)
    finally:
        await conn.close()
    if not result or result.get("password") is None:
        return None
    return result["password"]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    prompt = argv[0] if argv else "Password:"
    sock_path = os.environ.get("ODP_ASKPASS_SOCK")
    if not sock_path:
        print("odp-askpass: ODP_ASKPASS_SOCK is not set", file=sys.stderr)
        return 1
    try:
        password = asyncio.run(_ask(sock_path, prompt))
    except Exception as exc:  # noqa: BLE001
        print(f"odp-askpass: {exc}", file=sys.stderr)
        return 1
    if password is None:
        return 1  # cancelled: sudo treats a failing helper as no password
    sys.stdout.write(password + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
