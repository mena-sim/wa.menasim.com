#!/usr/bin/env bash
#
# Menasim WA Support — one-shot deploy / update.
# Run on the server from the app folder:
#
#     cd /www/wamena
#     chmod +x deploy.sh        # first time only
#     ./deploy.sh
#
# What it does: pull latest from git -> install Python deps -> build the React
# admin console -> restart the app -> health check.
# (The chat page at "/" is static HTML served by the API — nothing to build.)
#
set -euo pipefail

# ------------------- config (override via env if needed) -------------------
APP_DIR="${APP_DIR:-/www/wamena}"
BRANCH="${BRANCH:-main}"
SERVICE="${SERVICE:-wamena}"          # systemd service name (see deploy/wamena.service)
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
# ---------------------------------------------------------------------------

cyan()  { printf "\033[1;36m==> %s\033[0m\n" "$*"; }
green() { printf "\033[1;32m%s\033[0m\n" "$*"; }
red()   { printf "\033[1;31m%s\033[0m\n" "$*"; }

cd "$APP_DIR"

# 1) Sync code -------------------------------------------------------------
cyan "1/6  Pulling latest code (origin/$BRANCH)"
git fetch origin "$BRANCH"
if ! git pull --ff-only origin "$BRANCH"; then
  red   "Fast-forward not possible — hard-resetting to origin/$BRANCH."
  red   "(Only git-tracked files are reset; your .env and data/ are gitignored and kept.)"
  git reset --hard "origin/$BRANCH"
fi

# 2) Python deps -----------------------------------------------------------
cyan "2/6  Python venv + dependencies"
if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 3) Build the admin console ----------------------------------------------
cyan "3/6  Building admin console (admin-web -> dist)"
pushd admin-web >/dev/null
if [ -f package-lock.json ]; then npm ci; else npm install; fi
npm run build
popd >/dev/null

# 4) Runtime dirs ----------------------------------------------------------
cyan "4/6  Ensuring runtime folders (data/media, data/chroma)"
mkdir -p data/media data/chroma

# 5) Restart the app -------------------------------------------------------
cyan "5/6  Restarting the app"
if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
  sudo systemctl restart "$SERVICE"
  sleep 3
  sudo systemctl --no-pager --lines=5 status "$SERVICE" || true
else
  red "systemd service '$SERVICE' not found."
  red "First-time setup:"
  red "  sudo cp deploy/${SERVICE}.service /etc/systemd/system/${SERVICE}.service"
  red "  sudo systemctl daemon-reload && sudo systemctl enable --now ${SERVICE}"
  red "…or restart the project from aaPanel → Python Project Manager."
fi

# 6) Health check ----------------------------------------------------------
cyan "6/6  Health check"
if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null; then
  green "OK — http://127.0.0.1:${PORT}/health responded."
else
  red "Health check FAILED — inspect logs:  journalctl -u ${SERVICE} -n 50"
  exit 1
fi

green "Done."
green "  Admin  : https://adminwa.menasim.com/"
green "  Chat/API: https://wa.menasim.com/"
