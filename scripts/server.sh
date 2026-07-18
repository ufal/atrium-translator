#!/usr/bin/env bash
# Start the ATRIUM Translator API server and wait until it is healthy.
#
# Prefers Docker Compose (the `api` service), falls back to a local uvicorn
# launch inside a repository virtual environment.
#
# Usage:
#   bash scripts/server.sh            # Docker Compose api service, or local fallback
#   bash scripts/server.sh --local    # skip Docker, run uvicorn directly
#
# Environment:
#   ATRIUM_TR_PORT       - port to serve on (default: 8000)
#   ATRIUM_TR_URL        - health-check target (default: http://localhost:$ATRIUM_TR_PORT)
#   TRANSLATION_BACKEND  - lindat (default) or openai_compatible

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${ATRIUM_TR_PORT:-8000}"
BASE_URL="${ATRIUM_TR_URL:-http://localhost:${PORT}}"
HEALTH_URL="${BASE_URL}/info"
MODE="auto"

for arg in "$@"; do
    case "$arg" in
        --local) MODE="local" ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# Already running? Nothing to do.
if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    echo "✅ API already healthy at ${BASE_URL}"
    exit 0
fi

cd "$REPO_ROOT"

start_docker() {
    echo "🐳 Starting via docker compose (api service)..."
    docker compose up -d api
}

start_local() {
    echo "🐍 Starting local uvicorn server..."
    if [ ! -d "venv" ]; then
        echo "No venv found - creating one and installing requirements..."
        python3 -m venv venv
        # shellcheck disable=SC1091
        source venv/bin/activate
        pip install --upgrade pip
        pip install -r requirements.txt -r service/requirements.txt
    else
        # shellcheck disable=SC1091
        source venv/bin/activate
    fi
    nohup uvicorn service.api:app --host 0.0.0.0 --port "$PORT" > api_server.log 2>&1 &
    echo "Server PID: $! (logs: api_server.log)"
}

case "$MODE" in
    local) start_local ;;
    auto)
        if command -v docker > /dev/null 2>&1 && docker info > /dev/null 2>&1; then
            start_docker
        else
            start_local
        fi
        ;;
esac

# First launch downloads the FastText language-identification model; the
# translation itself runs against the remote LINDAT API (no big local models).
echo "⏳ Waiting for ${HEALTH_URL} (warmup may take a few minutes on first run)..."
DEADLINE=$((SECONDS + 900))
until curl -sf "$HEALTH_URL" > /dev/null 2>&1; do
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
        echo "❌ Server did not become healthy within 15 minutes." >&2
        echo "   Check: api_server.log (local) or 'docker compose logs api' (Docker)." >&2
        exit 1
    fi
    sleep 5
done

echo "✅ API healthy at ${BASE_URL}"
