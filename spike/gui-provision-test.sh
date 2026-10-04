#!/usr/bin/env bash
# GUI provision test (I19): drive the real wizard under Xvfb with xdotool, as a sudo user WITH a password,
# so the askpass password dialog is exercised. Screenshots go to spike/.out/gui/.
#
#   script -qec "spike/gui-provision-test.sh [release]" /dev/null > spike/.out/gui.log 2>&1
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB="$REPO/app/src-tauri/target/release/bundle/deb/Odoo Dev Panel_0.0.1_amd64.deb"
REL=${1:-24.04}
C="odp-gui-${REL//./}"
OUT="$REPO/spike/.out/gui"; mkdir -p "$OUT"; rm -f "$OUT"/*.png
KEEP=${KEEP:-0}

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }
as_dev() { lxc exec "$C" -- sudo -iu dev bash -ls "$@"; }
shot() { in_c sh -c "DISPLAY=:99 import -window root /tmp/$1.png" && lxc file pull "$C/tmp/$1.png" "$OUT/$1.png"; }

lxc delete -f "$C" >/dev/null 2>&1 || true
say "launch ubuntu:$REL"
lxc launch "ubuntu:$REL" "$C" >/dev/null
in_c cloud-init status --wait >/dev/null || true
lxc file push "$DEB" "$C/root/odp.deb"
in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb postgresql xvfb dbus-x11 xdotool imagemagick >/dev/null'
in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; sleep 3; pg_isready -h localhost'
# sudo user WITH a password: the wizard must ask for it through the dialog.
in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev; echo "dev:s3cret-pass" | chpasswd'

say "start Xvfb and the app"
# systemd-run: a plain background job dies when `lxc exec` returns
in_c systemd-run --quiet --unit=xvfb Xvfb :99 -screen 0 1400x1000x24
sleep 2
as_dev <<'EOS'
true
EOS
in_c systemd-run --quiet --unit=odp-gui --uid=dev --setenv=HOME=/home/dev --setenv=DISPLAY=:99 --setenv=WEBKIT_DISABLE_COMPOSITING_MODE=1 \
    dbus-run-session -- /usr/bin/odoo-dev-panel
sleep 12
shot 01-main

xd() { in_c sh -c "DISPLAY=:99 xdotool $*"; }
key() { for k in "$@"; do xd key --delay 150 "$k"; done; }
tab() { for _ in $(seq "$1"); do xd key --delay 120 Tab; done; }

say "open wizard"
xd mousemove 700 500 click 1; sleep 1
tab 1; sleep 1
shot 02-focus
key Return; sleep 2
shot 03-wizard

say "Review (defaults: Odoo 17)"
# order in the form: Close, version, user, config, port, branch, enterprise source, custom textarea, Review
tab 9; sleep 1
shot 04-before-review
key Return; sleep 6
shot 05-review

say "Create installation"
# order in review: Close, details, details, Back, Create
tab 5; sleep 1
shot 06-before-create
key Return; sleep 4
shot 07-password-dialog
xd type --delay 60 "s3cret-pass"; sleep 1
key Return
say "wait for provision (up to 12 min)"
for i in $(seq 72); do
    if in_c test -f /opt/odoo17/.odp-provision.json && in_c grep -q '"status": "complete"' /opt/odoo17/.odp-provision.json; then break; fi
    sleep 10
done
shot 08-done
in_c cat /opt/odoo17/.odp-provision.json | head -5
in_c grep -q '"status": "complete"' /opt/odoo17/.odp-provision.json
in_c stat -c '%U:%G %a %n' /opt/odoo17 /opt/odoo17/venv /etc/odoo/odoo17/default.conf
in_c /opt/odoo17/venv/bin/python /opt/odoo17/odoo/odoo-bin --version
if [ "$KEEP" = 1 ]; then echo "kept $C"; else lxc delete -f "$C" >/dev/null; fi
say "PASS"
