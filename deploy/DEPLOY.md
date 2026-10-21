# Deploy ke VPS (IDCloudHost) dari nol

Hasil akhir: `https://DOMAIN/chat` (chat publik) dan `https://DOMAIN/chat/admin` (admin),
HTTPS otomatis, database & Redis tidak terbuka ke internet, backup harian.

```
Internet ──443──▶ Caddy (HTTPS) ──/chat*──▶ web (Next.js :3000) ──/chat/api──▶ api (FastAPI :8000)
                                                                              ├─ db (Postgres + pgvector)
                                                                              └─ redis
```

---

## 0. Siapkan dulu

- Akun IDCloudHost + saldo.
- Domain/subdomain, mis. `kbli.domainmu.id`. Belum punya? Untuk uji coba bisa pakai
  `IP-VPS.sslip.io` (mis. `103.10.20.30.sslip.io`), HTTPS tetap jalan.
- API key Groq (`gsk_...`).
- Di Mac: folder project yang sudah jalan, **termasuk** `data/processed/*.npy` (embedding IndoSBERT).
- SSH key di Mac. Belum ada? `ssh-keygen -t ed25519` lalu Enter terus. Kunci publiknya:
  `cat ~/.ssh/id_ed25519.pub`

## 1. Buat VPS

Di panel IDCloudHost → **Cloud VPS** → buat baru:

| Pilihan | Isi |
|---|---|
| OS | **Ubuntu 24.04 LTS** |
| Spesifikasi | **4 vCPU, 8 GB RAM, disk 20 GB** (lebih lega: 40–80 GB) |
| Lokasi | Jakarta |
| Autentikasi | **SSH key** (tempel isi `id_ed25519.pub`) |

Kenapa 8 GB: IndoSBERT-large ±1,5 GB + bge-m3 (dokumen) ±2,5 GB + Postgres/Next/Redis ±1 GB.

**Disk 20 GB** cukup kalau mengikuti setelan hemat di panduan ini (swap 2 GB, cache build dibuang
setelah build, backup 4 hari, 10 versi data KBLI). Perkiraan setelah semua jalan: ±15 GB terpakai.
Pantau dengan `./deploy/disk-check.sh`. Disk biasanya bisa ditambah belakangan dari panel tanpa install ulang.

Catat **IP publik** VPS.

## 2. Arahkan domain

Di pengelola DNS domainmu, buat record:

```
Tipe A   nama: kbli (atau @)   nilai: <IP-VPS>   TTL: 300
```

Cek dari Mac (tunggu sampai muncul IP VPS): `dig +short kbli.domainmu.id`

## 3. Push project ke GitHub (dari Mac, sekali)

Buat repo **private** baru di GitHub, mis. `kbli-chatbot` (tanpa README). Lalu di folder project:

```bash
git init -b main            # lewati kalau folder ini sudah repo git
git add .
git status                  # pastikan .env, .venv, node_modules TIDAK ikut
git commit -m "KBLI chatbot v2"
git remote add origin git@github.com:<username-github>/kbli-chatbot.git
git push -u origin main
```

Embedding `data/processed/*.npy` (±5,5 MB) memang sengaja ikut, jadi server tidak perlu menghitung ulang.

## 4. Masuk ke VPS, ambil kode, siapkan server (sekali)

`<user>` = username yang kamu isi waktu membuat VPS (mis. `bkpmvps`).

```bash
ssh-keygen -R <IP-VPS>          # di Mac; perlu kalau VPS baru di-reinstall
ssh <user>@<IP-VPS>             # ketik yes
```

Di VPS, buat **deploy key** supaya VPS boleh membaca repo private:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_kbli -N "" -C "vps-kbli"
printf 'Host github.com\n  IdentityFile ~/.ssh/github_kbli\n  IdentitiesOnly yes\n' >> ~/.ssh/config
cat ~/.ssh/github_kbli.pub
```

Salin isinya ke GitHub → repo → **Settings → Deploy keys → Add deploy key**
("Allow write access" **tidak** dicentang). Lalu masih di VPS:

```bash
command -v git || sudo apt-get install -y git   # biasanya sudah ada di Ubuntu
ssh -T git@github.com                       # ketik yes; harus "successfully authenticated"
git clone git@github.com:<username-github>/kbli-chatbot.git ~/kbli-v2
sudo NEWUSER=$USER bash ~/kbli-v2/deploy/setup-server.sh
exit                                        # keluar lalu login lagi supaya grup docker aktif
```

`setup-server.sh` memasang Docker, firewall (hanya 22/80/443), swap 2 GB, zona waktu WIB.
(Disk ≥ 40 GB? pakai `sudo SWAP_GB=4 NEWUSER=$USER bash ...`.)

## 5. Isi konfigurasi

Di VPS:

```bash
cd ~/kbli-v2
cp deploy/env.production.example .env
openssl rand -hex 24      # salin -> POSTGRES_PASSWORD
openssl rand -base64 48   # salin -> JWT_SECRET
nano .env                 # isi DOMAIN, POSTGRES_PASSWORD, JWT_SECRET, LLM_API_KEY, HOME_URL
chmod 600 .env
```

## 6. Jalankan

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Pertama kali ±10–20 menit (build image + unduh IndoSBERT). Setelah selesai, buang cache build
(±2–3 GB) supaya disk 20 GB tetap lega:

```bash
docker builder prune -af && docker image prune -af
./deploy/disk-check.sh
```

Pantau:

```bash
docker compose -f docker-compose.prod.yml logs -f api     # tunggu "Application startup complete"
docker compose -f docker-compose.prod.yml ps              # semua harus "running"/"healthy"
```

## 7. Isi database & buat admin (sekali)

```bash
docker compose -f docker-compose.prod.yml exec api python -m pipeline.load_db
docker compose -f docker-compose.prod.yml exec api python -m api.create_admin --email emailmu@gmail.com --name "Aza"
```

## 8. Coba

- `https://DOMAIN/chat` → chat publik
- `https://DOMAIN/chat/admin` → login admin
- `https://DOMAIN/chat/api/health` → `{"status":"ok", ... "dense": true}`

Upload dokumen pertama memicu unduhan bge-m3 (±2,3 GB), jadi dokumen pertama butuh beberapa menit.
Setelah ada dokumen aktif, setel ambang relevansinya:

```bash
docker compose -f docker-compose.prod.yml exec api python -m eval.doc_calibrate
# masukkan angka saran ke DOC_MIN_SCORE di .env, lalu:
docker compose -f docker-compose.prod.yml up -d api
```

## 9. Backup otomatis

```bash
cd ~/kbli-v2 && ./deploy/backup.sh       # coba sekali
(crontab -l 2>/dev/null; echo "0 2 * * * cd $HOME/kbli-v2 && ./deploy/backup.sh >> backups/backup.log 2>&1") | crontab -
```

Tiap jam 02.00: dump database + versi data KBLI yang aktif + dokumen upload, disimpan **4 hari** di
`~/kbli-v2/backups` (atur dengan `KEEP_DAYS=7 ./deploy/backup.sh`). Kalau sisa disk < 2 GB, backup
tertua otomatis dihapus.

Karena backup ada di disk yang sama dengan server, **wajib** disalin keluar secara rutin (mis. seminggu
sekali dari Mac):

```bash
rsync -avz <user>@<IP-VPS>:~/kbli-v2/backups/ ./backup-vps/
```

Pulihkan database: `docker compose -f docker-compose.prod.yml exec -T db pg_restore -U kbli -d kbli --clean < backups/db-XXXX.dump`

## 10. Deploy otomatis tiap `git push` (GitHub Actions)

Workflow `.github/workflows/deploy.yml` menjalankan test backend + typecheck frontend, lalu masuk ke VPS
dan menjalankan `./deploy/update.sh` (git pull + rebuild). Kalau test gagal, server tidak disentuh.

Sekali saja, di **Mac**, buat kunci khusus untuk GitHub Actions:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/kbli_actions -N "" -C "github-actions-kbli"
ssh-copy-id -i ~/.ssh/kbli_actions.pub <user>@<IP-VPS>     # izinkan kunci ini masuk ke VPS
cat ~/.ssh/kbli_actions                                     # private key -> secret VPS_SSH_KEY
```

Di GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Nama | Isi |
|---|---|
| `VPS_HOST` | IP VPS, mis. `103.176.79.204` |
| `VPS_USER` | user di VPS, mis. `bkpmvps` |
| `VPS_SSH_KEY` | seluruh isi `~/.ssh/kbli_actions` (termasuk baris BEGIN/END) |

Mulai sekarang:

```bash
git add . && git commit -m "perubahan" && git push      # dari Mac -> otomatis ter-deploy
```

Progresnya terlihat di tab **Actions** repo. Deploy manual tetap bisa: tab Actions → deploy → Run workflow,
atau di VPS `cd ~/kbli-v2 && ./deploy/update.sh`.

Data, database, versi KBLI hasil edit dashboard, dan dokumen upload **tidak tersentuh** oleh deploy
(semuanya ada di volume Docker dan folder yang di-ignore Git).

## 11. Hubungkan dengan website utama (FE lama)

- Tombol "Mulai Chat" / "Mulai Konsultasi" → `https://DOMAIN/chat`
- Isi `HOME_URL=https://website-utama` di `.env` supaya tombol ← di chat kembali ke sana, lalu
  `docker compose -f docker-compose.prod.yml up -d --build web` (nilai ini dibakar saat build).
- Kalau nanti chat harus tampil di domain website utama (`websiteutama/chat`), pakai blok Nginx di
  `deploy/nginx-chat.conf` di server website utama dan arahkan ke VPS ini.

---

## Kalau ada masalah

| Gejala | Cek |
|---|---|
| Browser: sertifikat tidak valid / tidak bisa dibuka | `dig +short DOMAIN` harus = IP VPS; `docker compose ... logs caddy` |
| 502 Bad Gateway | API masih memuat model; `docker compose ... logs -f api` |
| API restart terus / "Killed" | RAM kurang: `docker stats`, `free -h`. Matikan dokumen (`DOCS_ENABLED=false`) atau naikkan RAM |
| `no space left on device` | `./deploy/disk-check.sh`, lalu `docker builder prune -af && docker image prune -af`; hapus backup lama; tambah disk dari panel |
| `dense: false` di /health | File `data/processed/*.npy` tidak ikut ter-push: cek `git ls-files data/processed` |
| Penjelasan AI selalu "tidak tersedia" | `LLM_API_KEY` salah / kuota Groq habis: `docker compose ... logs api | grep LLM` |
| Lupa password admin | `docker compose ... exec api python -m api.create_admin --email baru@x.id` lalu nonaktifkan akun lama di tab Pengguna |

Perintah berguna:

```bash
alias dc='docker compose -f ~/kbli-v2/docker-compose.prod.yml'
dc ps            # status
dc logs -f api   # log backend
dc restart api   # restart backend (mis. setelah ubah .env)
dc down          # matikan semua (data tetap aman di volume)
```
