#!/usr/bin/env bash
# Magpie site VM setup (Ubuntu Server 26.04 LTS, VM "magpie", 192.168.10.16)
#
# Run on the VM from the folder deploy-site.ps1 uploads (site.tgz and the two NGINX configs):
#   sudo bash setup-vm.sh                 full setup (first time, or to re-harden), then publish
#   sudo bash setup-vm.sh --deploy-only   just publish (what deploy-site.ps1 normally runs)
#
# What a full setup does:
#   1. Updates the system and turns on automatic security updates
#   2. Installs NGINX, certbot (with the Cloudflare DNS plugin), the Proxmox guest agent, ufw and vim
#   3. Publishes the site (below)
#   4. Opens only SSH, HTTP and HTTPS in the firewall
#   5. Turns off SSH password login, but ONLY if you've already added an SSH key
#
# Publishing:
#   - the site is unpacked into a new folder under /var/www/magpie-releases
#   - /var/www/magpie points at it (switched in one step, so there is no half-published moment)
#   - the last 3 releases are kept; to roll back, point the link at an older one:
#       sudo ln -sfn /var/www/magpie-releases/<older> /var/www/magpie
#
# HTTPS: the site is served over HTTPS once /etc/letsencrypt/live/magpie.whitesberg.net exists
# (README, "Site certificate"). Until then the HTTP-only config is installed.

set -euo pipefail

DEPLOY_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-only) DEPLOY_ONLY=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash setup-vm.sh" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN=magpie.whitesberg.net
CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
if [[ -f "$CERT" ]]; then CONF=magpie-site.conf; SCHEME=https; else CONF=magpie-site-http.conf; SCHEME=http; fi
PUBLIC_CONF=magpie-public.conf   # whitesberg.net through the Cloudflare tunnel, localhost:8081 only
for f in site.tgz "$CONF" "$PUBLIC_CONF"; do
  [[ -f "$HERE/$f" ]] || { echo "Missing $f next to this script." >&2; exit 1; }
done

# Guard against deploying to the wrong VM
HOST="$(hostname)"
if [[ "$HOST" != "magpie" ]]; then
  echo "Refusing: this VM is '$HOST', not magpie." >&2; exit 1
fi

ADMIN_USER="${SUDO_USER:-root}"
ADMIN_HOME="$(getent passwd "$ADMIN_USER" | cut -d: -f6)"
WEBROOT=/var/www/magpie
RELEASES=/var/www/magpie-releases

# ---------- full setup ----------

system_setup() {
  echo "==> Updating the system"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get upgrade -yq
  apt-get install -yq nginx certbot python3-certbot-dns-cloudflare qemu-guest-agent ufw vim unattended-upgrades
  dpkg-reconfigure -f noninteractive unattended-upgrades
  systemctl enable --now qemu-guest-agent || true   # needs Options > QEMU Guest Agent on in Proxmox
  rm -f /etc/nginx/sites-enabled/default
}

firewall() {
  echo "==> Firewall: SSH, HTTP and HTTPS only"
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow OpenSSH
  ufw allow 'Nginx Full'
  ufw delete allow 'Nginx HTTP' >/dev/null 2>&1 || true   # replaced by Nginx Full
  ufw --force enable
}

ssh_hardening() {
  local keys="$ADMIN_HOME/.ssh/authorized_keys"
  if [[ -s "$keys" ]]; then
    echo "==> Turning off SSH password login (a key is set up for $ADMIN_USER)"
    printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' \
      > /etc/ssh/sshd_config.d/10-magpie.conf
    systemctl reload ssh
  else
    echo "!!  No SSH key in $keys, so password login stays ON. Add a key and run the full setup again."
  fi
}

# ---------- publish ----------

publish() {
  echo "==> Publishing the site ($SCHEME)"
  command -v nginx >/dev/null || { echo "NGINX isn't installed. Run the full setup first (deploy-site.ps1 -Full)." >&2; exit 1; }
  local release="$RELEASES/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$release"
  tar -xzf "$HERE/site.tgz" -C "$release"
  find "$release" -type d -exec chmod 755 {} +
  find "$release" -type f -exec chmod 644 {} +
  chown -R root:root "$release"

  # an old plain folder (not a link) would block the switch
  if [[ -d "$WEBROOT" && ! -L "$WEBROOT" ]]; then mv "$WEBROOT" "$WEBROOT.old-$(date +%s)"; fi
  ln -sfn "$release" "$WEBROOT.tmp" && mv -T "$WEBROOT.tmp" "$WEBROOT"

  install -m 644 "$HERE/$CONF" /etc/nginx/sites-available/magpie.conf
  ln -sfn /etc/nginx/sites-available/magpie.conf /etc/nginx/sites-enabled/magpie.conf
  install -m 644 "$HERE/$PUBLIC_CONF" /etc/nginx/sites-available/whitesberg-public.conf
  ln -sfn /etc/nginx/sites-available/whitesberg-public.conf /etc/nginx/sites-enabled/whitesberg-public.conf
  nginx -t
  systemctl reload nginx

  # Keep the newest 3 releases. Sort by name (the timestamp), not by folder date: unpacking gives
  # every release folder the date of site/, so a date sort can delete the release just published.
  ls -1d "$RELEASES"/*/ | sort -r | tail -n +4 | xargs -r rm -rf

  # NGINX reloads in the background, so give the new config a few seconds to take over
  local page code try
  for page in / /magpie/ /magpie/privacy.html; do
    for try in 1 2 3 4 5 6 7 8 9 10; do
      code="$(curl -s -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" --resolve "$DOMAIN:80:127.0.0.1" "$SCHEME://$DOMAIN$page" || true)"
      [[ "$code" == "200" ]] && break
      sleep 1
    done
    [[ "$code" == "200" ]] || { echo "$page answered $code instead of 200." >&2; exit 1; }
    # the public copy that the Cloudflare tunnel serves as whitesberg.net
    code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: whitesberg.net' "http://127.0.0.1:8081$page" || true)"
    [[ "$code" == "200" ]] || { echo "Public site: $page answered $code instead of 200." >&2; exit 1; }
  done
  echo "==> Published $(basename "$release") at $SCHEME://$DOMAIN"
  [[ "$SCHEME" == https ]] || echo "!!  No certificate yet, so the site is HTTP only. See README, \"Site certificate\"."
}

if [[ $DEPLOY_ONLY -eq 0 ]]; then
  system_setup
  publish
  firewall
  ssh_hardening
else
  publish
fi
