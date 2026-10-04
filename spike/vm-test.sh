#!/usr/bin/env bash
# Spike 0 clean-machine test in fresh LXD containers (Ubuntu 24.04 = classic sudo, 26.04 = sudo-rs).
#
#   spike/vm-test.sh [path/to.deb] [release ...]      default releases: 24.04 26.04
#
# Per release: install the .deb on a fresh system, check the target needs no Python/Rust,
# run the headless acceptance as a sudo user, smoke-test the GUI binary under Xvfb
# (app start, kill -9 app, session survives, restart app, rediscovery), then purge and look for leftovers.
# Needs: snap lxd, `lxd init --auto`, and your user in the lxd group.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB=${1:-"$REPO/app/src-tauri/target/release/bundle/deb/Odoo Dev Panel_0.0.1_amd64.deb"}
shift || true
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}   # KEEP=1 leaves the containers for inspection

[ -f "$DEB" ] || { echo "no .deb at $DEB" >&2; exit 1; }

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }
# Run a script given on stdin as user dev. sudo -i re-quotes arguments (newlines become line
# continuations, $ stays live), so scripts must not be passed as arguments.
as_dev() { lxc exec "$C" -- sudo -iu dev bash -ls "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-test-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true

    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    in_c sh -c 'sudo --version | head -1'

    say "target has no Rust; system Python is not used by the app"
    in_c sh -c '! command -v cargo && ! command -v rustc' >/dev/null && echo "  no cargo/rustc: ok"

    say "install .deb"
    lxc file push "$DEB" "$C/root/odp.deb"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb xvfb dbus-x11 >/dev/null'
    in_c sh -c 'dpkg -l odoo-dev-panel | tail -1; ls -ld /run/odoo-dev-panel /opt/odoo-dev-panel/python; getent group odoo-dev'
    in_c /usr/lib/odoo-dev-panel/bin/odp --version

    say "users: dev (sudo, test-only NOPASSWD) and version user odoo99"
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev
        useradd --system --create-home --home-dir /opt/odoo99 --shell /bin/bash -G odoo-dev odoo99'
    lxc file push "$REPO/spike/acceptance.sh" "$C/home/dev/acceptance.sh" --mode 0755

    say "headless acceptance"
    lxc exec "$C" -- sudo -iu dev ./acceptance.sh odoo99

    say "shared managed-Python dir works for odoo99"
    as_dev <<'EOS'
set -e
export UV_PYTHON_INSTALL_DIR=/opt/odoo-dev-panel/python
/usr/lib/odoo-dev-panel/bin/uv python install --no-bin 3.12
py=$(/usr/lib/odoo-dev-panel/bin/uv python find --managed-python 3.12)
echo "  interpreter: $py"
sudo -u odoo99 "$py" -c 'import sys; print("  odoo99 runs", sys.version.split()[0])'
EOS

    say "GUI smoke under Xvfb: start app, kill -9 app, session survives, restart app"
    as_dev <<'EOS'
set -e
export WEBKIT_DISABLE_COMPOSITING_MODE=1
PY=/usr/lib/odoo-dev-panel/python/bin/python3.12
jfield() { "$PY" -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
start_app() { dbus-run-session -- xvfb-run -a /usr/bin/odoo-dev-panel >"$1" 2>&1 </dev/null & }

start_app /tmp/app1.log; sleep 10
pgrep -f "odoo_dev_panel sidecar" >/dev/null && echo "  app started its sidecar: ok" || { echo "  FAIL app did not start"; cat /tmp/app1.log; exit 1; }

odp --json agent status -u odoo99 >/dev/null || true
out=$(odp --json run -u odoo99 -- "$PY" /usr/lib/odoo-dev-panel/spike/fake_odoo.py --workers 2)
id=$(echo "$out" | jfield id)
pid=$(echo "$out" | jfield pid)
echo "  started session $id pid $pid"

pkill -9 -f /usr/bin/odoo-dev-panel; sleep 3
if pgrep -f "odoo_dev_panel sidecar" >/dev/null; then echo "  FAIL sidecar still running after app kill -9"; exit 1; fi
echo "  sidecar exited with the app: ok"
ps -o pid= -p "$pid" >/dev/null && echo "  session alive after app kill -9: ok" || { echo "  FAIL session died with the app"; exit 1; }

start_app /tmp/app2.log; sleep 10
pgrep -f "odoo_dev_panel sidecar" >/dev/null && echo "  app restarted: ok" || { echo "  FAIL app did not restart"; cat /tmp/app2.log; exit 1; }
odp --json ps -u odoo99 | grep -q "\"$id\"" && echo "  session rediscovered: ok" || { echo "  FAIL session not rediscovered"; exit 1; }
odp stop -u odoo99 "$id"
pkill -f /usr/bin/odoo-dev-panel || true
EOS

    say "purge and look for leftovers"
    in_c sh -c 'DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq odoo-dev-panel >/dev/null
        left=$(ls -d /usr/lib/odoo-dev-panel /usr/bin/odp /usr/bin/odoo-dev-panel /opt/odoo-dev-panel /run/odoo-dev-panel /usr/lib/tmpfiles.d/odoo-dev-panel.conf 2>/dev/null || true)
        if [ -n "$left" ]; then echo "  LEFTOVERS:"; echo "$left"; exit 1; else echo "  nothing left: ok"; fi'

    [ "$KEEP" = 1 ] || lxc delete -f "$C" >/dev/null
    say "PASSED"
done
