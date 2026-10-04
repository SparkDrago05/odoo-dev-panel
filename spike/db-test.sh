#!/usr/bin/env bash
# Database basics test (B3-B7) in fresh LXD containers, against a real PostgreSQL. No .deb needed.
#
#   script -qec "spike/db-test.sh [release ...]" /dev/null > spike/.out/db.log
#
# Builds a stub Odoo 17 installation (user odoo17, home /opt/odoo17, config with db_user/db_password) with a
# database `shop` (the tables the tool and the default recipe touch) and a filestore. Then, as odoo17 (the
# run-as user is the caller, so the database and file tools run locally; the agent path has its own tests):
#   clone --neutralize   copy of rows and filestore, recipe applied to the clone only, source unchanged
#   backup + restore     round trip into a new name, checksums, filestore content equal
#   restore of a tampered backup is refused and creates nothing
#   drop                 refused without the typed name; then database gone, filestore in .trash-*, not deleted
#   failed recipe        the clone is kept and reported as NOT neutralized, source unchanged
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-db-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql python3-venv >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'

    say "push core"
    tar -C "$REPO/core/src" -czf /tmp/odp-core.tgz --exclude=__pycache__ odoo_dev_panel
    lxc file push /tmp/odp-core.tgz "$C/root/odp-core.tgz"
    in_c sh -c 'mkdir -p /opt/odp-src && tar -C /opt/odp-src -xzf /root/odp-core.tgz && chmod -R a+rX /opt/odp-src'

    say "stub installation, role, database and filestore"
    in_c bash -se <<'EOS'
set -euo pipefail
useradd -m -d /opt/odoo17 -s /bin/bash odoo17
mkdir -p /opt/odoo17/odoo/odoo /opt/odoo17/odoo/addons /etc/odoo/odoo17
printf '#!/usr/bin/env python3\n' > /opt/odoo17/odoo/odoo-bin; chmod +x /opt/odoo17/odoo/odoo-bin
printf "RELEASE_LEVELS = [ALPHA, BETA, RC, FINAL] = ['alpha', 'beta', 'candidate', 'final']\nversion_info = (17, 0, 0, FINAL, 0, '')\n" > /opt/odoo17/odoo/odoo/release.py
python3 -m venv /opt/odoo17/venv
printf '[options]\naddons_path = /opt/odoo17/odoo/addons\ndb_user = odoo17\ndb_password = secret17\ndb_host = localhost\n' > /etc/odoo/odoo17/shop.conf
chown odoo17 /etc/odoo/odoo17/shop.conf; chmod 0640 /etc/odoo/odoo17/shop.conf
mkdir -p /opt/odoo17/.local/share/Odoo/filestore/shop/ab
echo "attachment-one" > /opt/odoo17/.local/share/Odoo/filestore/shop/ab/one.bin
chown -R odoo17:odoo17 /opt/odoo17
runuser -u postgres -- psql -X -q -c "CREATE ROLE odoo17 LOGIN CREATEDB PASSWORD 'secret17'" -c "CREATE DATABASE shop OWNER odoo17"
PGPASSWORD=secret17 psql -X -q -h localhost -U odoo17 -d shop <<'SQL'
CREATE TABLE ir_module_module (name text);
INSERT INTO ir_module_module VALUES ('base'), ('web');
CREATE TABLE res_users (id serial, login text, password text);
INSERT INTO res_users (login, password) VALUES ('admin', 'real-hash'), ('client', 'client-hash');
CREATE TABLE ir_cron (id serial, name text, active boolean);
INSERT INTO ir_cron (name, active) VALUES ('mail', true), ('backup', true);
CREATE TABLE ir_mail_server (id serial, active boolean);
INSERT INTO ir_mail_server (active) VALUES (true);
SQL
mkdir -p /opt/odoo17/.config/odoo-dev-panel/recipes
printf "DO \$\$ BEGIN RAISE EXCEPTION 'recipe fails on purpose in {{database}}'; END \$\$;\n" > /opt/odoo17/.config/odoo-dev-panel/recipes/broken.sql
chown -R odoo17:odoo17 /opt/odoo17/.config
EOS

    say "checks"
    in_c runuser -u odoo17 -- env HOME=/opt/odoo17 PYTHONPATH=/opt/odp-src bash -se <<'EOS'
set -eo pipefail
fail() { echo "  FAIL $*"; exit 1; }
odp() { python3 -m odoo_dev_panel "$@"; }
sql() { PGPASSWORD=secret17 psql -X -A -t -h localhost -U odoo17 -d "$1" -c "$2"; }
FS=/opt/odoo17/.local/share/Odoo/filestore

odp db list /opt/odoo17 | grep -q "^shop .*filestore" || fail "list"

echo "-- clone --neutralize"
odp db clone /opt/odoo17 shop shop_dev --neutralize -y >/tmp/clone.out 2>&1 || { cat /tmp/clone.out; fail "clone"; }
[ "$(cat $FS/shop_dev/ab/one.bin)" = attachment-one ] || fail "filestore not copied"
[ "$(sql shop_dev 'select count(*) from res_users')" = 2 ] || fail "rows not copied"
[ "$(sql shop_dev "select count(*) from res_users where password='admin'")" = 2 ] || fail "passwords not reset in clone"
[ "$(sql shop_dev 'select count(*) from ir_cron where active')" = 0 ] || fail "crons active in clone"
[ "$(sql shop_dev 'select count(*) from ir_mail_server where active')" = 0 ] || fail "mail servers active in clone"
[ "$(sql shop "select count(*) from res_users where password='real-hash'")" = 1 ] || fail "source passwords changed"
[ "$(sql shop 'select count(*) from ir_cron where active')" = 2 ] || fail "source crons changed"
grep -q secret17 /tmp/clone.out && fail "password in output"
ls ~/.local/state/odoo-dev-panel/db/clone-shop_dev-*.json >/dev/null || fail "no receipt"
grep -q secret17 ~/.local/state/odoo-dev-panel/db/*.json && fail "password in receipt"
echo "  clone: ok"

echo "-- backup and restore"
odp db backup /opt/odoo17 shop -y >/tmp/backup.out 2>&1 || { cat /tmp/backup.out; fail "backup"; }
B=$(ls -d /opt/odoo17/odp-backups/shop-*)
for f in dump.pgdump filestore.tar manifest.json SHA256SUMS; do [ -f "$B/$f" ] || fail "missing $f"; done
odp db restore /opt/odoo17 "$B" --as shop_back -y >/tmp/restore.out 2>&1 || { cat /tmp/restore.out; fail "restore"; }
[ "$(cat $FS/shop_back/ab/one.bin)" = attachment-one ] || fail "restored filestore"
[ "$(sql shop_back 'select count(*) from res_users')" = 2 ] || fail "restored rows"
odp db restore /opt/odoo17 "$B" --as shop_back -y >/dev/null 2>&1 && fail "restore over an existing name"
echo tamper >> "$B/dump.pgdump"
odp db restore /opt/odoo17 "$B" --as shop_bad -y >/dev/null 2>&1 && fail "tampered backup restored"
[ ! -e $FS/shop_bad ] || fail "tampered restore made a filestore"
sql postgres "select 1 from pg_database where datname='shop_bad'" | grep -q 1 && fail "tampered restore made a database"
echo "  backup/restore: ok"

echo "-- failed recipe keeps the clone, flags it, leaves the source"
odp db clone /opt/odoo17 shop shop_fail --neutralize broken -y >/tmp/fail.out 2>&1 && fail "broken recipe passed"
grep -q "NOT neutralized" /tmp/fail.out || { cat /tmp/fail.out; fail "no NOT neutralized message"; }
[ -d $FS/shop_fail ] || fail "clone removed after a failed recipe"
[ "$(sql shop 'select count(*) from ir_cron where active')" = 2 ] || fail "source changed"
echo "  failed recipe: ok"

echo "-- drop"
odp db drop /opt/odoo17 shop_dev -y >/dev/null 2>&1 && fail "drop without typed name"
odp db drop /opt/odoo17 shop_dev --confirm wrong -y >/dev/null 2>&1 && fail "drop with a wrong name"
[ -d $FS/shop_dev ] || fail "filestore gone after refused drop"
odp db drop /opt/odoo17 shop_dev --confirm shop_dev >/tmp/drop.out 2>&1 || { cat /tmp/drop.out; fail "drop"; }
sql postgres "select 1 from pg_database where datname='shop_dev'" | grep -q 1 && fail "database still there"
[ ! -e $FS/shop_dev ] || fail "filestore still in place"
T=$(ls -d $FS/.trash-shop_dev-*) || fail "no trash folder"
[ "$(cat $T/ab/one.bin)" = attachment-one ] || fail "trash content"
echo "  drop: ok"
EOS

    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
    say "PASS"
done
