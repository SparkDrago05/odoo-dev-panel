#!/usr/bin/env bash
# Phase 8 (T1-T6) end to end: provision from a profile with nested repositories, then export, apply a bundle to the
# existing installation and update addons_path with confirmation. Fresh LXD containers, the built .deb.
#
#   script -qec "spike/profile-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/profile.log
#
# Needs network: git clone of odoo/odoo and OCA repositories, uv Python download, pip. V=17 by default.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEB=${1:-$(ls "$REPO"/app/src-tauri/target/release/bundle/deb/*.deb 2>/dev/null | head -1)}
shift || true
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}
V=${V:-17}

[ -f "$DEB" ] || { echo "no .deb found; build one first (packaging/build-runtime.sh, then pnpm tauri build in app/)" >&2; exit 1; }

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }
as_dev_v() { { echo "export V=$V"; cat; } | lxc exec "$C" -- sudo -iu dev bash -ls; }

for REL in "${RELEASES[@]}"; do
    C="odp-prof-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true

    say "install .deb and PostgreSQL; user dev (sudo, test-only NOPASSWD)"
    lxc file push "$DEB" "$C/root/odp.deb"
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /root/odp.deb postgresql >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'
    in_c sh -c 'useradd -m -s /bin/bash -G sudo,odoo-dev dev
        echo "dev ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-dev && chmod 0440 /etc/sudoers.d/90-dev'

    say "profiles: org overlay + a profile with nested repositories and {series} placeholders"
    as_dev_v <<'EOS'
set -e
mkdir -p ~/.config/odoo-dev-panel/profiles
cat > ~/.config/odoo-dev-panel/org.toml <<'T'
name = "Test org"
[[repos]]
name = "server-ux"
url = "https://github.com/OCA/server-ux.git"
branch = "{series}"
destination = "custom/oca/tools/server-ux"
group = "oca"
T
cat > ~/.config/odoo-dev-panel/profiles/dev.toml <<'T'
name = "Dev setup"
[config]
workers = 0
without_demo = "all"
[["repos+"]]
name = "web"
url = "https://github.com/OCA/web.git"
branch = "{series}"
destination = "custom/oca/web"
[["repos+"]]
name = "nope"
url = "https://github.com/OCA/server-tools.git"
branch = "no-such-branch-{version}"
destination = "custom/bad"
T
odp profile list
echo "== plan refuses a missing branch (remote check)"
if odp --json provision plan -V $V --profile dev > /tmp/plan.json; then echo "FAIL: plan accepted a missing branch"; exit 1; fi
python3 - <<'PY'
import json
d = json.load(open("/tmp/plan.json"))
st = {c["id"]: c["status"] for c in d["preflight"]}
assert st["remote:nope"] == "fail" and st["remote:web"] == "ok" and st["remote:server-ux"] == "ok", st
assert d["profile"]["origin"]["repos.0"] == "org", d["profile"]["origin"]
assert any(t["path"] == "custom/oca/tools/server-ux" for t in d["tree"]), d["tree"]
print("plan ok: missing branch caught, nested tree, layer origins")
PY
# drop the bad repository from the profile
python3 - <<'PY'
import pathlib, re
p = pathlib.Path.home() / ".config/odoo-dev-panel/profiles/dev.toml"
p.write_text(p.read_text().split('[["repos+"]]\nname = "nope"')[0])
PY
EOS

    say "odp provision run -V $V --profile dev"
    as_dev_v <<'EOS'
set -e
odp provision run -V $V --profile dev -y
EOS

    say "checks"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
conf=/etc/odoo/odoo$V/default.conf
[ -d /opt/odoo$V/custom/oca/tools/server-ux/.git ] || fail "nested org repo not cloned"
[ -d /opt/odoo$V/custom/oca/web/.git ] || fail "profile repo not cloned"
flat=$(tr -d '\n\t' < $conf)
echo "$flat" | grep -q "custom/oca/tools/server-ux" || fail "addons_path lacks server-ux"
echo "$flat" | grep -q "custom/oca/web" || fail "addons_path lacks web"
grep -q "^workers = 0" $conf || fail "config option from the profile"
grep -q "^without_demo = all" $conf || fail "without_demo from the profile"
python3 - "$V" <<'PY' || fail "repositories.json"
import json, os, sys
v = sys.argv[1]
d = json.load(open(os.path.expanduser("~/.local/state/odoo-dev-panel/repositories.json")))
a = {x["destination"]: x for x in d["assoc"]}
assert a["custom/oca/tools/server-ux"]["group"] == "oca" and a["custom/oca/tools/server-ux"]["preferred_branch"] == f"{v}.0", a
assert a["odoo"]["purpose"] == "community", a
PY
odp repo list --installation /opt/odoo$V
/opt/odoo$V/venv/bin/python /opt/odoo$V/odoo/odoo-bin --version

echo "== resume: a second run reports the previous receipt and reuses everything"
odp --json provision plan -V $V --profile dev --no-remote | python3 -c "import json,sys; d=json.load(sys.stdin); p=d['previous']; assert p and p['status']=='complete', p; print('previous:', p['status'], p['completed'])"
before=$(md5sum < $conf)
odp provision run -V $V --profile dev -y | grep -E "kept|FAILED|ready"
[ "$(md5sum < $conf)" = "$before" ] || fail "config changed by second run"

echo "== export is sanitized and round-trips"
odp profile export /opt/odoo$V --name exported
t=~/.config/odoo-dev-panel/profiles/exported.toml
cat $t
grep -q 'destination = "custom/oca/tools/server-ux"' $t || fail "export lacks nested repo"
grep -qiE "pass|db_|admin" $t && fail "export leaked a secret or db setting"
odp profile show exported -V $V >/dev/null || fail "exported profile does not resolve"

echo "== apply a bundle to the existing installation, then update addons_path with confirmation"
cat > ~/.config/odoo-dev-panel/profiles/extra.toml <<'T'
[[repos]]
name = "server-ux"
url = "https://github.com/OCA/server-ux.git"
destination = "custom/oca/tools/server-ux"
[[repos]]
name = "reporting"
url = "https://github.com/OCA/reporting-engine.git"
branch = "{series}"
destination = "custom/oca/reporting"
group = "oca"
T
odp repo apply /opt/odoo$V --profile extra --yes --json > /tmp/apply.json
python3 - <<'PY'
import json
r = {x["repo"].split("/")[-1]: x["status"] for x in json.load(open("/tmp/apply.json"))["results"]}
assert r == {"server-ux": "kept", "reporting": "ok"}, r
print("apply ok:", r)
PY
odp repo addons /opt/odoo$V /opt/odoo$V/custom/oca/reporting
odp repo addons /opt/odoo$V /opt/odoo$V/custom/oca/reporting --apply $conf --yes
tr -d '\n\t' < $conf | grep -q "custom/oca/reporting" || fail "addons_path not updated"
ls $conf.bak-* >/dev/null 2>&1 || ls /etc/odoo/odoo$V/*.bak-* >/dev/null || fail "no config backup"
[ "$(stat -c '%U:%G %a' $conf)" = "dev:odoo$V 640" ] || fail "config owner/mode changed"
echo "ALL PROFILE CHECKS PASSED"
EOS
    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
done
