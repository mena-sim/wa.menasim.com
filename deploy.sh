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
NEW_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

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
restarted=false

# Show what is currently serving the port (helps diagnose the runner).
port_pids() {
  { ss -lptnH "sport = :${PORT}" 2>/dev/null | grep -oP 'pid=\K[0-9]+';
    command -v fuser >/dev/null && fuser "${PORT}/tcp" 2>/dev/null; } \
    | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u
}

if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
  sudo systemctl restart "$SERVICE"
  restarted=true
  sleep 3
  sudo systemctl --no-pager --lines=5 status "$SERVICE" || true
elif pgrep -f "gunicorn: master .*app\.main:app" >/dev/null 2>&1; then
  # aaPanel/gunicorn: graceful reload (re-imports new code, zero downtime).
  green "Detected gunicorn — sending graceful reload (HUP) to the master."
  pkill -HUP -f "gunicorn: master .*app\.main:app" || true
  restarted=true
  sleep 3
else
  red "No systemd service and no gunicorn master found for app.main:app."
  PIDS="$(port_pids || true)"
  if [ -n "${PIDS:-}" ]; then
    red "Something is serving :${PORT} (PID(s): ${PIDS}). It's likely the aaPanel"
    red "Python Project Manager. Restart it there, or set up systemd:"
  else
    red "Nothing appears to be serving :${PORT}. First-time setup options:"
  fi
  red "  sudo cp deploy/${SERVICE}.service /etc/systemd/system/${SERVICE}.service"
  red "  sudo systemctl daemon-reload && sudo systemctl enable --now ${SERVICE}"
  red "…or aaPanel -> Python Project Manager -> ${SERVICE} -> Restart."
fi

# 6) Health check + verify the RUNNING code matches what we just pulled -----
cyan "6/6  Health check"
if ! curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null; then
  red "Health check FAILED — the app isn't responding on :${PORT}."
  red "If you just restarted via systemd:  journalctl -u ${SERVICE} -n 50"
  exit 1
fi
green "OK — http://127.0.0.1:${PORT}/health responded."

RUN_SHA="$(curl -fsS "http://127.0.0.1:${PORT}/health/version" 2>/dev/null \
  | python -c "import sys,json;print(json.load(sys.stdin).get('commit',''))" 2>/dev/null || echo '')"
if [ -n "$RUN_SHA" ] && [ "$RUN_SHA" != "unknown" ]; then
  if [ "$RUN_SHA" = "$NEW_SHA" ]; then
    green "Running code is up to date (commit ${RUN_SHA})."
  else
    red   "WARNING: the app is STILL running old code (running ${RUN_SHA}, latest ${NEW_SHA})."
    red   "The restart didn't take effect. Restart the app now:"
    red   "  aaPanel -> Python Project Manager -> ${SERVICE} -> Restart"
    red   "  (or set up systemd as above so ./deploy.sh can do it automatically)."
  fi
fi

green "Done."
green "  Admin  : https://adminwa.menasim.com/"
green "  Chat/API: https://wa.menasim.com/"
