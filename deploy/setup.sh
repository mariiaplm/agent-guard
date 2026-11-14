#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu server (Vultr). Run as root:
#   bash setup.sh https://github.com/mariiaplm/agent-guard.git
# Safe to run again later: it pulls the latest code and restarts the service.
set -euo pipefail

REPO_URL="${1:?usage: bash setup.sh <git repo url>}"
APP_DIR=/opt/agent-guard

apt-get update -y
apt-get install -y python3-venv python3-pip git

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt -r guard/requirements.txt
if [ -f agent/requirements.txt ]; then .venv/bin/pip install -r agent/requirements.txt; fi

if [ ! -f .env ]; then
  echo "GEMINI_API_KEY=" > .env
  chmod 600 .env
  echo ">>> Created $APP_DIR/.env. Add your Gemini key to it, then run: systemctl restart agentguard"
fi

cp deploy/agentguard.service /etc/systemd/system/agentguard.service
systemctl daemon-reload
systemctl enable agentguard
systemctl restart agentguard

if command -v ufw >/dev/null; then ufw allow 22/tcp || true; ufw allow 8000/tcp || true; fi

sleep 2
systemctl --no-pager --lines=5 status agentguard || true
echo
echo ">>> Done. Open http://$(curl -s -4 ifconfig.me || echo SERVER_IP):8000/"