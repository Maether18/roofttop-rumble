#!/usr/bin/env bash
# Rooftop Rumble: install and start both game servers on a fresh Ubuntu
# 22.04 / 24.04 VM (Azure, AWS, Oracle...). Run it as a normal sudo user or root:
#
#   curl -fsSL https://raw.githubusercontent.com/Maether18/roofttop-rumble/<branch>/deploy/setup.sh | bash -s <branch>
#   # or
#   git clone -b <branch> https://github.com/Maether18/roofttop-rumble.git && bash roofttop-rumble/deploy/setup.sh <branch>
#
# <branch> defaults to main. Safe to run again: it updates the code and
# restarts the servers. Everything is inside main(), so with "curl | bash"
# the whole script is downloaded before anything runs.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Maether18/roofttop-rumble.git}"
INSTALL_DIR="/opt/rooftop-rumble"
SERVICES=(rooftop-tcp rooftop-udp)
TCP_PORT=5000
UDP_PORT=5001

step() { printf '\n==> %s\n' "$*"; }
warn() { printf '  WARNING: %s\n' "$*" >&2; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

main() {
    local branch="${1:-main}"
    local sudo=""
    if [ "$(id -u)" -ne 0 ]; then sudo="sudo"; fi
    local apt=($sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -y -q)

    step "Installing git, python3 and curl"
    "${apt[@]}" update < /dev/null
    "${apt[@]}" install git python3 python3-venv curl ca-certificates < /dev/null

    step "Getting branch '$branch' into $INSTALL_DIR"
    if [ -d "$INSTALL_DIR/.git" ]; then
        $sudo git -C "$INSTALL_DIR" fetch --quiet origin
        $sudo git -C "$INSTALL_DIR" checkout --quiet "$branch"
        $sudo git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$branch"
    else
        $sudo git clone --quiet --branch "$branch" "$REPO_URL" "$INSTALL_DIR"
    fi
    echo "  now at: $($sudo git -C "$INSTALL_DIR" log -1 --format='%h %s')"
    for s in "${SERVICES[@]}"; do
        [ -f "$INSTALL_DIR/deploy/$s.service" ] ||
            die "branch '$branch' has no deploy/$s.service. Use a branch that has the deploy/ folder (for example: bash -s feature)."
    done

    step "Python environment ($INSTALL_DIR/.venv)"
    $sudo python3 -m venv "$INSTALL_DIR/.venv"
    local reqs="$INSTALL_DIR/requirements-server.txt"
    if [ -f "$reqs" ] && grep -qvE '^[[:space:]]*(#|$)' "$reqs"; then
        $sudo "$INSTALL_DIR/.venv/bin/pip" install -q -r "$reqs"
    else
        echo "  no extra packages needed (standard library only)"
    fi
    (cd "$INSTALL_DIR" && .venv/bin/python3 -B -c "import tcp.server, udp.server") ||
        die "the server code does not import, see the error above"

    step "Installing and starting systemd services"
    for s in "${SERVICES[@]}"; do
        $sudo install -m 644 "$INSTALL_DIR/deploy/$s.service" "/etc/systemd/system/$s.service"
    done
    $sudo systemctl daemon-reload
    $sudo systemctl enable --quiet "${SERVICES[@]}"
    $sudo systemctl restart "${SERVICES[@]}"

    step "Opening ports $TCP_PORT/tcp and $UDP_PORT/udp in the VM's firewall"
    if command -v ufw > /dev/null && [[ "$($sudo ufw status 2>/dev/null)" == *"Status: active"* ]]; then
        $sudo ufw allow "$TCP_PORT/tcp" > /dev/null
        $sudo ufw allow "$UDP_PORT/udp" > /dev/null
        echo "  ufw: allowed"
    else
        echo "  ufw is not active: nothing to open"
    fi
    # Oracle Cloud Ubuntu images ship iptables rules that REJECT everything
    # except SSH, even when ufw is off. Insert ACCEPT rules before them.
    if command -v iptables > /dev/null && [[ "$($sudo iptables -S INPUT 2>/dev/null)" == *"-j REJECT"* ]]; then
        local rule
        for rule in "-p tcp --dport $TCP_PORT" "-p udp --dport $UDP_PORT"; do
            # shellcheck disable=SC2086  # $rule must split into separate arguments
            $sudo iptables -C INPUT $rule -j ACCEPT 2>/dev/null || $sudo iptables -I INPUT $rule -j ACCEPT
        done
        if command -v netfilter-persistent > /dev/null; then
            $sudo netfilter-persistent save > /dev/null 2>&1 || warn "could not save iptables rules"
        else
            warn "iptables rules added but not saved; they reset on reboot"
        fi
        echo "  iptables: allowed (REJECT rules found, as on Oracle Cloud)"
    fi

    step "Checking the services"
    sleep 2
    local ok=1
    for s in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "$s"; then
            echo "  $s: running"
        else
            ok=0
            warn "$s is not running. Last log lines:"
            $sudo journalctl -u "$s" -n 15 --no-pager >&2 || true
        fi
    done

    local ip
    ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null ||
          curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null ||
          echo "<VM_PUBLIC_IP>")"

    cat <<EOF

============================================================
 Rooftop Rumble servers on $ip
   TCP server: port $TCP_PORT/tcp      UDP server: port $UDP_PORT/udp
============================================================
 IMPORTANT: also allow inbound TCP $TCP_PORT and UDP $UDP_PORT in the
 cloud firewall (Azure NSG / AWS security group / Oracle security list).

 Players run, on their own computers in the project folder:
   python -m client.ui --protocol tcp --host $ip --port $TCP_PORT
   python -m client.ui --protocol udp --host $ip --port $UDP_PORT

 Bots to fill a room (from any computer):
   python -m tools.bots --protocol udp --host $ip --port $UDP_PORT --bots 2 --join-only

 On this VM:
   systemctl status rooftop-tcp rooftop-udp     service status
   journalctl -u rooftop-udp -f                 live logs
   bash $INSTALL_DIR/deploy/update.sh           pull new code and restart
EOF
    [ "$ok" -eq 1 ] || die "at least one server failed to start (see the logs above)"
}

main "$@"
