#!/usr/bin/env bash
# Build packaging/build/odoo-dev-panel: everything the .deb puts in /usr/lib/odoo-dev-panel.
#   python/   standalone CPython 3.12 (python-build-standalone, fetched by uv) with the core installed
#   bin/      odp and odp-askpass launchers, uv
#   spike/    fake_odoo.py
# Needs uv on the build machine. The target machine needs neither Python nor Rust.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
OUT=$HERE/build/odoo-dev-panel
PREFIX=/usr/lib/odoo-dev-panel
PY_VERSION=${PY_VERSION:-3.12}

export PATH="$PATH:$HOME/.local/bin:$HOME/.cargo/bin"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT/bin" "$OUT/spike" "$HERE/build/uv-python"

UV_PYTHON_INSTALL_DIR="$HERE/build/uv-python" uv python install "$PY_VERSION"
SRC_PY=$(UV_PYTHON_INSTALL_DIR="$HERE/build/uv-python" uv python find --managed-python "$PY_VERSION")
SRC_ROOT=$(dirname "$(dirname "$(readlink -f "$SRC_PY")")")
cp -a "$SRC_ROOT" "$OUT/python"
# Do not ship the uv-managed marker: this copy is owned by the package, not by uv.
rm -f "$OUT/python/lib/python$PY_VERSION/EXTERNALLY-MANAGED"
# The Tauri deb bundler copies symlinks as full files, so drop symlinks and build-only parts.
PYLIB="$OUT/python/lib/python$PY_VERSION"
rm -rf "$OUT/python/include" "$OUT/python/share" "$OUT/python/lib/pkgconfig" \
    "$OUT/python/lib/libpython$PY_VERSION.so" "$OUT/python/lib"/tcl* "$OUT/python/lib"/tk* \
    "$PYLIB"/config-* "$PYLIB/test" "$PYLIB/idlelib" "$PYLIB/tkinter" "$PYLIB/turtledemo" \
    "$PYLIB/ensurepip" "$PYLIB/lib2to3"
find "$OUT/python/bin" -mindepth 1 ! -name "python$PY_VERSION" -delete

SITE=$("$OUT/python/bin/python$PY_VERSION" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
cp -r "$REPO/core/src/odoo_dev_panel" "$SITE/"
find "$SITE/odoo_dev_panel" -name __pycache__ -prune -exec rm -rf {} +
"$OUT/python/bin/python$PY_VERSION" -m compileall -q -d "$PREFIX${SITE#$OUT}/odoo_dev_panel" "$SITE/odoo_dev_panel"

for name in odp odp-askpass; do
    sub=""
    [ "$name" = odp-askpass ] && sub="askpass "
    cat > "$OUT/bin/$name" <<SH
#!/bin/sh

exec $PREFIX/python/bin/python$PY_VERSION -I -B -m odoo_dev_panel $sub"\$@"
SH
    chmod 0755 "$OUT/bin/$name"
done

install -m 0755 "$(command -v uv)" "$OUT/bin/uv"
install -m 0755 "$REPO/spike/fake_odoo.py" "$OUT/spike/fake_odoo.py"

du -sh "$OUT"
