#!/usr/bin/env bash
# Discovery acceptance (D12) in fresh LXD containers. No .deb needed: the core is stdlib Python run from a tarball.
#
#   script -qec "spike/discover-test.sh [release ...]" /dev/null > spike/.out/discover.log
#
# Builds layouts from stub Odoo trees (no network, no real Odoo):
#   nested:  /opt/odoo17 (clone in odoo/, venv/), config in /etc/odoo/odoo17, user odoo17, PG role + databases, a running process
#   plain:   /home/dev/src/odoo (clone, .venv inside), odoo.conf next to it, no /etc/odoo
#   broken:  /home/dev/old with a venv whose interpreter vanished
# plus an orphan config for a version that is not installed.
# Then checks `odp discover --json` against that ground truth and that `odp adopt` changes no Odoo file.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
if [ $# -gt 0 ]; then RELEASES=("$@"); else RELEASES=(24.04 26.04); fi
KEEP=${KEEP:-0}
HERE=$(cd "$(dirname "$0")" && pwd)

say() { printf '\n\033[1m[%s] %s\033[0m\n' "$REL" "$*"; }
in_c() { lxc exec "$C" -- "$@"; }

for REL in "${RELEASES[@]}"; do
    C="odp-disc-${REL//./}"
    lxc delete -f "$C" >/dev/null 2>&1 || true
    say "launch fresh ubuntu:$REL"
    lxc launch "ubuntu:$REL" "$C" >/dev/null
    in_c cloud-init status --wait >/dev/null || true
    in_c sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql python3-venv >/dev/null'
    in_c sh -c 'systemctl enable --now postgresql >/dev/null 2>&1; pg_isready -h localhost || sleep 5; pg_isready -h localhost'

    say "push core and test files"
    tar -C "$REPO/core/src" -czf /tmp/odp-core.tgz --exclude=__pycache__ odoo_dev_panel
    lxc file push /tmp/odp-core.tgz "$C/root/odp-core.tgz"
    lxc file push "$HERE/discover-setup.sh" "$HERE/discover-check.py" "$C/root/"
    in_c sh -c 'mkdir -p /opt/odp-src && tar -C /opt/odp-src -xzf /root/odp-core.tgz && chmod -R a+rX /opt/odp-src'

    say "build ground truth"
    in_c bash /root/discover-setup.sh

    say "odp discover"
    in_c runuser -u dev -- env PYTHONPATH=/opt/odp-src python3 -m odoo_dev_panel discover --root /opt --root /home/dev
    in_c runuser -u dev -- bash -c 'PYTHONPATH=/opt/odp-src ODP_REGISTRY=/tmp/reg.json python3 -m odoo_dev_panel --json discover --root /opt --root /home/dev > /tmp/discover.json'

    say "checks"
    in_c python3 /root/discover-check.py

    say "adopt changes no Odoo file"
    in_c runuser -u dev -- bash /root/discover-adopt.sh

    if [ "$KEEP" = 1 ]; then echo "  kept $C"; else lxc delete -f "$C" >/dev/null; fi
    say "PASS"
done
