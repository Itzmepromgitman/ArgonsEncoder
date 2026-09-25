#!/usr/bin/env bash
set -euo pipefail

if ! command -v "${FFMPEG_BIN:-ffmpeg}" >/dev/null 2>&1; then
    echo "FFmpeg executable not found: ${FFMPEG_BIN:-ffmpeg}" >&2
    exit 1
fi
if ! command -v "${FFPROBE_BIN:-ffprobe}" >/dev/null 2>&1; then
    echo "ffprobe executable not found: ${FFPROBE_BIN:-ffprobe}" >&2
    exit 1
fi

if [ "${UPDATE_ON_START:-0}" = "1" ]; then
    python3 update.py
fi
exec python3 -m bot
