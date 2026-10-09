#!/usr/bin/env bash
# Phase 10 (Y1-Y9) end to end: provision Odoo 17 in fresh LXD containers, then the Python environment: show,
# install typed and missing packages through the run-as agent, import validation, export, disk use, and the dev
# tools (debugpy into the venv; rtlcss and the pinned wkhtmltopdf build through sudo). Needs the built .deb and
# network (Odoo clone, uv Python, pip, apt, npm, the wkhtmltopdf release).
#
#   script -qec "spike/python-test.sh [path/to.deb] [release ...]" /dev/null > spike/.out/python.log
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
    C="odp-py-${REL//./}"
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

    say "provision Odoo $V; a custom repository whose requirements ask for a missing package"
    as_dev_v <<'EOS'
set -e
odp provision run -V $V -y >/dev/null
mkdir -p /opt/odoo$V/custom/dev && cd /opt/odoo$V/custom/dev && git init -q
printf 'openupgradelib\nphonenumbers>=8.13\n' > requirements.txt
EOS

    say "show, install typed and missing packages, validate, export, disk"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
R=/opt/odoo$V
odp python show $R --json > /tmp/show.json || true
python3 - <<'PY'
import json
d = json.load(open("/tmp/show.json"))
missing = sorted(r["name"] for r in d["requirements"] if r["status"] == "missing")
assert "openupgradelib" in missing, missing
assert d["interpreter"]["uv_managed"] and d["interpreter"]["matches_pin"], d["interpreter"]
print("show ok: missing", missing)
PY
odp python install $R "https://example.com/x.tar.gz" --plan && fail "URL accepted"
odp python install $R "phonenumbers>=8.13" --yes
odp python install $R --missing --yes
odp python show $R --json > /tmp/show2.json || true
python3 -c "import json; d=json.load(open('/tmp/show2.json')); m=[r['name'] for r in d['requirements'] if r['status']=='missing']; assert not m, m; print('install ok: nothing missing')"
odp python validate $R --yes || fail "validate"
echo "validate ok"
odp python export $R | grep -q '^openupgradelib==' || fail "export"
odp python disk $R --json | python3 -c "import json,sys; d=json.load(sys.stdin); assert any(p['label']=='venv' and p['bytes']>0 for p in d['parts']), d; print('disk ok')"
EOS

    say "dev tools: debugpy (venv), rtlcss and wkhtmltopdf (sudo)"
    as_dev_v <<'EOS'
set -e
fail() { echo "  FAIL $*"; exit 1; }
R=/opt/odoo$V
odp python tool debugpy --root $R --yes
odp python tool rtlcss --yes
odp python tool wkhtmltopdf --plan | grep -q "sha256sum -c" || fail "wkhtmltopdf plan lacks the hash check"
odp python tool wkhtmltopdf --yes
odp python tools $R --json > /tmp/tools.json
python3 - <<'PY'
import json
t = {r["tool"]: r for r in json.load(open("/tmp/tools.json"))}
assert t["debugpy"]["installed"], t["debugpy"]
assert t["rtlcss"]["installed"], t["rtlcss"]
assert t["wkhtmltopdf"]["installed"] and t["wkhtmltopdf"]["patched"], t["wkhtmltopdf"]
print("tools ok:", t["wkhtmltopdf"]["version"], "|", t["rtlcss"]["version"], "| debugpy", t["debugpy"]["version"])
PY
echo "ALL PYTHON CHECKS PASSED"
EOS
    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
done
