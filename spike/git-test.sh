#!/usr/bin/env bash
# W1-W12: the Git workspace in fresh LXD containers. No .deb, no network, no real Odoo: stub trees and local bare
# remotes only.
#
#   script -qec "spike/git-test.sh [release ...]" /dev/null > spike/.out/git.log
#
# Layout /opt/odoo19 (owner odoo19, group odoo19, 2775 like a provisioned tree):
#   odoo/                     community stub, cloned by odoo19        -> foreign for dev: read-only
#   custom/cms/admissions     cloned by dev, then dirtied             -> pull skipped (uncommitted changes)
#   custom/hr/payroll         cloned by dev, upstream moves ahead     -> pull fast-forwards, changed module reported
#   custom/hr/attendance      cloned by dev, diverged                 -> pull skipped (diverged)
# Plus /home/dev/work/client_x outside the root, on the instance's addons_path.
# Then: list (JSON), pull --plan, pull, add --url into a nested folder, refusals (../, password URL), forget.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-git-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    in_c sh -c 'command -v git >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null)'

    say "push core"
    tar -C "$REPO/core/src" -czf /tmp/odp-core.tgz --exclude=__pycache__ odoo_dev_panel
    lxc file push /tmp/odp-core.tgz "$C/root/odp-core.tgz"
    in_c sh -c 'mkdir -p /opt/odp-src && tar -C /opt/odp-src -xzf /root/odp-core.tgz && chmod -R a+rX /opt/odp-src'

    say "build the layout"
    in_c bash -se <<'EOS'
set -euo pipefail
printf '#!/bin/sh\nPYTHONPATH=/opt/odp-src exec python3 -m odoo_dev_panel "$@"\n' > /usr/local/bin/odp
chmod 0755 /usr/local/bin/odp
useradd -m -s /bin/bash dev
useradd --system -m -d /opt/odoo19 -s /usr/sbin/nologin odoo19
usermod -aG odoo19 dev
chmod 2775 /opt/odoo19
G='git -c user.name=t -c user.email=t@example.com'

as() { runuser -u "$1" -- bash -c "$2"; }
seed() {  # seed <name> <branch> <file>...: a bare remote under /srv/remotes
    local d=/srv/seed/$1; mkdir -p "$d"; cd "$d"; git init -q -b "$2"
    shift 2; for f in "$@"; do mkdir -p "$(dirname "$f")"; echo x > "$f"; done
    $G add -A; $G commit -qm init; git clone -q --bare "$d" "/srv/remotes/$(basename "$d").git"; cd /
}
mkdir -p /srv/remotes
seed odoo 19.0 odoo-bin odoo/release.py
printf "version_info = (19, 0, 0, 'final', 0, '')\n" > /srv/seed/odoo/odoo/release.py
(cd /srv/seed/odoo && $G commit -qam release && git push -q /srv/remotes/odoo.git 19.0)
seed admissions staging-19 adm_core/__manifest__.py
seed payroll staging-19 hr_pay/__manifest__.py
seed attendance main hr_att/__manifest__.py
seed client_x main x_mod/__manifest__.py
chmod -R a+rwX /srv/remotes
# Fixture only: the bare remotes are root's; let every user clone them. /opt/odoo19 stays untrusted for dev.
for r in /srv/remotes/*.git; do git config --system --add safe.directory "$r"; done  # '/*' patterns need git 2.46

as odoo19 'git clone -q file:///srv/remotes/odoo.git /opt/odoo19/odoo'
mkdir -p /opt/odoo19/custom && chown odoo19:odoo19 /opt/odoo19/custom && chmod 2775 /opt/odoo19/custom
as dev 'mkdir -p /opt/odoo19/custom/cms /opt/odoo19/custom/hr
  git clone -q file:///srv/remotes/admissions.git /opt/odoo19/custom/cms/admissions
  git clone -q file:///srv/remotes/payroll.git /opt/odoo19/custom/hr/payroll
  git clone -q file:///srv/remotes/attendance.git /opt/odoo19/custom/hr/attendance
  git clone -q file:///srv/remotes/client_x.git /home/dev/work/client_x
  echo dirty >> /opt/odoo19/custom/cms/admissions/adm_core/__manifest__.py
  cd /opt/odoo19/custom/hr/attendance && echo l > local.txt && git add -A && git -c user.name=d -c user.email=d@e commit -qm local'
# upstream moves: payroll gets a module change, attendance diverges
(cd /srv/seed/payroll && echo y > hr_pay/models.py && $G add -A && $G commit -qm upd && git push -q /srv/remotes/payroll.git staging-19)
(cd /srv/seed/attendance && echo y > hr_att/up.py && $G add -A && $G commit -qm upd && git push -q /srv/remotes/attendance.git main)
mkdir -p /etc/odoo/odoo19
printf '[options]\naddons_path = /opt/odoo19/odoo/addons,/opt/odoo19/custom/cms,/opt/odoo19/custom/hr,/home/dev/work/client_x\n' > /etc/odoo/odoo19/main.conf
chmod 0644 /etc/odoo/odoo19/main.conf
EOS

    say "list (JSON) as dev"
    in_c runuser -u dev -- bash -se <<'EOS'
set -euo pipefail
export HOME=/home/dev
fail() { echo "FAIL: $*"; exit 1; }
odp repo list --installation /opt/odoo19 --json > /tmp/list.json
python3 - <<'PY'
import json
rows = {r["path"]: r for r in json.load(open("/tmp/list.json"))["repos"]}
def st(p): return rows[p]["state"]
assert set(rows) >= {"/opt/odoo19/odoo", "/opt/odoo19/custom/cms/admissions", "/opt/odoo19/custom/hr/payroll",
                     "/opt/odoo19/custom/hr/attendance", "/home/dev/work/client_x"}, sorted(rows)
assert rows["/opt/odoo19/odoo"]["purpose"] == "community"
assert st("/opt/odoo19/odoo")["foreign"] and st("/opt/odoo19/odoo")["ok"], st("/opt/odoo19/odoo")
assert st("/opt/odoo19/custom/cms/admissions")["dirty"]
assert rows["/home/dev/work/client_x"]["installations"][0]["relative"] is None
print("list ok:", len(rows), "repositories")
PY
odp repo list
grep -q "safe.directory" ~/.gitconfig 2>/dev/null && fail "safe.directory was written to the git config"

echo "== fetch, then pull --plan"
odp repo fetch --bulk --installation /opt/odoo19 --yes
odp repo pull /opt/odoo19/custom/cms/admissions /opt/odoo19/custom/hr/payroll /opt/odoo19/custom/hr/attendance /opt/odoo19/odoo --plan --json > /tmp/plan.json
python3 - <<'PY'
import json
skip = {i["repo"]: i["skip"] for i in json.load(open("/tmp/plan.json"))["items"]}
assert "uncommitted" in skip["/opt/odoo19/custom/cms/admissions"], skip
assert "diverged" in skip["/opt/odoo19/custom/hr/attendance"], skip
assert "owned by odoo19" in skip["/opt/odoo19/odoo"], skip
assert skip["/opt/odoo19/custom/hr/payroll"] is None, skip
print("plan ok")
PY
echo "== pull"
odp repo pull /opt/odoo19/custom/cms/admissions /opt/odoo19/custom/hr/payroll --yes --json > /tmp/pull.json
python3 - <<'PY'
import json
res = {r["repo"]: r for r in json.load(open("/tmp/pull.json"))["results"]}
assert res["/opt/odoo19/custom/hr/payroll"]["status"] == "ok" and res["/opt/odoo19/custom/hr/payroll"]["changed_modules"] == ["hr_pay"], res
assert res["/opt/odoo19/custom/cms/admissions"]["status"] == "skipped"
print("pull ok")
PY
grep -q dirty /opt/odoo19/custom/cms/admissions/adm_core/__manifest__.py || fail "local change lost"

echo "== add --url into a nested folder"
odp repo add /opt/odoo19 --url file:///srv/remotes/client_x.git --dest custom/extensions/client_custom --group ext --yes
test -d /opt/odoo19/custom/extensions/client_custom/.git || fail "clone missing"
python3 -c "import json; d=json.load(open('/home/dev/.local/state/odoo-dev-panel/repositories.json')); a=d['assoc'][0]; assert a['destination']=='custom/extensions/client_custom' and a['group']=='ext', a"
echo "== refusals"
odp repo add /opt/odoo19 --url file:///srv/remotes/client_x.git --dest ../escape --plan && fail "../ accepted"
odp repo add /opt/odoo19 --url https://u:secret@example.com/x.git --dest custom/x --plan > /tmp/pw.txt && fail "password URL accepted"
grep -q secret /tmp/pw.txt && fail "password printed"
odp repo switch /opt/odoo19/odoo 18.0 --plan && fail "switch on a foreign repo accepted"
echo "== forget keeps files"
odp repo forget /opt/odoo19/custom/extensions/client_custom
test -d /opt/odoo19/custom/extensions/client_custom/.git || fail "forget removed files"
echo "ALL GIT CHECKS PASSED"
EOS
    [ "$KEEP" = 1 ] || lxc delete -f "$C" >/dev/null
done
