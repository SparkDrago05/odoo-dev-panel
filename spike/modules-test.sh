#!/usr/bin/env bash
# Phase 9 (M1-M9) end to end against a real Odoo: provision Odoo 17 in fresh LXD containers, scaffold a module
# into a custom repository, then check, changed, tests in a throwaway database (passing and failing), install
# and upgrade with a snapshot. Needs the built .deb and network (Odoo clone, uv Python, pip).
#
#   script -qec "spike/modules-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/modules.log
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB=${1:-$(ls -t "$REPO"/app/src-tauri/target/release/bundle/deb/*.deb 2>/dev/null | head -1)}
shift || true
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}
V=${V:-17}
[ -f "$DEB" ] || { echo "no .deb found" >&2; exit 1; }

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }
as_dev_v() { { echo "export V=$V"; cat; } | lxc exec "$C" -- sudo -iu dev bash -ls; }

for REL in "${RELEASES[@]}"; do
    C="odp-mod-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    say "install .deb and PostgreSQL; user dev"
    lxc file push "$DEB" "$C/root/odp.deb"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb postgresql >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev'

    say "provision Odoo $V with an empty custom repository on the addons_path"
    as_dev_v <<'EOS'
set -e
odp provision run -V $V -y >/dev/null
mkdir -p /opt/odoo$V/custom/dev && cd /opt/odoo$V/custom/dev && git init -q
conf=/etc/odoo/odoo$V/default.conf
python3 - "$conf" "$V" <<'PY'
import re, sys
p, v = sys.argv[1], sys.argv[2]
t = open(p).read()
t = re.sub(r"(addons_path = [^\n]*(?:\n\t[^\n]*)*)", r"\1,\n\t/opt/odoo%s/custom/dev" % v, t, count=1)
open(p, "w").write(t)
PY
grep -A4 addons_path $conf
EOS

    say "scaffold, check, changed"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
odp modules scaffold /opt/odoo$V /opt/odoo$V/custom/dev odp_demo --yes
mkdir -p /opt/odoo$V/custom/dev/odp_demo/tests
cat > /opt/odoo$V/custom/dev/odp_demo/tests/__init__.py <<'P'
from . import test_basic
P
cat > /opt/odoo$V/custom/dev/odp_demo/tests/test_basic.py <<'P'
from odoo.tests.common import TransactionCase


class TestBasic(TransactionCase):
    def test_partner(self):
        self.assertTrue(self.env["res.partner"].create({"name": "odp"}).id)
P
echo "from . import models" > /opt/odoo$V/custom/dev/odp_demo/__init__.py
odp modules check $conf odp_demo || fail "scaffolded module has manifest errors"
odp modules changed $conf --json > /tmp/changed.json
python3 -c "import json; d=json.load(open('/tmp/changed.json')); m=d['modules']['odp_demo']; assert m['new'] and m['action']=='install', m; print('changed ok: new module, install suggested')"
EOS

    say "tests in a throwaway database: passing (dropped), then failing (kept, then dropped)"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
odp modules test $conf odp_demo --yes --json > /tmp/t1.json || fail "passing test run reported failure"
python3 - <<'PY'
import json
r = json.load(open("/tmp/t1.json"))
assert r["status"] == "passed" and r["tests"] >= 1 and not r["kept"], r
print("pass ok:", r["tests"], "test(s), database dropped:", r["database"])
PY
psql -h localhost -U odoo$V -d postgres -tAc "select count(*) from pg_database where datname like 'odp_test_%'" \
    -w 2>/dev/null || true
cat >> /opt/odoo$V/custom/dev/odp_demo/tests/test_basic.py <<'P'

    def test_fails_on_purpose(self):
        self.assertEqual(1, 2)
P
if odp modules test $conf odp_demo --yes --json > /tmp/t2.json; then fail "failing test run reported success"; fi
python3 - <<'PY'
import json
r = json.load(open("/tmp/t2.json"))
assert r["status"] == "failed" and r["kept"], r
assert any("test_fails_on_purpose" in f["test"] for f in r["failed"]), r["failed"]
print("fail ok: kept", r["database"], "failed:", [f["test"] for f in r["failed"]])
open("/tmp/t2.id", "w").write(r["id"])
PY
odp modules tests | head -3
odp modules drop-test "$(cat /tmp/t2.id)"
odp modules tests --json | python3 -c "import json,sys; r=json.load(sys.stdin)[0]; assert not r['kept'], r; print('drop-test ok')"
EOS

    say "install, then upgrade with a snapshot"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
sed -i '/def test_fails_on_purpose/,$d' /opt/odoo$V/custom/dev/odp_demo/tests/test_basic.py
odp modules install $conf odp_demo -d devdb --no-snapshot --yes || fail "install"
odp modules upgrade $conf odp_demo -d devdb --yes --json > /tmp/u.json || fail "upgrade"
python3 -c "import json; r=json.load(open('/tmp/u.json')); assert r['exit_code']==0 and r['backup'], r; print('upgrade ok, snapshot', r['backup'])"
odp modules upgrade $conf ghost_module -d devdb --plan && fail "upgrade of an unknown module accepted"
echo "ALL MODULE CHECKS PASSED"
EOS
    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
done
