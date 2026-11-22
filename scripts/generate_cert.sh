#!/usr/bin/env bash
# Optional self-signed certificate for HTTPS (needed for the Web Speech
# mic button on most phones). Typing / keyboard dictation works on HTTP.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/certs"
mkdir -p "$DIR"
# Include hostname; add extra SAN names as arguments if you like.
CN="${1:-jarvis-hub}"
openssl req -x509 -newkey rsa:2048 \
  -keyout "$DIR/key.pem" -out "$DIR/cert.pem" \
  -days 1825 -nodes \
  -subj "/CN=${CN}" \
  -addext "subjectAltName=DNS:${CN},DNS:localhost,IP:127.0.0.1"
chmod 600 "$DIR/key.pem"
echo "Wrote $DIR/cert.pem and $DIR/key.pem"
echo "Set ssl.enabled to true in config.json and restart the hub."
