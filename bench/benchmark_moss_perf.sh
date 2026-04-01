#!/usr/bin/env bash
# benchmark_moss_perf.sh — Compare subprocess vs HTTP server perf on test_008
#
# Run on the GPU server:
#   bash /opt/sila/bench/benchmark_moss_perf.sh
#
# Prerequisites:
#   - test_008 created via setup_test_008.sh
#   - moss-tts-server.service installed and stopped (for baseline)

set -euo pipefail

APP_DIR="/opt/sila/app"
INPUT="/opt/sila/projects/test_008/source.mp4"
BASELINE_DIR="/opt/sila/projects/test_008_perf_baseline"
SERVER_DIR="/opt/sila/projects/test_008_perf_server"

cd "${APP_DIR}"

if [ ! -f "${INPUT}" ]; then
    echo "ERROR: ${INPUT} not found. Run setup_test_008.sh first."
    exit 1
fi

# --- Baseline: subprocess mode ---
echo "=== BASELINE (subprocess) ==="
echo "Stopping moss-tts-server to force subprocess mode..."
systemctl stop moss-tts-server 2>/dev/null || true
sleep 2

START_SUB=$(date +%s)
python -m src.cli.main \
    --input "${INPUT}" \
    --target-langs en \
    --tts-engine moss \
    --demucs auto \
    --rewrite-endpoint http://localhost:8081 \
    --output-dir "${BASELINE_DIR}" \
    --force-reprocess
END_SUB=$(date +%s)
TIME_SUB=$((END_SUB - START_SUB))
echo "Subprocess total: ${TIME_SUB}s"

# --- HTTP server mode ---
echo ""
echo "=== HTTP SERVER ==="
echo "Starting moss-tts-server..."
systemctl start moss-tts-server
echo "Waiting for model load..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8082/health > /dev/null 2>&1; then
        echo "Server ready after ${i}s"
        break
    fi
    sleep 2
done
curl -s http://localhost:8082/health | python3 -m json.tool

START_HTTP=$(date +%s)
python -m src.cli.main \
    --input "${INPUT}" \
    --target-langs en \
    --tts-engine moss \
    --demucs auto \
    --rewrite-endpoint http://localhost:8081 \
    --output-dir "${SERVER_DIR}" \
    --force-reprocess
END_HTTP=$(date +%s)
TIME_HTTP=$((END_HTTP - START_HTTP))
echo "HTTP server total: ${TIME_HTTP}s"

# --- Comparison ---
echo ""
echo "=== PERF COMPARISON ==="
echo "Subprocess: ${TIME_SUB}s"
echo "HTTP server: ${TIME_HTTP}s"
if [ "${TIME_HTTP}" -gt 0 ]; then
    SPEEDUP=$(python3 -c "print(f'{${TIME_SUB}/${TIME_HTTP}:.1f}')")
    echo "Speedup: ${SPEEDUP}x"
fi

# --- Audit both ---
echo ""
echo "=== AUDIT ==="
echo "--- Baseline ---"
python scripts/audio_audit.py "${BASELINE_DIR}"/*_en.mp4 2>/dev/null || echo "(audit script not found)"
echo "--- HTTP Server ---"
python scripts/audio_audit.py "${SERVER_DIR}"/*_en.mp4 2>/dev/null || echo "(audit script not found)"

echo ""
echo "=== METRICS ==="
python scripts/show_metrics.py "${BASELINE_DIR}/" 2>/dev/null || true
python scripts/show_metrics.py "${SERVER_DIR}/" 2>/dev/null || true
