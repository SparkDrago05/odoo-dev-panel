#!/usr/bin/env bash
# Two more layouts unlike /opt/odooNN, in fresh LXD containers, against a real PostgreSQL. No .deb needed.
#
#   script -qec "spike/layouts-test.sh [release ...]" /dev/null > spike/.out/layouts.log
#
# Two layouts that share nothing with /opt/odooNN, built from stub Odoo trees (no network, no real Odoo):
#   home    the developer's own clone ~/src/odoo-17.0 with .venv inside, config ~/.odoorc without db_user,
#                db_host or password: Odoo connects as the OS user over the Unix socket (peer auth).
#   system  the layout of Odoo's own .deb: system user `odoo` (not odooNN, home /var/lib/odoo, 0750),
#                source /srv/odoo/18.0 with venv/, config /etc/odoo/odoo.conf with db_user = odoo,
#                db_host = False, db_password = False (peer auth), data_dir = /var/lib/odoo.
# For each: discover, db list, clone with filestore, backup, drop, start and stop the instance through the
# run-as user's agent. The agents are started by root with the same systemd-run command the app uses.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-lay-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql python3-venv >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready || sleep 5; pg_isready'

    say "push core"
    tar -C "$REPO/core/src" -czf /tmp/odp-core.tgz --exclude=__pycache__ odoo_dev_panel
    lxc file push /tmp/odp-core.tgz "$C/root/odp-core.tgz"
    in_c sh -c 'mkdir -p /opt/odp-src && tar -C /opt/odp-src -xzf /root/odp-core.tgz && chmod -R a+rX /opt/odp-src'

    say "build both layouts"
    in_c bash -se <<'EOS'
set -euo pipefail
# odp for every user (the agent is started by absolute path, like /usr/lib/odoo-dev-panel/bin/odp)
printf '#!/bin/sh\nPYTHONPATH=/opt/odp-src exec python3 -m odoo_dev_panel "$@"\n' > /usr/local/bin/odp
chmod 0755 /usr/local/bin/odp
groupadd --system odoo-dev
install -d -m 2770 -o root -g odoo-dev /run/odoo-dev-panel

stub() {  # stub <clone dir> <major>: odoo-bin that serves --http-port until stopped
    mkdir -p "$1/odoo" "$1/addons"
    cat > "$1/odoo-bin" <<'PY'
#!/usr/bin/env python3
import socket, sys, time
port = int(sys.argv[sys.argv.index("--http-port") + 1]) if "--http-port" in sys.argv else None
if port:
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", port)); s.listen()
print("stub odoo", sys.argv[1:], flush=True)
time.sleep(3600)
PY
    chmod +x "$1/odoo-bin"
    printf "RELEASE_LEVELS = [ALPHA, BETA, RC, FINAL] = ['alpha', 'beta', 'candidate', 'final']\nversion_info = (%s, 0, 0, FINAL, 0, '')\n" "$2" > "$1/odoo/release.py"
}
odoo_tables() {  # odoo_tables <psql connection args...>: the tables the tool and the default recipe touch
    psql -X -q "$@" <<'SQL'
CREATE TABLE ir_module_module (name text);
INSERT INTO ir_module_module VALUES ('base'), ('web');
CREATE TABLE res_users (id serial, login text, password text);
INSERT INTO res_users (login, password) VALUES ('admin', 'real-hash');
CREATE TABLE ir_cron (id serial, name text, active boolean);
INSERT INTO ir_cron (name, active) VALUES ('mail', true);
CREATE TABLE ir_mail_server (id serial, active boolean);
INSERT INTO ir_mail_server (active) VALUES (true);
SQL
}

# home: a regular account, owner of its clone, role named after itself (createuser -s $USER style)
useradd -m -s /bin/bash -G odoo-dev dev
stub /home/dev/src/odoo-17.0 17
python3 -m venv /home/dev/src/odoo-17.0/.venv
printf '[options]\naddons_path = /home/dev/src/odoo-17.0/addons\nhttp_port = 8069\n' > /home/dev/.odoorc
mkdir -p /home/dev/.local/share/Odoo/filestore/blog/aa
echo "home-attachment" > /home/dev/.local/share/Odoo/filestore/blog/aa/one.bin
chown -R dev:dev /home/dev
runuser -u postgres -- createuser --createdb dev
runuser -u dev -- createdb blog
runuser -u dev -- bash -c "$(declare -f odoo_tables); odoo_tables -d blog"

# system: Odoo's .deb layout
useradd --system --home-dir /var/lib/odoo --create-home --shell /usr/sbin/nologin -G odoo-dev odoo
chmod 0750 /var/lib/odoo
stub /srv/odoo/18.0 18
python3 -m venv /srv/odoo/18.0/venv
chown -R odoo:odoo /srv/odoo
mkdir -p /etc/odoo
printf '[options]\naddons_path = /srv/odoo/18.0/addons\ndata_dir = /var/lib/odoo\ndb_host = False\ndb_port = False\ndb_user = odoo\ndb_password = False\nhttp_port = 8070\n' > /etc/odoo/odoo.conf
chmod 0644 /etc/odoo/odoo.conf
mkdir -p /var/lib/odoo/filestore/shop/bb
echo "system-attachment" > /var/lib/odoo/filestore/shop/bb/one.bin
chown -R odoo:odoo /var/lib/odoo
runuser -u postgres -- createuser --createdb odoo
runuser -u odoo -- createdb shop
runuser -u odoo -- bash -c "$(declare -f odoo_tables); odoo_tables -d shop"

# agents, started as the app starts them (sudo -A -- systemd-run ..., here already root)
for u in dev odoo; do
    eval "$(ODP_EXE=/usr/local/bin/odp PYTHONPATH=/opt/odp-src python3 -c "
import shlex; from odoo_dev_panel import privilege; print(shlex.join(privilege.systemd_run_args('$u')))")"
done
for i in $(seq 20); do [ -S /run/odoo-dev-panel/dev.sock ] && [ -S /run/odoo-dev-panel/odoo.sock ] && break; sleep 0.5; done
ls -l /run/odoo-dev-panel
EOS

    say "checks as the developer"
    in_c runuser -u dev -- env HOME=/home/dev ODP_EXE=/usr/local/bin/odp bash -se <<'EOS'
set -eo pipefail
fail() { echo "  FAIL $*"; exit 1; }
odp() { /usr/local/bin/odp "$@"; }
HOME_ROOT=/home/dev/src/odoo-17.0
SYS_ROOT=/srv/odoo/18.0

echo "-- discover"
odp --json discover > /tmp/d.json
python3 - "$HOME_ROOT" "$SYS_ROOT" <<'PY' || fail "discover"
import json, sys
home, system = sys.argv[1:]
s = json.load(open("/tmp/d.json"))
inst = {i["root"]: i for i in s["installations"]}
assert home in inst and inst[home]["owner"] == "dev" and inst[home]["version"] == "17.0", inst.get(home)
assert system in inst and inst[system]["owner"] == "odoo" and inst[system]["version"] == "18.0", inst.get(system)
assert inst[home]["venv_ok"] and inst[system]["venv_ok"], "venvs"
by_path = {i["path"]: i for i in s["instances"]}
assert by_path["/home/dev/.odoorc"]["installation"] == home, by_path.get("/home/dev/.odoorc")
assert by_path["/etc/odoo/odoo.conf"]["installation"] == system, by_path.get("/etc/odoo/odoo.conf")
dbs = {d["installation"]: d for d in s["databases"]}
print("  home dbs:", dbs[home]["databases"] and [d["name"] for d in dbs[home]["databases"]], dbs[home]["error"])
print("  system dbs:", dbs[system]["databases"] and [d["name"] for d in dbs[system]["databases"]], dbs[system]["error"])
assert [d["name"] for d in dbs[home]["databases"]] == ["blog"], dbs[home]
assert dbs[home]["databases"][0]["filestore"] == "/home/dev/.local/share/Odoo/filestore/blog"
# Discovery runs as the dev user: peer authentication lets only `odoo` in. `odp db` asks the agent (below).
assert "peer authentication" in (dbs[system]["error"] or ""), dbs[system]
PY
echo "  discover: ok"

odp agent status | grep -q "dev.*running" || fail "agent dev"
odp agent status | grep -q "odoo.*running" || fail "agent odoo"

for layout in home system; do
    if [ $layout = home ]; then ROOT=$HOME_ROOT DB=blog FS=/home/dev/.local/share/Odoo/filestore CONF=/home/dev/.odoorc RUN_AS=dev
    else ROOT=$SYS_ROOT DB=shop FS=/var/lib/odoo/filestore CONF=/etc/odoo/odoo.conf RUN_AS=odoo; fi
    echo "-- $layout: db list, clone, backup, drop"
    odp db list $ROOT | grep -q "^$DB .* filestore  $FS/$DB" || { odp db list $ROOT; fail "$layout list"; }
    odp db clone $ROOT $DB ${DB}_dev --neutralize -y >/tmp/clone.out 2>&1 || { cat /tmp/clone.out; fail "$layout clone"; }
    odp db list $ROOT | grep -q "^${DB}_dev " || fail "$layout clone not listed"
    odp db backup $ROOT ${DB}_dev -y >/tmp/backup.out 2>&1 || { cat /tmp/backup.out; fail "$layout backup"; }
    odp db drop $ROOT ${DB}_dev --confirm ${DB}_dev >/tmp/drop.out 2>&1 || { cat /tmp/drop.out; fail "$layout drop"; }
    odp db list $ROOT | grep -q "^${DB}_dev " && fail "$layout drop left the database"
    echo "  $layout db: ok"

    echo "-- $layout: start and stop"
    odp --json start $CONF -d $DB > /tmp/start.json || fail "$layout start"
    id=$(python3 -c 'import json; print(json.load(open("/tmp/start.json"))["id"])')
    port=$(python3 -c 'import json; print(json.load(open("/tmp/start.json"))["meta"]["port"])')
    for i in $(seq 20); do python3 -c "import socket; socket.create_connection(('127.0.0.1', $port), 1)" 2>/dev/null && break; sleep 0.5; done
    python3 -c "import socket; socket.create_connection(('127.0.0.1', $port), 1)" || fail "$layout not listening on $port"
    odp stop -u $RUN_AS $id >/dev/null || fail "$layout stop"
    echo "  $layout run: ok (port $port)"
done
EOS

    say "filestore results (as root)"
    in_c bash -se <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
ls -d /home/dev/.local/share/Odoo/filestore/.trash-blog_dev-* >/dev/null || fail "home trash"
[ "$(cat /home/dev/.local/share/Odoo/filestore/.trash-blog_dev-*/aa/one.bin)" = home-attachment ] || fail "home filestore copy"
ls -d /var/lib/odoo/filestore/.trash-shop_dev-* >/dev/null || fail "system trash"
[ "$(stat -c %U /var/lib/odoo/filestore/.trash-shop_dev-*)" = odoo ] || fail "system filestore not owned by odoo"
[ "$(cat /var/lib/odoo/filestore/.trash-shop_dev-*/bb/one.bin)" = system-attachment ] || fail "system filestore copy"
ls -d /var/lib/odoo/odp-backups/shop_dev-* >/dev/null || fail "system backup folder"
echo "  filestores: ok"
EOS

    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
    say "PASS"
done
