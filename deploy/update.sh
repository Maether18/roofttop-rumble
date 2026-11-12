#!/usr/bin/env bash
# Rooftop Rumble: pull the latest code on the deployed branch and restart
# both servers. Players in a match are disconnected by the restart.
#
#   bash /opt/rooftop-rumble/deploy/update.sh
#
# To switch branches, run deploy/setup.sh <branch> again instead.
set -euo pipefail

INSTALL_DIR="/opt/rooftop-rumble"
SERVICES=(rooftop-tcp rooftop-udp)

sudo=""
if [ "$(id -u)" -ne 0 ]; then sudo="sudo"; fi

$sudo git -C "$INSTALL_DIR" pull --ff-only
echo "now at: $($sudo git -C "$INSTALL_DIR" log -1 --format='%h %s')"

# Reinstall the units in case they changed in this update.
for s in "${SERVICES[@]}"; do
    $sudo install -m 644 "$INSTALL_DIR/deploy/$s.service" "/etc/systemd/system/$s.service"
done
$sudo systemctl daemon-reload
$sudo systemctl restart "${SERVICES[@]}"

sleep 2
for s in "${SERVICES[@]}"; do
    printf '%s: %s\n' "$s" "$(systemctl is-active "$s" || true)"
done
