#!/usr/bin/env bash
# Ringkasan pemakaian disk. Jalankan kapan saja:  ./deploy/disk-check.sh
cd "$(dirname "$0")/.."
echo "== Disk"; df -h / | tail -1
echo; echo "== Docker"; docker system df
echo; echo "== Folder project"; du -sh data backups 2>/dev/null
echo; echo "== Versi data KBLI tersimpan: $(ls -1d data/kbli_versions/2* 2>/dev/null | wc -l)"
echo; echo "Kalau sisa < 3 GB:  docker builder prune -af && docker image prune -af"
