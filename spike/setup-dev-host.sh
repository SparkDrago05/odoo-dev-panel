#!/usr/bin/env bash
# Prepare this machine to run the spike from the source tree, without the .deb.
# Run once with sudo:  sudo spike/setup-dev-host.sh
# Creates: group odoo-dev, test user odoo99 (home /opt/odoo99), /run/odoo-dev-panel,
# /opt/odoo-dev-panel/{python,dev}. Log out and back in afterwards so the new group applies.
set -euo pipefail

DEV_USER=${SUDO_USER:?run this script with sudo}
GROUP=odoo-dev
TEST_USER=odoo99

getent group "$GROUP" >/dev/null || groupadd "$GROUP"
usermod -aG "$GROUP" "$DEV_USER"

if ! id "$TEST_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/opt/$TEST_USER" --shell /bin/bash "$TEST_USER"
fi
usermod -aG "$GROUP" "$TEST_USER"

install -d -m 2770 -o root -g "$GROUP" /run/odoo-dev-panel
install -d -m 2775 -o root -g "$GROUP" /opt/odoo-dev-panel /opt/odoo-dev-panel/python /opt/odoo-dev-panel/dev
# Recreate the socket directory on every boot.
install -m 0644 "$(dirname "$0")/../packaging/tmpfiles.d/odoo-dev-panel.conf" /etc/tmpfiles.d/odoo-dev-panel.conf

echo "Done. Log out and back in (or run 'newgrp $GROUP'), then run spike/dev-sync.sh."
