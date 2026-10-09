#!/usr/bin/env bash
# Phase 13 (Z1-Z4) and U8 end to end against a real Odoo and PostgreSQL: provision Odoo 17 in fresh LXD containers,
# then metadata and sizes of a real database, a session of another role (hidden, then visible after the pg_monitor
# grant), a lock wait and a long query found by the verdict, cancel of the role's own query (and refusal for another
# role's), the SQL log of a --log-sql run grouped by statement, and the log of an Odoo started outside the app.
# Needs the built .deb and network (Odoo clone, uv Python, pip).
#
#   script -qec "spike/perf-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/perf.log
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
    C="odp-perf-${REL//./}"
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

    say "provision Odoo $V and a database"
    as_dev_v <<'EOS'
set -e
odp provision run -V $V -y >/dev/null
odp modules install /etc/odoo/odoo$V/default.conf base -d devdb --no-snapshot --yes >/dev/null
echo provisioned
EOS

    say "Z1/Z2: models, one model, external IDs, sizes, bounded count"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
r=/opt/odoo$V
odp db models $r devdb -q res.partner --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
m = {x['model']: x for x in d['models']}
assert 'res.partner' in m and m['res.partner']['fields'] > 20 and 'base' in m['res.partner']['modules'], d
assert m['res.partner']['name'] == 'Contact', m['res.partner']
print('models ok:', d['total'], 'matching')"
odp db model $r devdb res.partner --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
f = {x['name']: x for x in d['fields']}
assert f['parent_id']['relation'] == 'res.partner' and f['parent_id']['ttype'] == 'many2one', f['parent_id']
assert any(x['model'] == 'res.users' and x['name'] == 'partner_id' for x in d['incoming']), d['incoming'][:5]
print('model ok:', len(d['fields']), 'fields,', len(d['incoming']), 'incoming')"
odp db xmlids $r devdb -q base.main_company --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(x['model'] == 'res.company' and x['res_id'] == 1 for x in d['xmlids']), d
print('xmlids ok')"
odp db sizes $r devdb --limit 5 --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['database_bytes'] > 1e6 and len(d['tables']) == 5 and d['source'] == 'postgresql', d
print('sizes ok:', round(d['database_bytes'] / 1e6), 'MB')"
odp db count $r devdb res_partner --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['count'] >= 1 and not d['capped'], d
print('count ok:', d['count'])"
odp db models $r devdb -q "x'; drop table res_partner; --" 2>/tmp/inj.err && fail "unsafe search accepted"
grep -q "letters, digits" /tmp/inj.err || fail "wrong refusal"
odp db count $r devdb 'res_partner"' 2>/dev/null && fail "bad table accepted"
echo "metadata ok"
EOS

    say "Z3: another role's session hidden, lock wait, long query, verdict"
    in_c sudo -u postgres psql -qc "CREATE ROLE other LOGIN PASSWORD 'other'"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
PGPASSWORD=other nohup psql -h localhost -U other -d postgres -c 'SELECT pg_sleep(300)' >/tmp/other.out 2>&1 &
pw=$(awk -F' = ' '/^db_password/{print $2}' $conf)
export PGPASSWORD=$pw
psql -h localhost -U odoo$V -d devdb -qc "BEGIN; UPDATE res_partner SET name = name WHERE id = 1; SELECT pg_sleep(90); COMMIT;" >/dev/null 2>&1 &
sleep 2
psql -h localhost -U odoo$V -d devdb -qc "UPDATE res_partner SET name = name WHERE id = 1" > /tmp/blocked.out 2>&1 &
sleep 12
odp perf /opt/odoo$V -d devdb --json > /tmp/p1.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/p1.json"))
s = d["postgres"]["server"]
assert not s["monitor"], s
ids = {f["id"]: f for f in d["verdict"]}
assert "blocked" in ids and "long-queries" in ids and "visibility" in ids, list(ids)
other = [x for x in d["postgres"]["sessions"] if x["role"] == "other"]
assert other and other[0]["hidden"] and other[0]["query"] is None, other
blocked = [x for x in d["postgres"]["sessions"] if x["blocked_by"]]
assert blocked and blocked[0]["mine"], blocked
open("/tmp/blocked.pid", "w").write(str(blocked[0]["pid"]))
open("/tmp/other.pid", "w").write(str(other[0]["pid"]))
print("verdict ok:", [f["id"] for f in d["verdict"]])
PY
odp perf /opt/odoo$V -d devdb | head -8
if odp perf /opt/odoo$V -d devdb --cancel "$(cat /tmp/other.pid)"; then fail "cancel of another role's query accepted"; fi
odp perf /opt/odoo$V -d devdb --cancel "$(cat /tmp/blocked.pid)" | grep -q "'cancelled': True" || fail "cancel"
sleep 1
grep -q "canceling statement due to user request" /tmp/blocked.out || { cat /tmp/blocked.out; fail "the blocked query was not cancelled"; }
echo "cancel ok"
EOS

    say "Z3: pg_monitor grant shows the other role's query; revoke"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
PGPASSWORD=other nohup psql -h localhost -U other -d postgres -c 'SELECT pg_sleep(120)' >/tmp/other2.out 2>&1 &
sleep 2
odp perf /opt/odoo$V --grant --yes | tail -2
odp perf /opt/odoo$V -d devdb --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['postgres']['server']['monitor'], d['postgres']['server']
other = [x for x in d['postgres']['sessions'] if x['role'] == 'other']
assert other and not other[0]['hidden'] and 'pg_sleep' in other[0]['query'], other
assert 'visibility' not in [f['id'] for f in d['verdict']]
print('grant ok: other role visible')"
odp perf /opt/odoo$V --revoke --yes >/dev/null
odp perf /opt/odoo$V -d devdb --json | python3 -c "import json,sys; assert not json.load(sys.stdin)['postgres']['server']['monitor']; print('revoke ok')"
EOS

    say "Z4: --log-sql run grouped by statement"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
sid=$(odp --json start $conf -d devdb --log-sql --stop-after-init | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
for i in $(seq 1 60); do odp --json ps -u odoo$V | python3 -c "import json,sys; s=[x for x in json.load(sys.stdin) if x['id']=='$sid'][0]; sys.exit(0 if s['state'] not in ('running','stopping') else 1)" && break; sleep 2; done
odp --json logs -u odoo$V "$sid" --sql > /tmp/sql.json
odp logs -u odoo$V "$sid" | grep -c "odoo.sql_db: .*query: " > /tmp/sql.count
python3 - <<'PY'
import json
d = json.load(open("/tmp/sql.json"))
raw = int(open("/tmp/sql.count").read())
assert raw >= 10 and d["queries"] == raw and d["timed"], ({k: d[k] for k in ("queries", "statements", "timed")}, raw)
print("sql ok:", d["queries"], "queries,", d["statements"], "shapes, timed:", d["timed"])
print("  top:", d["top"][0]["count"], "x", d["top"][0]["statement"][:90])
PY
odp logs -u odoo$V "$sid" --sql | head -4
EOS

    say "U8: log of an Odoo started outside the app (config logfile)"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
ext=/etc/odoo/odoo$V/outside.conf
sed 's/^http_port = .*/http_port = 8099/' $conf > $ext
grep -q '^http_port' $ext || echo "http_port = 8099" >> $ext
echo "logfile = /opt/odoo$V/outside.log" >> $ext
chmod 640 $ext
odp logs --config $ext 2>/tmp/no.err && fail "a log before Odoo wrote one"
grep -q "No such file" /tmp/no.err || { cat /tmp/no.err; fail "wrong reason"; }
sudo -u odoo$V setsid /opt/odoo$V/venv/bin/python /opt/odoo$V/odoo/odoo-bin -c $ext -d devdb >/dev/null 2>&1 &
for i in $(seq 1 60); do curl -fsS -o /dev/null http://localhost:8099/web/login && break; sleep 2; done
odp logs --config $ext 2>/dev/null | grep -q "HTTP service (werkzeug) running" || fail "outside log not read"
odp logs --config $ext -l WARNING >/dev/null || fail "level filter"
odp perf /opt/odoo$V -d devdb --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
t = [x for x in d['odoo'] if (x['config'] or '').endswith('outside.conf')]
assert t and t[0]['rss_bytes'] > 50e6, d['odoo']
print('outside process seen:', t[0]['pid'], round(t[0]['rss_bytes'] / 2**20), 'MiB')"
sudo pkill -f "odoo-bin -c $ext" || true
echo "ALL PERF CHECKS PASSED"
EOS
    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
done
