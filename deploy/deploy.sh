#!/usr/bin/env bash
# Push the local checkout to the Lightsail box and (re)start it.
# Run LOCALLY from the repo root:
#   SSH_KEY=~/.ssh/lightsail-sg.pem bash deploy/deploy.sh [host]
#
# Copies the code plus your local .env (gateway key) over SSH; the server
# never needs GitHub access. Safe to re-run after every change.
set -euo pipefail

HOST="${1:-56.10.70.203}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/lightsail-sg.pem}"
REMOTE_DIR="/home/ubuntu/supplier-compare-agent"
SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$HOST")

cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || { echo "No .env here; the server needs the gateway key." >&2; exit 1; }

"${SSH[@]}" "mkdir -p $REMOTE_DIR"
rsync -az --delete -e "ssh -i $SSH_KEY" \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude '*.pyc' \
  --exclude decisions.jsonl --exclude agent-core --exclude starter-kit \
  ./ "ubuntu@$HOST:$REMOTE_DIR/"
"${SSH[@]}" "chmod 600 $REMOTE_DIR/.env && sudo bash $REMOTE_DIR/deploy/setup_server.sh"

echo "Live at http://$HOST/"
