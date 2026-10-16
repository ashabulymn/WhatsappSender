#!/usr/bin/env bash
# wasend — start webhook receiver (FastAPI) + Streamlit dashboard.
# WEBHOOK_PORT defaults to 8000, DASHBOARD_PORT defaults to 8501.
set -euo pipefail

WEBHOOK_PORT="${WEBHOOK_PORT:-8000}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8501}"

echo "Starting GoWA webhook receiver on :${WEBHOOK_PORT} ..."
uvicorn webhook:app --host 0.0.0.0 --port "${WEBHOOK_PORT}" &
WEBHOOK_PID=$!

echo "Starting Streamlit dashboard on :${DASHBOARD_PORT} ..."
streamlit run dashboard.py \
    --server.port="${DASHBOARD_PORT}" \
    --server.address=0.0.0.0 \
    --server.headless=true &
DASH_PID=$!

# Forward termination signals to both children
trap 'kill ${WEBHOOK_PID} ${DASH_PID} 2>/dev/null; wait' TERM INT

wait
