#!/usr/bin/env bash
# End-to-end provision test (I20, I21): Odoo 17 (or V=16 and so on) in fresh LXD containers.
#
#   script -qec "spike/provision-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/provision.log
#
# Per release: install the .deb and PostgreSQL, create an orphan PostgreSQL role, run `odp provision run`
# as a sudo user with an enterprise archive and a custom repo, check ownership, modes, receipt, odoo-bin --version,
# the PostgreSQL login, then a second run (everything reused, config unchanged) and a broken-venv repair.
#   V=16 selects another Odoo version (16 builds old C packages: tests the compiler flags on 26.04).
# Needs network (git clone of odoo/odoo, uv Python download, pip).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB=${1:-"$REPO/app/src-tauri/target/release/bundle/deb/Odoo Dev Panel_0.0.1_amd64.deb"}
shift || true
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}
V=${V:-17}   # Odoo version: V=16 tests Python 3.10 and the C build flags

[ -f "$DEB" ] || { echo "no .deb at $DEB" >&2; exit 1; }

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }
# Scripts below are quoted heredocs; V is passed in as a first line.
as_dev_v() { { echo "export V=$V"; cat; } | as_dev; }
as_dev() { lxc exec "$C" -- sudo -iu dev bash -ls "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-prov-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true

    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true

    say "install .deb and PostgreSQL"
    lxc file push "$DEB" "$C/root/odp.deb"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb postgresql >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'

    say "user dev (sudo, test-only NOPASSWD, group odoo-dev)"
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev'

    say "orphan PostgreSQL role odoo$V exists before provisioning (must be reused)"
    in_c runuser -u postgres -- psql -X -q -c "CREATE ROLE odoo$V LOGIN PASSWORD 'old-unknown-password'"

    say "odp provision run -V $V"
    as_dev_v <<'EOS'
set -e
# enterprise stand-in: a zip with one top-level folder (it must be stripped)
python3 - <<'PY'
import os, zipfile
v = os.environ["V"]
with zipfile.ZipFile(f"/home/dev/enterprise-{v}.0.zip", "w") as zf:
    zf.writestr(f"enterprise-{v}.0/web_enterprise/__manifest__.py", "{'name': 'stub'}")
PY
odp provision plan -V $V | head -20
odp provision run -V $V -y --enterprise-archive /home/dev/enterprise-$V.0.zip \
    --custom server-ux=https://github.com/OCA/server-ux.git#$V.0
EOS

    say "checks"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
stat -c '%U:%G %a %n' /opt/odoo$V /opt/odoo$V/odoo /opt/odoo$V/venv /etc/odoo/odoo$V /etc/odoo/odoo$V/default.conf
[ "$(stat -c '%U:%G %a' /opt/odoo$V)" = "dev:odoo$V 2775" ] || fail "root ownership"
[ "$(stat -c '%U' /opt/odoo$V/venv)" = "odoo$V" ] || fail "venv owner"
[ "$(stat -c '%a' /etc/odoo/odoo$V/default.conf)" = "640" ] || fail "config mode"
grep -q '"status": "complete"' /opt/odoo$V/.odp-provision.json || fail "receipt not complete"
[ -f /opt/odoo$V/enterprise/web_enterprise/__manifest__.py ] || fail "enterprise archive not extracted/stripped"
[ -d /opt/odoo$V/custom/server-ux/.git ] || fail "custom repo not cloned"
grep -q "addons_path = .*enterprise.*custom/server-ux" <(tr -d '\n\t' < /etc/odoo/odoo$V/default.conf) || fail "addons_path"
/opt/odoo$V/venv/bin/python /opt/odoo$V/odoo/odoo-bin --version
pw=$(sed -n 's/^db_password = //p' /etc/odoo/odoo$V/default.conf)
PGPASSWORD=$pw psql -h localhost -U odoo$V -d postgres -tAc 'select current_user'
odp agent status -u odoo$V
before=$(md5sum < /etc/odoo/odoo$V/default.conf)
echo "  second run reuses everything:"
odp provision run -V $V -y --enterprise-archive /home/dev/enterprise-$V.0.zip \
    --custom server-ux=https://github.com/OCA/server-ux.git#$V.0 | grep -E "kept|already|FAILED|ready"
[ "$(md5sum < /etc/odoo/odoo$V/default.conf)" = "$before" ] || fail "config changed by second run"
PGPASSWORD=$pw psql -h localhost -U odoo$V -d postgres -tAc 'select 1' >/dev/null || fail "login after second run"
echo "  reuse: ok"
echo "  broken venv is repaired:"
sudo rm /opt/odoo$V/venv/bin/python
odp provision run -V $V -y --enterprise-archive /home/dev/enterprise-$V.0.zip \
    --custom server-ux=https://github.com/OCA/server-ux.git#$V.0 | grep -E "broken|moved|FAILED|ready"
ls -d /opt/odoo$V/venv.broken-* >/dev/null || fail "broken venv not moved aside"
/opt/odoo$V/venv/bin/python /opt/odoo$V/odoo/odoo-bin --version || fail "venv not repaired"
echo "  repair: ok"
echo "  stopped agent whose unit still holds a process is restarted:"
odp run -u odoo$V -- /bin/sleep 600 >/dev/null
odp agent stop -u odoo$V >/dev/null
sleep 3
odp provision run -V $V -y --enterprise-archive /home/dev/enterprise-$V.0.zip \
    --custom server-ux=https://github.com/OCA/server-ux.git#$V.0 | grep -E "FAILED|ready"
odp agent status -u odoo$V | grep -q " running " || fail "agent not running after rerun"
echo "  agent restart: ok"
EOS

    say "agent survives: still running after the provision session ended"
    in_c systemctl list-units 'odp-agent-odoo$V-*' --no-legend

    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
    say "PASS"
done
