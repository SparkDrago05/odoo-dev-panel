#!/usr/bin/env bash
# Doctor + venv repair test (H1, H12) in fresh LXD containers.
#
#   script -qec "spike/repair-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/repair.log
#
# Per release: build a legacy installation the way the reference machine has them (user odoo17 with home
# /opt/odoo17, a shallow Odoo 17 clone, a venv made with Python 3.11 holding two extra packages), then break
# it like a distribution upgrade does: venv/bin/python now points to the system python3 (3.12 or 3.14),
# while the packages stay in lib/python3.11. Then, as a sudo user:
#   odp doctor        flags the broken venv (H1) with a repair
#   odp repair venv   with a requirement that cannot be installed: fails, the old venv is untouched
#   odp repair venv   --carry-extras: reuses the Python 3.12 another version user installed, new venv in place, old one kept as venv.bak-*, extras installed,
#                     owned by odoo17, odoo-bin --version works as odoo17, the doctor no longer flags it
# Needs network (git clone of odoo/odoo, uv Python download, pip).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB=${1:-"$REPO/app/src-tauri/target/release/bundle/deb/Odoo Dev Panel_0.0.1_amd64.deb"}
shift || true
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}

[ -f "$DEB" ] || { echo "no .deb at $DEB" >&2; exit 1; }

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }
as_dev() { lxc exec "$C" -- sudo -iu dev bash -ls "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-repair-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true

    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true

    say "install .deb and git"
    lxc file push "$DEB" "$C/root/odp.deb"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb git >/dev/null'

    say "user dev (sudo, test-only NOPASSWD, group odoo-dev)"
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev'

    say "legacy installation: odoo17 owns /opt/odoo17, venv with Python 3.11, then the system python under it"
    in_c sh -ec '
        cd /tmp
        useradd --system --user-group --create-home --home-dir /opt/odoo17 --shell /bin/bash odoo17
        usermod -aG odoo-dev odoo17
        chmod 0755 /opt/odoo17   # useradd makes homes 0750; the reference machine has them readable
        runuser -u odoo17 -- git clone -q --depth=1 --single-branch --branch 17.0 https://github.com/odoo/odoo.git /opt/odoo17/odoo
        export UV_PYTHON_INSTALL_DIR=/opt/odoo-dev-panel/python
        # As on the reference machine: another version user installed the shared Python 3.12 with umask 022,
        # so its files (and .temp) are read-only for odoo17. The repair must reuse it, not reinstall it.
        useradd --system --user-group --create-home --home-dir /opt/odoo16 odoo16
        usermod -aG odoo-dev odoo16
        runuser -u odoo16 -- sh -c "umask 022; cd /tmp; HOME=/opt/odoo16 UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR /usr/lib/odoo-dev-panel/bin/uv python install -q --no-bin 3.12"
        # The old interpreter (3.11) is put there by root; odoo17 only builds its old venv from it.
        HOME=/root /usr/lib/odoo-dev-panel/bin/uv python install -q --no-bin 3.11
        runuser -u odoo17 -- env HOME=/opt/odoo17 UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR /usr/lib/odoo-dev-panel/bin/uv venv -q --python 3.11 --python-preference only-managed /opt/odoo17/venv
        runuser -u odoo17 -- env HOME=/opt/odoo17 /usr/lib/odoo-dev-panel/bin/uv pip install -q --python /opt/odoo17/venv/bin/python tabulate six
        ln -sfn /usr/bin/python3 /opt/odoo17/venv/bin/python
        ls -ld /opt/odoo-dev-panel/python/.temp
        ls -l /opt/odoo17/venv/bin/python; ls /opt/odoo17/venv/lib'

    say "doctor flags the broken venv"
    as_dev <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
out=$(odp doctor --no-databases || true)
echo "$out" | grep -A2 "Broken venv in /opt/odoo17" || { echo "$out"; fail "no H1 finding"; }
echo "$out" | grep -q "repair: odp repair venv /opt/odoo17" || fail "no repair offered"
echo "  doctor: ok"
EOS

    say "agent of odoo17, build packages from the plan's hint"
    as_dev <<'EOS'
set -e
odp agent start -u odoo17
cmd=$(odp repair venv /opt/odoo17 --plan | sed -n 's/.*Run: \(sudo apt-get install .*\)$/\1/p' | head -1)
if [ -n "$cmd" ]; then echo "  $cmd"; DEBIAN_FRONTEND=noninteractive $cmd -y -qq >/dev/null; fi
odp repair venv /opt/odoo17 --plan --carry-extras | sed -n '1,30p'
EOS

    say "a failed rebuild leaves the old venv in service"
    as_dev <<'EOS'
set -eo pipefail
fail() { echo "  FAIL $*"; exit 1; }
sudo install -d -o odoo17 -g odoo17 /opt/odoo17/custom/broken
echo "odp-no-such-package-for-repair-test" | sudo -u odoo17 tee /opt/odoo17/custom/broken/requirements.txt >/dev/null
if odp repair venv /opt/odoo17 -y 2>&1 | tail -5; then fail "repair should have failed"; fi
[ -d /opt/odoo17/venv/lib/python3.11 ] || fail "old venv changed"
[ ! -e /opt/odoo17/venv.new ] || fail "venv.new left behind"
ls -d /opt/odoo17/venv.bak-* 2>/dev/null && fail "backup made on a failed build"
ls ~/.local/state/odoo-dev-panel/repairs/ | grep -q venv || fail "no receipt"
sudo rm -rf /opt/odoo17/custom/broken
echo "  failed rebuild: ok"
EOS

    say "repair: rebuild, validate, swap"
    as_dev <<'EOS'
set -eo pipefail
fail() { echo "  FAIL $*"; exit 1; }
odp repair venv /opt/odoo17 -y --carry-extras | tee /tmp/repair.out | grep -E "^== |already installed|Not in the new venv|rebuilt"
grep -q "Python 3.12 already installed" /tmp/repair.out || fail "shared Python 3.12 was not reused"
[ "$(stat -c %U /opt/odoo17/venv)" = odoo17 ] || fail "venv owner"
grep -q relocatable /opt/odoo17/venv/pyvenv.cfg || fail "venv not relocatable"
bak=$(ls -d /opt/odoo17/venv.bak-*) || fail "no backup"
[ -d "$bak/lib/python3.11" ] || fail "backup is not the old venv"
[ ! -e /opt/odoo17/venv.new ] || fail "venv.new left behind"
ls /opt/odoo17/venv/lib/python3.12/site-packages | grep -qi '^tabulate-' || fail "extras not carried over"
sudo -u odoo17 /opt/odoo17/venv/bin/python /opt/odoo17/odoo/odoo-bin --version || fail "odoo-bin --version as odoo17"
readlink -f /opt/odoo17/venv/bin/python | grep -q '^/opt/odoo-dev-panel/python/' || fail "interpreter not the managed one"
if odp doctor --no-databases | grep -E "venv-broken|Broken venv|package\(s\) from /opt/odoo17/odoo"; then fail "doctor still flags the venv"; fi
echo "  repair: ok"
EOS

    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
    say "PASS"
done
