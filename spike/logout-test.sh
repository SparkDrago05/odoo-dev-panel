#!/usr/bin/env bash
# F9: does a desktop logout kill the agent or its Odoo process?
#
#   spike/logout-test.sh [path/to.deb] [release]        default release: 24.04
#
# The desktop starts the app inside a systemd *user* scope (app-*.scope under user@UID.service).
# Logging out stops user@UID.service, which stops every unit inside it. This test reproduces that:
# it starts an agent and a session from a user scope in a fresh container, stops user@UID.service,
# and checks whether the Odoo process and the agent are still alive.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB=${1:-"$REPO/app/src-tauri/target/release/bundle/deb/Odoo Dev Panel_0.0.1_amd64.deb"}
REL=${2:-24.04}
C="odp-logout-${REL//./}"
KEEP=${KEEP:-0}

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }

lxc delete -f "$C" >/dev/null 2>&1 || true
say "launch fresh ubuntu:$REL"
lxc launch "ubuntu:$REL" "$C" >/dev/null
in_c cloud-init status --wait >/dev/null || true
lxc file push "$DEB" "$C/root/odp.deb"
in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb >/dev/null'
in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
    echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev
    useradd --system --create-home --home-dir /opt/odoo99 --shell /bin/bash -G odoo-dev odoo99
    loginctl enable-linger dev'
sleep 3
in_c systemctl is-active user@1001.service

say "start agent and session from a user scope, like the desktop starts the app"
lxc exec "$C" -- bash -s <<'EOS'
set -e
export XDG_RUNTIME_DIR=/run/user/1001
runuser -u dev -- env XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus \
    systemd-run --user --scope --quiet --unit=fake-app --collect -- bash -c '
        PY=/usr/lib/odoo-dev-panel/python/bin/python3.12
        odp agent start -u odoo99
        odp --json run -u odoo99 -- $PY /usr/lib/odoo-dev-panel/spike/fake_odoo.py --workers 2 > /tmp/session.json
        sleep 1
        cat /proc/self/cgroup
    '
echo "--- agent and odoo cgroups:"
for p in $(pgrep -u odoo99); do printf '  %s %s -> %s\n' "$p" "$(tr '\0' ' ' </proc/$p/cmdline | cut -c1-50)" "$(cut -d: -f3 /proc/$p/cgroup)"; done
EOS

say "logout: stop user@1001.service"
in_c systemctl stop user@1001.service
sleep 3

say "result"
if in_c pgrep -u odoo99 -f fake_odoo >/dev/null; then echo "  Odoo process SURVIVED logout"; else echo "  Odoo process was KILLED by logout"; fi
if in_c pgrep -u odoo99 -f 'agent serve' >/dev/null; then echo "  agent SURVIVED logout"; else echo "  agent was KILLED by logout"; fi

[ "$KEEP" = 1 ] || lxc delete -f "$C" >/dev/null
