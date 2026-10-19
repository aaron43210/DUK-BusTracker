#!/usr/bin/env bash
# =============================================================================
# dev-up.sh — bring the DUK Bus Tracker stack up LOCALLY for testing.
#
# Wraps `docker compose` with docker-compose.yml + docker-compose.override.yml
# (the override disables TLS, exposes :8000/:5432, and mounts a dummy Firebase
# creds file). Never used on the server — deploy there with push_to_server.sh.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# 1. Docker must be running.
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running. Start Docker Desktop (or colima) and retry." >&2
  exit 1
fi

# 2. Local-only Firebase creds path (dummy file; push notifications no-op locally).
export FIREBASE_CREDS_PATH="./duk_micro/firebase_creds.local.json"
if [ ! -f "$FIREBASE_CREDS_PATH" ]; then
  echo '{"_comment":"local dev dummy"}' > "$FIREBASE_CREDS_PATH"
fi

# 3. Preflight: the app refuses to start if these are blank/short (see settings.py).
#    DATABASE_URL is supplied by the override, so it may be blank in duk_micro/.env.
ENV_FILE="./duk_micro/.env"
missing=0
check() { # var, min_len
  local v; v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
  if [ -z "$v" ] || [ "${#v}" -lt "${2:-1}" ]; then
    echo "  - $1 is missing or too short in $ENV_FILE (needs >= ${2:-1} chars)"; missing=1
  fi
}
check SECRET_KEY 32
check ADMIN_TOKEN 32
check GPS_API_KEY 1
check ADMIN_USERNAME 1
check ADMIN_PASSWORD 1
if [ "$missing" = "1" ]; then
  echo "ERROR: fill the values above in $ENV_FILE before starting (generate with: openssl rand -hex 32)." >&2
  exit 1
fi

# 4. Up. Pass through any extra args (e.g. -d, or a service name).
echo "Starting stack — API on http://localhost:8000  (gateway on http://localhost:8080)"
exec docker compose up --build "$@"
