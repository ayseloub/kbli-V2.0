#!/usr/bin/env bash
# Pasang versi kode terbaru (setelah git pull / upload kode baru). Data & database tidak tersentuh.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .git ]; then git pull --ff-only; fi
docker compose -f docker-compose.prod.yml up -d --build
# disk kecil: buang cache build & image lama (±2–3 GB) setelah build selesai
docker builder prune -af
docker image prune -af
df -h / | tail -1
docker compose -f docker-compose.prod.yml ps
