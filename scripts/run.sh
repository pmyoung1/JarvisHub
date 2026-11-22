#!/usr/bin/env bash
# Foreground run for Linux / macOS (development and the verification suite).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export JARVIS_CONFIG="${JARVIS_CONFIG:-$ROOT/config.json}"
exec python3 -m jarvis.launcher
