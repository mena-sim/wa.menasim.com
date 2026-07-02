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
WORKERS="${WORKERS:-2}"
# ---------------------------------------------------------------------------

cyan()  { printf "\033[1;36m==> %s\033[0m\n" "$*"; }
green() { printf "\033[1;32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[1;33m%s\033[0m\n" "$*"; }
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

# PIDs listening on $PORT (ss and/or fuser).
port_pids() {
  { ss -lptnH "sport = :${PORT}" 2>/dev/null | grep -oP 'pid=\K[0-9]+';
    command -v fuser >/dev/null && fuser "${PORT}/tcp" 2>/dev/null; } \
    | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u
}

# Any gunicorn/uvicorn worker serving this app (aaPanel titles vary).
app_pids() {
  { pgrep -f 'gunicorn.*app\.main:app' 2>/dev/null || true;
    pgrep -f 'gunicorn: master' 2>/dev/null || true;
    pgrep -f '[uv]icorn.*app\.main:app' 2>/dev/null || true; } \
    | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u
}

all_app_pids() {
  { port_pids; app_pids; } | sort -u
}

kill_app_on_port() {
  local pids attempt pid
  pids="$(all_app_pids || true)"
  if [ -z "${pids:-}" ]; then
    return 0
  fi
  yellow "Stopping app on :${PORT} (PID(s): $(echo "$pids" | tr '\n' ' '))"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  for attempt in 1 2 3 4 5; do
    sleep 1
    pids="$(port_pids || true)"
    [ -z "${pids:-}" ] && return 0
  done
  yellow "Port :${PORT} still in use — force killing."
  for pid in $pids; do
    kill -9 "$pid" 2>/dev/null || true
  done
  sleep 1
}

start_app_gunicorn() {
  mkdir -p data
  local log="data/gunicorn.log"
  yellow "Starting gunicorn (uvicorn workers) on 127.0.0.1:${PORT}…"
  # shellcheck disable=SC2086
  nohup .venv/bin/gunicorn app.main:app \
    --workers "${WORKERS}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "127.0.0.1:${PORT}" \
    --chdir "${APP_DIR}" \
    --access-logfile - \
    --error-logfile - \
    >>"${log}" 2>&1 &
  disown 2>/dev/null || true
  sleep 3
}

if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
  sudo systemctl restart "$SERVICE"
  restarted=true
  sleep 3
  sudo systemctl --no-pager --lines=5 status "$SERVICE" || true
elif pgrep -f 'gunicorn.*app\.main:app|gunicorn: master' >/dev/null 2>&1; then
  green "Detected gunicorn — sending graceful reload (HUP) to master(s)."
  pkill -HUP -f 'gunicorn: master' 2>/dev/null || pkill -HUP -f 'gunicorn.*app\.main:app' 2>/dev/null || true
  restarted=true
  sleep 3
else
  PIDS="$(port_pids || true)"
  if [ -n "${PIDS:-}" ] || [ -n "$(app_pids || true)" ]; then
    yellow "No systemd/gunicorn master matched — killing :${PORT} and restarting gunicorn."
    kill_app_on_port
    start_app_gunicorn
    restarted=true
  else
    yellow "Nothing on :${PORT} — starting gunicorn."
    start_app_gunicorn
    restarted=true
  fi
fi

if [ "$restarted" = false ]; then
  red "App was not restarted. Set up systemd for automatic restarts:"
  red "  sudo cp deploy/${SERVICE}.service /etc/systemd/system/${SERVICE}.service"
  red "  sudo systemctl daemon-reload && sudo systemctl enable --now ${SERVICE}"
fi

# 6) Health check + verify the RUNNING code matches what we just pulled -----
cyan "6/6  Health check"
if ! curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null; then
  red "Health check FAILED — the app isn't responding on :${PORT}."
  red "Check logs: tail -50 data/gunicorn.log"
  red "If you use systemd: journalctl -u ${SERVICE} -n 50"
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
    red   "Re-run ./deploy.sh or check data/gunicorn.log for startup errors."
  fi
fi

green "Done."
green "  Admin  : https://adminwa.menasim.com/"
green "  Chat/API: https://wa.menasim.com/"
