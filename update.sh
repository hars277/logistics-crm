#!/usr/bin/env bash
# One-command live update: pull latest code + restart the app.
# Usage on the VPS:  bash /opt/logistics_crm/update.sh
cd /opt/logistics_crm || exit 1
git stash >/dev/null 2>&1
git pull
chown -R www-data:www-data /opt/logistics_crm
systemctl restart logistics-crm
echo "=========================================="
echo " Updated + restarted."
systemctl --no-pager status logistics-crm | head -4
