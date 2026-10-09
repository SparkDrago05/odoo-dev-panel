#!/usr/bin/env bash
# Phase 11 (K1-K3) end to end against a real Odoo: provision Odoo 17 in fresh LXD containers, install a scaffolded
# module, then run the safe-upgrade recipe (snapshot, upgrade, tests), a declined gate, a saved workflow that fails
# at a run-as command and is retried from that step, and the test-copy recipe (clone, neutralize, start) followed
# by an instance.stop workflow. Needs the built .deb and network (Odoo clone, uv Python, pip).
#
#   script -qec "spike/tasks-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/tasks.log
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
    C="odp-task-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    say "install .deb and PostgreSQL; user dev"
    lxc file push "$DEB" "$C/root/odp.deb"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb postgresql curl >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev'

    say "provision Odoo $V; a custom repository with a remote; module odp_demo installed in devdb"
    as_dev_v <<'EOS'
set -e
odp provision run -V $V -y >/dev/null
git config --global user.email dev@example.com && git config --global user.name dev
mkdir -p /opt/odoo$V/custom/dev && cd /opt/odoo$V/custom/dev && git init -q -b main
conf=/etc/odoo/odoo$V/default.conf
python3 - "$conf" "$V" <<'PY'
import re, sys
p, v = sys.argv[1], sys.argv[2]
t = open(p).read()
t = re.sub(r"(addons_path = [^\n]*(?:\n\t[^\n]*)*)", r"\1,\n\t/opt/odoo%s/custom/dev" % v, t, count=1)
open(p, "w").write(t)
PY
odp modules scaffold /opt/odoo$V /opt/odoo$V/custom/dev odp_demo --yes >/dev/null
mkdir -p odp_demo/tests
printf 'from . import test_basic\n' > odp_demo/tests/__init__.py
cat > odp_demo/tests/test_basic.py <<'P'
from odoo.tests.common import TransactionCase


class TestBasic(TransactionCase):
    def test_partner(self):
        self.assertTrue(self.env["res.partner"].create({"name": "odp"}).id)
P
git add -A && git commit -qm "odp_demo"
git init -q --bare ~/remote-dev.git && git remote add origin ~/remote-dev.git && git push -qu origin main
odp modules install $conf odp_demo -d devdb --no-snapshot --yes >/dev/null
odp tasks list
EOS

    say "K3 safe-upgrade recipe: snapshot, upgrade (gate approved by --yes), tests"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
odp tasks preview safe-upgrade -p config=$conf -p database=devdb -p modules=odp_demo
odp tasks run safe-upgrade -p config=$conf -p database=devdb -p modules=odp_demo --yes --json > /tmp/r1.json \
    || { cat /tmp/r1.json; fail "safe-upgrade"; }
python3 - <<'PY'
import json
r = json.load(open("/tmp/r1.json"))
assert r["status"] == "ok", r
assert [s["status"] for s in r["steps"]] == ["ok", "ok", "ok"], r["steps"]
assert r["steps"][0]["result"]["backup"].startswith("/"), r["steps"][0]
assert r["steps"][1]["gate"] and "odoo" in r["steps"][1]["identity"], r["steps"][1]
assert "passed" in r["steps"][2]["summary"], r["steps"][2]
print("safe-upgrade ok:", r["steps"][0]["result"]["backup"], "|", r["steps"][2]["summary"])
PY
odp db snapshots /opt/odoo$V | grep -q devdb || fail "no snapshot of devdb listed"
EOS

    say "declined gate: the upgrade does not run"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
if printf 'y\nn\n' | odp tasks run safe-upgrade -p config=$conf -p database=devdb -p modules=odp_demo > /tmp/r2.txt; then
    cat /tmp/r2.txt; fail "a declined run reported success"
fi
grep -q "declined" /tmp/r2.txt || { cat /tmp/r2.txt; fail "no declined status"; }
odp tasks history safe-upgrade --json | python3 -c "
import json, sys
r = json.load(sys.stdin)[0]
assert r['status'] == 'declined' and r['failed_at'] == 1, r
assert [s['status'] for s in r['steps']] == ['ok', 'declined'], r['steps']
print('declined ok: snapshot ran, upgrade did not')"
EOS

    say "saved workflow: pull, clone, run-as command fails, retry from the failed step, drop the copy"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
cat > /tmp/flow.toml <<T
name = "Container flow"

[params.config]
kind = "config"

[params.database]
kind = "database"

[params.copy]
kind = "new_database"

[[steps]]
op = "git.pull"
repos = ["/opt/odoo$V/custom/dev"]

[[steps]]
op = "db.clone"
config = "{config}"
database = "{database}"
target = "{copy}"

[[steps]]
op = "command"
as = "run-as"
installation = "{installation}"
argv = ["test", "-e", "/tmp/odp-flag"]

[[steps]]
op = "db.drop"
config = "{config}"
database = "{copy}"
T
odp tasks import /tmp/flow.toml --name flow
odp tasks show flow | head -3
rm -f /tmp/odp-flag
if odp tasks run flow -p config=$conf -p database=devdb -p copy=devcopy1 --yes > /tmp/r3.txt; then
    cat /tmp/r3.txt; fail "the command step should fail"
fi
cat /tmp/r3.txt | tail -12
run=$(odp tasks history flow --json | python3 -c "
import json, sys
r = json.load(sys.stdin)[0]
assert r['status'] == 'fail' and r['failed_at'] == 2, r
assert [s['status'] for s in r['steps']] == ['ok', 'ok', 'fail'], r['steps']
assert 'odoo' in r['steps'][2]['identity'], r['steps'][2]
print(r['run'])")
odp db list /opt/odoo$V | grep -q devcopy1 || fail "the clone is missing"
touch /tmp/odp-flag
odp tasks retry "$run" --yes > /tmp/r4.txt || { cat /tmp/r4.txt; fail "retry"; }
grep -q "from step 3" /tmp/r4.txt || fail "the retry did not start at the failed step"
odp tasks history flow --json | python3 -c "
import json, sys
r = json.load(sys.stdin)[0]
assert r['status'] == 'ok' and r['retry_of'] == '$run' and r['start'] == 2, r
assert [s['index'] for s in r['steps']] == [2, 3], r['steps']
print('retry ok: steps 3-4 ran, clone and pull not repeated')"
if odp db list /opt/odoo$V | grep -q devcopy1; then fail "the copy was not dropped"; fi
cp /tmp/flow.toml /tmp/flow2.toml && sed -i 's/Container flow/Changed/' /tmp/flow2.toml
odp tasks import /tmp/flow2.toml --name flow --overwrite
rm -f /tmp/odp-flag
odp tasks run flow -p config=$conf -p database=devdb -p copy=devcopy2 --yes > /dev/null || true
run=$(odp tasks history flow --json | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['run'])")
odp tasks import /tmp/flow.toml --name flow --overwrite
if odp tasks retry "$run" --yes 2> /tmp/r5.err; then fail "retry of a changed workflow accepted"; fi
grep -q "changed since" /tmp/r5.err || { cat /tmp/r5.err; fail "wrong refusal"; }
echo "changed-workflow retry refused ok"
EOS

    say "test-copy recipe (clone, neutralize, start), then instance.stop"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
odp tasks run test-copy -p config=$conf -p database=devdb -p target=devcopy3 --yes --json > /tmp/r6.json \
    || { cat /tmp/r6.json; fail "test-copy"; }
port=$(python3 -c "
import json
r = json.load(open('/tmp/r6.json'))
assert r['status'] == 'ok', r
assert [s['op'] for s in r['steps']] == ['db.clone', 'db.neutralize', 'instance.start'], r['steps']
print(r['steps'][2]['result']['port'])")
for i in $(seq 1 60); do curl -fsS -o /dev/null "http://localhost:$port/web/login" && break; sleep 2; done
curl -fsS -o /dev/null "http://localhost:$port/web/login" || fail "Odoo on the copy does not answer on $port"
echo "copy serves on $port"
cat > /tmp/stop.toml <<T
name = "Stop"
[params.config]
kind = "config"
[[steps]]
op = "instance.stop"
config = "{config}"
T
odp tasks import /tmp/stop.toml --name stop
odp tasks run stop -p config=$conf --yes --json | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert r['status'] == 'ok' and 'stopped 1' in r['steps'][0]['summary'], r
print('stop ok:', r['steps'][0]['summary'])"
sleep 3
if curl -fsS -o /dev/null "http://localhost:$port/web/login"; then fail "Odoo still answers after stop"; fi
ls -l ~/.local/state/odoo-dev-panel/tasks/history.jsonl
grep -c '"type": "start"' ~/.local/state/odoo-dev-panel/tasks/history.jsonl
if grep -qi 'password' ~/.local/state/odoo-dev-panel/tasks/history.jsonl; then fail "history mentions a password"; fi
echo "ALL TASK CHECKS PASSED"
EOS
    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
done
