#!/usr/bin/env bash
# setup_test_008.sh — Extract 5-minute segment from test_007 as test_008
#
# Run on the GPU server (where /opt/sila/ and ffmpeg exist):
#   bash /opt/sila/bench/setup_test_008.sh
#
# Extracts minutes 05:00-10:00 from test_007 source video.
# Validates speech content (mean_volume > -30 dB).
# If too silent, tries 10:00-15:00 and 15:00-20:00.

set -euo pipefail

SOURCE_DIR="/opt/sila/projects/test_007"
OUTPUT_DIR="/opt/sila/projects/test_008"
OUTPUT="${OUTPUT_DIR}/source.mp4"

# --- Find source video ---
VIDEO=$(find "${SOURCE_DIR}" -name "*.mp4" -size +50M | head -1)
if [ -z "${VIDEO}" ]; then
    echo "ERROR: No source video >50MB found in ${SOURCE_DIR}"
    echo "Searching broader..."
    VIDEO=$(find /opt/sila/projects/ -path "*/test_007*" -name "*.mp4" -size +50M | head -1)
fi
if [ -z "${VIDEO}" ]; then
    echo "FATAL: Cannot find test_007 source video"
    exit 1
fi
echo "Source: ${VIDEO}"
ffprobe -v quiet -show_format -show_streams "${VIDEO}" 2>&1 | head -30

# --- Extract segment ---
mkdir -p "${OUTPUT_DIR}"

try_extract() {
    local START=$1
    local LABEL=$2
    echo "--- Trying ${LABEL} (start=${START}) ---"

    ffmpeg -y -i "${VIDEO}" \
        -ss "${START}" -t 00:05:00 \
        -c copy \
        "${OUTPUT}" 2>/dev/null

    # Verify duration
    DUR=$(ffprobe -v quiet -show_format "${OUTPUT}" 2>&1 | grep duration | head -1)
    echo "Duration: ${DUR}"

    # Check speech volume
    ffmpeg -y -i "${OUTPUT}" -vn -ar 48000 -ac 1 /tmp/test_008_check.wav 2>/dev/null
    VOLUME=$(ffmpeg -i /tmp/test_008_check.wav -af volumedetect -f null /dev/null 2>&1 | \
        grep mean_volume | awk '{print $5}')
    echo "Mean volume: ${VOLUME} dB"

    # Check if > -30 dB (not too silent)
    if python3 -c "import sys; sys.exit(0 if float('${VOLUME}') > -30 else 1)"; then
        echo "OK: Speech detected (${VOLUME} dB > -30 dB)"
        return 0
    else
        echo "WARN: Too silent (${VOLUME} dB <= -30 dB)"
        return 1
    fi
}

# Try preferred offsets in order
if try_extract "00:05:00" "minutes 05:00-10:00"; then
    SEGMENT_RANGE="05:00-10:00"
elif try_extract "00:10:00" "minutes 10:00-15:00"; then
    SEGMENT_RANGE="10:00-15:00"
elif try_extract "00:15:00" "minutes 15:00-20:00"; then
    SEGMENT_RANGE="15:00-20:00"
else
    echo "WARN: All segments quiet, using 05:00-10:00 anyway"
    ffmpeg -y -i "${VIDEO}" -ss 00:05:00 -t 00:05:00 -c copy "${OUTPUT}" 2>/dev/null
    SEGMENT_RANGE="05:00-10:00"
fi

echo ""
echo "=== test_008 ready ==="
echo "Source: ${OUTPUT}"
echo "Segment: ${SEGMENT_RANGE} from test_007"
ffprobe -v quiet -show_format "${OUTPUT}" 2>&1 | grep -E "duration|size"

# Log
echo "test_008 : extrait 5min de test_007 (YouTube), minutes ${SEGMENT_RANGE}" \
    >> /opt/sila/app/logs/moss_fix.log 2>/dev/null || true

rm -f /tmp/test_008_check.wav
echo "Done. Run pipeline with: python -m src.cli.main --input ${OUTPUT} --target-langs en --tts-engine moss"
