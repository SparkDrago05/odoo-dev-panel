#!/usr/bin/env bash
# Copy the core and the spike files to /opt/odoo-dev-panel/dev, where version users can read them.
# A checkout under /home/<you> is not readable by odooNN users, so agents cannot run from it.
# Then:  source spike/dev-env.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEST=/opt/odoo-dev-panel/dev
PYTHON=${ODP_PYTHON:-/usr/bin/python3.12}

umask 002
mkdir -p "$DEST/bin" "$DEST/src" "$DEST/spike"
# Replace the sources but keep directories: __pycache__ written by the version users (odooNN) cannot be
# deleted by you. A stale .pyc is harmless (Python checks the source mtime); the wrappers below stop new ones.
find "$DEST/src" "$DEST/spike" -name '*.py' -delete
(cd "$REPO/core/src" && find . -name '*.py' -exec cp --parents -f {} "$DEST/src" \;)
(cd "$REPO/spike" && find . \( -name '*.py' -o -name '*.sh' \) -exec cp --parents -f {} "$DEST/spike" \;)

cat > "$DEST/bin/odp" <<SH
#!/bin/sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$DEST/src exec $PYTHON -m odoo_dev_panel "\$@"
SH
cat > "$DEST/bin/odp-askpass" <<SH
#!/bin/sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$DEST/src exec $PYTHON -m odoo_dev_panel askpass "\$@"
SH
# uv for provision: the agent of odooNN must be able to run it (a copy under /home is not readable).
UV=$(command -v uv || ls "$HOME/.local/bin/uv" 2>/dev/null || true)
if [ -n "$UV" ]; then install -m 0775 "$UV" "$DEST/bin/uv"; else echo "warning: uv not found; provision needs it (ODP_UV)" >&2; fi
chmod 0775 "$DEST/bin/odp" "$DEST/bin/odp-askpass"
echo "synced to $DEST"
