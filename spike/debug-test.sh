#!/usr/bin/env bash
# Phase 12 (Q1-Q4) end to end against a real Odoo: provision Odoo 17 in fresh LXD containers, install debugpy,
# then a server preset started under debugpy as the run-as user with a real DAP attach (breakpoint hit in an Odoo
# controller, frame checked, continue), a test preset under the debugger, launch.json merge (new, merge, conflict,
# JSONC refused), odp start --debug-port, and a Tasks instance.start step with a debug port.
# Needs the built .deb and network (Odoo clone, uv Python, pip).
#
#   script -qec "spike/debug-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/debug.log
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
    C="odp-dbg-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    say "install .deb and PostgreSQL; user dev"
    lxc file push "$DEB" "$C/root/odp.deb"
    lxc file push "$REPO/spike/dap_check.py" "$C/usr/local/bin/dap_check.py"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb postgresql curl >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev'

    say "provision Odoo $V, a database, a module with a test; debugpy missing first"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
odp provision run -V $V -y >/dev/null
conf=/etc/odoo/odoo$V/default.conf
mkdir -p /opt/odoo$V/custom/dev
python3 - "$conf" "$V" <<'PY'
import re, sys
p, v = sys.argv[1], sys.argv[2]
t = open(p).read()
t = re.sub(r"(addons_path = [^\n]*(?:\n\t[^\n]*)*)", r"\1,\n\t/opt/odoo%s/custom/dev" % v, t, count=1)
t = re.sub(r"^workers = .*$", "workers = 2", t, flags=re.M) if re.search(r"^workers", t, re.M) else t + "workers = 2\n"
open(p, "w").write(t)
PY
odp modules scaffold /opt/odoo$V /opt/odoo$V/custom/dev odp_demo --yes >/dev/null
mkdir -p /opt/odoo$V/custom/dev/odp_demo/tests
printf 'from . import test_basic\n' > /opt/odoo$V/custom/dev/odp_demo/tests/__init__.py
cat > /opt/odoo$V/custom/dev/odp_demo/tests/test_basic.py <<'P'
from odoo.tests.common import TransactionCase


class TestBasic(TransactionCase):
    def test_partner(self):
        self.assertTrue(self.env["res.partner"].create({"name": "odp"}).id)
P
odp modules install $conf base -d devdb --no-snapshot --yes >/dev/null
odp debug add web -c $conf -d devdb --name "Web debug"
odp debug list
if odp debug plan web > /tmp/p0.txt; then cat /tmp/p0.txt; fail "plan ok without debugpy"; fi
grep -q "debugpy is not in" /tmp/p0.txt || { cat /tmp/p0.txt; fail "no debugpy check"; }
odp python tool debugpy --root /opt/odoo$V --yes >/dev/null
odp debug plan web | tee /tmp/p1.txt
grep -q -- "-m debugpy --listen 127.0.0.1:5678" /tmp/p1.txt || fail "no debugpy command"
grep -q -- "--workers=0 --max-cron-threads=0" /tmp/p1.txt || fail "workers not forced"
grep -q "any local user" /tmp/p1.txt || fail "no local-user warning"
EOS

    say "Q2: server preset under debugpy, IDE-style attach, breakpoint hit in a controller"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
odp debug start web --yes --json > /tmp/s.json
sid=$(python3 -c "import json; print(json.load(open('/tmp/s.json'))['session']['id'])")
http=$(python3 -c "import json; print(json.load(open('/tmp/s.json'))['session']['meta']['port'])")
ps -o user=,args= -u odoo$V | grep -q "debugpy --listen 127.0.0.1:5678" || fail "debugpy not running as odoo$V"
for i in $(seq 1 60); do curl -fsS -o /dev/null "http://localhost:$http/web/login" && break; sleep 2; done
file=/opt/odoo$V/odoo/addons/web/controllers/webclient.py
line=$(grep -n "return odoo.service.common.exp_version()" $file | head -1 | cut -d: -f1)
[ -n "$line" ] || fail "no breakpoint line in $file"
python3 /usr/local/bin/dap_check.py 5678 "$file" "$line" "http://localhost:$http/web/webclient/version_info" || fail "DAP attach"
curl -fsS -o /dev/null "http://localhost:$http/web/login" || fail "Odoo stopped after detach"
odp stop -u odoo$V "$sid" >/dev/null
sleep 2
if (exec 3<>/dev/tcp/127.0.0.1/5678) 2>/dev/null; then fail "debug port still open after stop"; fi
echo "server debug ok"
EOS

    say "Q2/Q4: test preset under debugpy (throwaway database, history)"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
odp debug add tests -c $conf --kind test -m odp_demo --port 5690
odp debug start tests --yes --json > /tmp/t.json || { cat /tmp/t.json; fail "test preset"; }
python3 -c "
import json
r = json.load(open('/tmp/t.json'))['test']
assert r['status'] == 'passed' and not r['kept'], r
print('test preset ok:', r['tests'], 'test(s)')"
odp modules tests --json | python3 -c "import json,sys; r=json.load(sys.stdin)[0]; assert r['status'] == 'passed', r; print('in module test history:', r['database'])"
EOS

    say "Q1: launch.json new, merge, conflict, replace, JSONC refused"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
root=/opt/odoo$V
odp debug vscode $root --write
python3 -c "
import json
d = json.load(open('$root/.vscode/launch.json'))
names = sorted(c['name'] for c in d['configurations'])
assert names == ['Odoo: Web debug', 'Odoo: tests'], names
assert all(c['type'] == 'debugpy' and c['request'] == 'attach' for c in d['configurations'])
print('new file ok:', names)"
python3 - "$root/.vscode/launch.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["configurations"].append({"name": "Mine", "type": "node", "request": "launch"})
d["configurations"][0]["connect"]["port"] = 9999
json.dump(d, open(p, "w"), indent=2)
PY
if odp debug vscode $root --write > /tmp/v1.txt; then fail "conflict written without --replace"; fi
grep -q "confirm to replace" /tmp/v1.txt || { cat /tmp/v1.txt; fail "no conflict message"; }
odp debug vscode $root --write --replace
ls $root/.vscode/launch.json.bak-* >/dev/null || fail "no backup"
python3 -c "
import json
d = json.load(open('$root/.vscode/launch.json'))
assert any(c['name'] == 'Mine' for c in d['configurations'])
assert all(c.get('connect', {}).get('port') != 9999 for c in d['configurations'])
print('merge + replace ok')"
printf '{\n  // mine\n  "version": "0.2.0",\n  "configurations": [],\n}\n' > $root/.vscode/launch.json
cp $root/.vscode/launch.json /tmp/jsonc.json
if odp debug vscode $root --write > /tmp/v2.txt; then fail "JSONC rewritten"; fi
grep -q "Entries to paste" /tmp/v2.txt || fail "no snippet"
cmp -s $root/.vscode/launch.json /tmp/jsonc.json || fail "JSONC file changed"
echo "JSONC refused ok"
if odp debug open /etc/hostname:1 2>/tmp/o.err; then fail "opened without an IDE"; fi
grep -q "no IDE found" /tmp/o.err || { cat /tmp/o.err; fail "wrong open error"; }
EOS

    say "Run and Tasks with a debug port"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
odp start $conf -d devdb --debug-port 5701 --stop-after-init --dry-run | grep -q "debugpy --listen 127.0.0.1:5701" || fail "odp start --debug-port"
cat > /tmp/dbg.toml <<T
name = "Debug start"
[params.config]
kind = "config"
[[steps]]
op = "instance.start"
config = "{config}"
database = "devdb"
debug_port = 5702
[[steps]]
op = "instance.stop"
config = "{config}"
T
odp tasks import /tmp/dbg.toml --name dbg
odp tasks preview dbg -p config=$conf | grep -q "debugpy --listen 127.0.0.1:5702" || fail "tasks preview"
odp tasks run dbg -p config=$conf --yes --json | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert r['status'] == 'ok', r
print('tasks debug start/stop ok:', [s['summary'] for s in r['steps']])"
grep -qi password ~/.config/odoo-dev-panel/debug-presets.json && fail "preset file mentions a password"
echo "ALL DEBUG CHECKS PASSED"
EOS
    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
done
