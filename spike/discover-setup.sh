#!/usr/bin/env bash
# Runs INSIDE the test container as root (see discover-test.sh). Never run this on a real machine:
# it creates users, PostgreSQL roles and files under /opt and /etc/odoo.
set -euo pipefail
[ -f /run/lxc/lxc-container ] || [ "$(systemd-detect-virt --container 2>/dev/null || true)" != "none" ] || { echo "not in a container" >&2; exit 1; }

useradd -m -s /bin/bash dev
useradd -m -d /opt/odoo17 -s /bin/bash odoo17
usermod -aG odoo17 dev   # like the owner: the dev user reads /opt/odooNN through the group
chmod 2770 /opt/odoo17

stub() {  # stub <clone dir> <major>
    mkdir -p "$1/odoo" "$1/addons"
    printf '#!/usr/bin/env python3\nimport time\ntime.sleep(600)\n' > "$1/odoo-bin"
    chmod +x "$1/odoo-bin"
    printf "RELEASE_LEVELS = [ALPHA, BETA, RC, FINAL] = ['alpha', 'beta', 'candidate', 'final']\nversion_info = (%s, 0, 0, FINAL, 0, '')\n" "$2" > "$1/odoo/release.py"
}

# nested layout: /opt/odooNN
stub /opt/odoo17/odoo 17
python3 -m venv /opt/odoo17/venv
mkdir -p /etc/odoo/odoo17 /etc/odoo/odoo13
printf '[options]\naddons_path = /opt/odoo17/odoo/addons\ndb_user = odoo17\ndb_password = secret17\ndb_host = localhost\n' > /etc/odoo/odoo17/shop.conf
printf '[options]\naddons_path = /opt/odoo13/addons\n' > /etc/odoo/odoo13/old.conf
mkdir -p /opt/odoo17/.local/share/Odoo/filestore/shop
chown -R odoo17:odoo17 /opt/odoo17
runuser -u postgres -- psql -X -q -c "CREATE ROLE odoo17 LOGIN PASSWORD 'secret17'" -c "CREATE DATABASE shop OWNER odoo17" -c "CREATE DATABASE blog OWNER odoo17"

# plain layout
stub /home/dev/src/odoo 18
python3 -m venv /home/dev/src/odoo/.venv
printf '[options]\naddons_path = /home/dev/src/odoo/addons\nhttp_port = 8070\n' > /home/dev/src/odoo/odoo.conf
chown -R dev:dev /home/dev/src

# a tree whose venv interpreter vanished
stub /home/dev/old/odoo 15
python3 -m venv /home/dev/old/venv
rm /home/dev/old/venv/bin/python
chown -R dev:dev /home/dev/old

# a running process as odoo17, started the way the owner does it by hand
runuser -u odoo17 -- bash -c 'cd /opt/odoo17 && nohup /opt/odoo17/venv/bin/python ./odoo/odoo-bin -c /etc/odoo/odoo17/shop.conf -d shop --dev=xml >/dev/null 2>&1 &'
sleep 1

# helper for the adopt check
cat > /root/discover-adopt.sh <<'ADOPT'
set -e
export PYTHONPATH=/opt/odp-src ODP_REGISTRY=/tmp/reg2.json
snapshot() { find /opt/odoo17 /home/dev/src /etc/odoo -type f -not -path "*/.local/*" -printf "%p %T@ %s\n" | sort | md5sum; }
before=$(snapshot)
python3 -m odoo_dev_panel adopt /opt/odoo17 --name shop17
python3 -m odoo_dev_panel adopt /home/dev/src/odoo
python3 -m odoo_dev_panel adopt /home/dev/nope && { echo "  FAIL adopted a non-installation"; exit 1; } || true
[ "$before" = "$(snapshot)" ] || { echo "  FAIL files changed"; exit 1; }
grep -q shop17 /tmp/reg2.json
! grep -q secret17 /tmp/reg2.json
python3 -m odoo_dev_panel --json discover --no-databases --root /opt --root /home/dev | python3 -c '
import json, sys
s = json.load(sys.stdin)
names = [i["name"] for i in s["installations"] if i["adopted"]]
assert sorted(names) == ["odoo", "shop17"], names'
echo "  adopt: ok"
ADOPT
chmod a+r /root/discover-adopt.sh /root/discover-check.py
chmod a+x /root
