#!/usr/bin/env bash
# Backup database + data penting. Disetel untuk disk kecil:
#   - hanya versi data KBLI yang AKTIF yang ikut (bukan 30 versi), plus dokumen upload
#   - simpan KEEP_DAYS hari (default 4); kalau sisa disk < 2 GB, backup tertua dihapus duluan
# Jalankan dari folder project. Otomatis tiap jam 02.00 lewat cron (lihat DEPLOY.md).
# Salinan di luar server tetap wajib (scp ke Mac / Google Drive) — backup di disk yang sama
# tidak menolong kalau VPS-nya rusak.
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP_DAYS=${KEEP_DAYS:-4}
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M)

docker compose -f docker-compose.prod.yml exec -T db pg_dump -U kbli -Fc kbli > "backups/db-$STAMP.dump"

PATHS=(data/processed)
[ -d data/uploads ] && PATHS+=(data/uploads)
if [ -f data/kbli_versions/CURRENT ]; then
  CUR=$(cat data/kbli_versions/CURRENT)
  PATHS+=(data/kbli_versions/CURRENT "data/kbli_versions/$CUR")
fi
tar -czf "backups/data-$STAMP.tar.gz" "${PATHS[@]}"

find backups -name 'db-*.dump' -mtime +"$KEEP_DAYS" -delete
find backups -name 'data-*.tar.gz' -mtime +"$KEEP_DAYS" -delete

# pengaman disk: sisakan minimal 2 GB
while [ "$(df --output=avail -k / | tail -1)" -lt 2097152 ]; do
  OLD=$(ls -1t backups/*-*.{dump,tar.gz} 2>/dev/null | tail -1 || true)
  [ -z "$OLD" ] || [ "$OLD" = "backups/db-$STAMP.dump" ] && break
  echo "Disk hampir penuh, hapus $OLD"; rm -f "$OLD"
done

echo "$(date) backup OK: db-$STAMP.dump ($(du -h backups/db-$STAMP.dump | cut -f1)), data-$STAMP.tar.gz ($(du -h backups/data-$STAMP.tar.gz | cut -f1)) | sisa disk: $(df -h / | awk 'NR==2{print $4}')"
