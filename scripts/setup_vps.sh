#!/usr/bin/env bash
# Idempotent VPS bootstrap: Docker, Docker Compose plugin, docker group, UFW (22/80/443).
# Run on a fresh Ubuntu/Debian VPS as a user with sudo. Re-run safe if already configured.

set -euo pipefail

log() { printf '[setup_vps] %s\n' "$*"; }
warn() { printf '[setup_vps] WARN: %s\n' "$*" >&2; }

if ! command -v sudo >/dev/null 2>&1; then
  echo "Error: sudo is required." >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  warn "Running as root; docker group step will be skipped."
  RUN_USER="${SUDO_USER:-root}"
else
  RUN_USER="$(id -un)"
fi

log "Installing Docker (get.docker.com) if missing..."
if command -v docker >/dev/null 2>&1; then
  log "Docker already installed: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sudo sh
fi

log "Ensuring Docker service is enabled and running..."
sudo systemctl enable docker >/dev/null 2>&1 || true
sudo systemctl start docker

log "Checking Docker Compose plugin..."
if docker compose version >/dev/null 2>&1; then
  log "Docker Compose plugin: $(docker compose version)"
else
  warn "docker compose plugin not found after install; attempting package install..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y docker-compose-plugin
  else
    warn "Could not auto-install compose plugin on this distro. Install manually if needed."
  fi
fi

if [[ "$(id -u)" -ne 0 ]]; then
  log "Adding user '${RUN_USER}' to docker group..."
  if groups "$RUN_USER" | grep -q '\bdocker\b'; then
    log "User already in docker group."
  else
    sudo usermod -aG docker "$RUN_USER"
    log "Added to docker group. Log out and back in (or new SSH session) for group to apply."
  fi
fi

log "Configuring UFW (allow 22, 80, 443; deny other incoming)..."
if command -v ufw >/dev/null 2>&1; then
  sudo ufw --force reset >/dev/null 2>&1 || true
  sudo ufw default deny incoming
  sudo ufw default allow outgoing
  sudo ufw allow 22/tcp comment 'SSH'
  sudo ufw allow 80/tcp comment 'HTTP'
  sudo ufw allow 443/tcp comment 'HTTPS'
  sudo ufw --force enable
  sudo ufw status verbose || true
else
  warn "ufw not installed; skipping firewall setup."
fi

log "=== VPS setup summary ==="
docker --version 2>/dev/null || warn "docker binary not in PATH for current shell"
docker compose version 2>/dev/null || warn "docker compose not available"
if command -v ufw >/dev/null 2>&1; then
  log "UFW: enabled (22, 80, 443 allowed)"
else
  log "UFW: not configured"
fi
log "Done. Next: clone senderplatformtest, configure .env, run docker compose from multi-messaging-platform/"
