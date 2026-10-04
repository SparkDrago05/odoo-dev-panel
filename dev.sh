#!/usr/bin/env bash
# Run Odoo Dev Panel from the source tree. No .deb, no packaging build.
#
#   ./dev.sh            sync the core, then start the app with hot reload (pnpm tauri dev)
#   ./dev.sh sync       only sync the core to /opt/odoo-dev-panel/dev
#   ./dev.sh test       core unit tests
#   ./dev.sh odp ...    run the CLI from the synced copy, e.g. ./dev.sh odp agent status
#   ./dev.sh shell      open a shell with the dev environment (odp alias, ODP_* variables)
#
# One-time host setup: sudo spike/setup-dev-host.sh, then log out and back in.
# Why a synced copy: agents run as odooNN users, who cannot read a checkout under your home.
# The Python core is re-synced on every start; UI changes reload by themselves.
# Provision is refused for versions that already exist on this machine; test that in containers (spike/provision-test.sh).
set -euo pipefail

REPO=$(cd "$(dirname "$0")" && pwd)
export PATH="$PATH:$HOME/.cargo/bin:$HOME/.local/bin"

# A terminal inside the VS Code snap exports snap library paths. WebKit then loads the snap's libc
# and dies ("symbol lookup error ... libpthread"). Restore the original values and drop the rest.
clean_snap_env() {
    local name orig
    for orig in $(compgen -e | grep '_VSCODE_SNAP_ORIG$' || true); do
        name=${orig%_VSCODE_SNAP_ORIG}
        export "$name=${!orig}"
    done
    unset GTK_PATH GTK_EXE_PREFIX GDK_PIXBUF_MODULE_FILE GDK_PIXBUF_MODULEDIR GIO_MODULE_DIR GSETTINGS_SCHEMA_DIR \
        LOCPATH GTK_IM_MODULE_FILE LD_LIBRARY_PATH
    case "${XDG_DATA_DIRS:-}" in */snap/*) unset XDG_DATA_DIRS ;; esac
}
clean_snap_env

need_setup() {
    echo "dev.sh: $1" >&2
    echo "Run once: sudo spike/setup-dev-host.sh   then log out and back in." >&2
    exit 1
}

check_host() {
    getent group odoo-dev >/dev/null || need_setup "group odoo-dev is missing"
    id -nG | tr ' ' '\n' | grep -qx odoo-dev || need_setup "you are not in group odoo-dev in this session"
    [ -w /opt/odoo-dev-panel/dev ] || need_setup "/opt/odoo-dev-panel/dev is missing or not writable"
    [ -d /run/odoo-dev-panel ] || need_setup "/run/odoo-dev-panel is missing"
}

sync_core() {
    check_host
    "$REPO/spike/dev-sync.sh" >/dev/null
    echo "core synced to /opt/odoo-dev-panel/dev"
}

cmd=${1:-app}
[ $# -gt 0 ] && shift
case "$cmd" in
    app)
        sync_core
        # shellcheck disable=SC1091
        source "$REPO/spike/dev-env.sh"
        if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
            echo "dev.sh: no DISPLAY or WAYLAND_DISPLAY; run it from a terminal on your desktop session" >&2
            exit 1
        fi
        [ -n "${DEV_DEBUG:-}" ] && env | grep -E '^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|GDK_|GTK_|XDG_SESSION_TYPE|XDG_DATA_DIRS|SNAP)' | sort >&2
        # Known WebKitGTK freeze workarounds (GPU compositing and DMABUF rendering on Wayland/Xwayland, Nvidia).
        # The container tests ran with compositing off. Override by exporting the variable before ./dev.sh.
        export WEBKIT_DISABLE_COMPOSITING_MODE="${WEBKIT_DISABLE_COMPOSITING_MODE:-1}"
        export WEBKIT_DISABLE_DMABUF_RENDERER="${WEBKIT_DISABLE_DMABUF_RENDERER:-1}"
        command -v pnpm >/dev/null || { echo "dev.sh: pnpm not found" >&2; exit 1; }
        cd "$REPO/app"
        [ -d node_modules ] || pnpm install
        exec pnpm tauri dev "$@"
        ;;
    sync) sync_core ;;
    test)
        cd "$REPO/core"
        PYTHONPATH=src exec python3.12 -m unittest discover -s tests -t . "$@"
        ;;
    odp)
        sync_core
        # shellcheck disable=SC1091
        source "$REPO/spike/dev-env.sh"
        exec "$ODP_EXE" "$@"
        ;;
    shell)
        sync_core
        # shellcheck disable=SC1091
        source "$REPO/spike/dev-env.sh"
        echo "dev shell: odp, ODP_EXE, ODP_ASKPASS, ODP_UV set. Exit to leave."
        exec "${SHELL:-bash}" -i
        ;;
    *) sed -n 2,12p "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
