#!/usr/bin/env bash
# Connects the magpie VM to Cloudflare with a tunnel, so https://whitesberg.net reaches the public static
# pages without opening any port on the router. Run as root ON THE magpie VM:
#
#   sudo bash setup-tunnel.sh            asks for the tunnel token (from the Cloudflare dashboard, see README)
#   sudo bash setup-tunnel.sh status     shows the service, its connections and where Cloudflare sends traffic
#
# The tunnel's public hostname (whitesberg.net) must point at http://localhost:8081, the public-only site
# that setup-vm.sh installs. Never at anything else: this script checks that after connecting and stops
# the tunnel if a hostname points somewhere else.
# Safe to re-run: it replaces the token (use that to rotate it).
set -euo pipefail

PUBLIC_PORT=8081
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
[ "$(hostname)" = magpie ] || { echo "Refusing: this VM is '$(hostname)', not magpie." >&2; exit 1; }

# Where cloudflared is sending each hostname, from the configuration Cloudflare pushes to it
check_ingress() {
  local log
  log="$(journalctl -u cloudflared --since "-10 min" --no-pager 2>/dev/null | grep 'Updated to new configuration' | tail -1 || true)"
  if [ -z "$log" ]; then
    echo "    no configuration received yet (add the public hostname in the dashboard, then: sudo bash setup-tunnel.sh status)"
    return 0
  fi
  local services
  services="$(printf '%s' "$log" | grep -oE 'service[\\]*":[\\]*"[^"\\]*' | sed 's/.*"//' | sort -u)"
  echo "    tunnel sends traffic to: $(echo $services)"
  if printf '%s\n' "$services" | grep -E '^https?://' | grep -qvE "^http://(localhost|127\.0\.0\.1):$PUBLIC_PORT/?$"; then
    echo "DANGER: a public hostname points somewhere other than http://localhost:$PUBLIC_PORT (that could put" >&2
    echo "something else on the home network on the internet). Stopping the tunnel. Fix the hostname in the" >&2
    echo "dashboard, then re-run." >&2
    systemctl stop cloudflared
    exit 1
  fi
}

if [ "${1:-}" = status ]; then
  systemctl is-active cloudflared || true
  journalctl -u cloudflared --since "-1 day" --no-pager | grep -c 'Registered tunnel connection' | sed 's/^/    connections registered (last day): /'
  check_ingress
  exit 0
fi

curl -fsS -o /dev/null "http://127.0.0.1:$PUBLIC_PORT/" \
  || { echo "The public site on localhost:$PUBLIC_PORT is not answering. Run .\\deploy\\deploy-site.ps1 first." >&2; exit 1; }

if ! command -v cloudflared >/dev/null; then
  echo "==> installing cloudflared (Cloudflare's apt repository)"
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -y
  apt-get install -y cloudflared
fi
echo "    $(cloudflared --version)"

if [ -z "${TUNNEL_TOKEN:-}" ]; then
  read -r -s -p "Tunnel token (Cloudflare > Zero Trust > Networks > Tunnels > whitesberg): " TUNNEL_TOKEN < /dev/tty
  echo
fi
# accept either the bare token or the whole "cloudflared service install <token>" line from the dashboard
TUNNEL_TOKEN="$(printf '%s' "$TUNNEL_TOKEN" | tr -d '[:space:]' | sed 's/^.*install//')"
[ ${#TUNNEL_TOKEN} -gt 50 ] || { echo "That does not look like a tunnel token." >&2; exit 1; }

echo "==> cloudflared service"
# The token lives in a root-only file, so it never shows in the unit file or the process list
# (HomeNetwork doc 16, "Network and public access").
install -d -m 0700 /etc/cloudflared
( umask 077; printf '%s' "$TUNNEL_TOKEN" > /etc/cloudflared/token )
unset TUNNEL_TOKEN
cat > /etc/systemd/system/cloudflared.service <<'UNIT'
[Unit]
Description=Cloudflare Tunnel (whitesberg.net public pages)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run --token-file /etc/cloudflared/token
Restart=on-failure
RestartSec=5s
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable cloudflared >/dev/null
systemctl restart cloudflared

echo "==> waiting for the tunnel to connect"
OK=false
for _ in $(seq 1 30); do
  if journalctl -u cloudflared --since "-2 min" --no-pager | grep -q 'Registered tunnel connection'; then OK=true; break; fi
  sleep 1
done
if ! $OK; then
  echo "cloudflared did not connect. Recent log:" >&2
  journalctl -u cloudflared -n 20 --no-pager >&2 || true
  exit 1
fi
echo "    connected"
sleep 3
check_ingress

cat <<EOF

Done. The tunnel starts with the VM. Check from anywhere: https://whitesberg.net
No ports are open on the router; Cloudflare provides HTTPS. Status any time: sudo bash setup-tunnel.sh status
EOF
