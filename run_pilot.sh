#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
export LINEPILOT_API_KEY="${LINEPILOT_API_KEY:-pilot-demo-key-2025}"
export LINEPILOT_API_URL="http://localhost:8000"

echo "🚀 LinePilot Pilot Stack Launcher"
echo "   API Key: $LINEPILOT_API_KEY"
echo ""

# --- Start services ---
echo "▶ Starting services with Docker Compose..."
docker compose up -d --build

# --- Wait for API health ---
echo "▶ Waiting for API health check..."
RETRIES=20
until curl -s -o /dev/null -w "%{http_code}" "$LINEPILOT_API_URL/health" | grep -q 200; do
    sleep 2
    RETRIES=$((RETRIES-1))
    if [ $RETRIES -le 0 ]; then
        echo "❌ API failed to start"
        docker compose logs api
        exit 1
    fi
done
echo "✔ API is healthy"

# --- Run integration tests ---
echo "▶ Running end‑to‑end integration tests..."
LINEPILOT_API_KEY="$LINEPILOT_API_KEY" python -m pytest tests/test_pilot_integration.py -v || {
    echo "❌ Integration tests failed. Check logs."
    exit 1
}

# --- Launch dashboard (optional) ---
echo ""
echo "✔ Pilot stack is live:"
echo "   Dashboard  → http://localhost:8501"
echo "   API Docs   → http://localhost:8000/docs"
echo "   Telemetry  → generating synthetic intervals every 5s"
echo ""
echo "▶ Open the dashboard in your default browser? (y/n)"
read -r OPEN
if [[ "$OPEN" =~ ^[Yy]$ ]]; then
    if command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:8501"
    elif command -v open &>/dev/null; then
        open "http://localhost:8501"
    fi
fi

echo "▶ To stop the stack: docker compose down"