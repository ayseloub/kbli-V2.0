# KBLI v2

Pencarian kode KBLI + chatbot hemat token. Retrieval (BM25 + IndoSBERT) yang memutuskan,
template yang menjawab, LLM hanya dipanggil saat user minta penjelasan detail.

## Struktur

```
kbli/        inti bersama: preprocess, lexicon, embedder, retrieval, metrics, config
pipeline/    build_dataset → embed_chunks → load_db   (offline)
eval/        run_eval (P@1, Recall@5, MRR@5, nDCG@5), tune_gate (ambang topik)
api/         FastAPI: engine (routing), templates, llm, ratelimit, store
db/          schema.sql (PostgreSQL 16 + pgvector)
tools/       generator script konsol OSS untuk mengambil uraian resmi
tests/       59 test (preprocessing, API, topic gate)
data/raw/    Excel lampiran + JSON uraian OSS (input)
```

## Menjalankan

```bash
cp .env.example .env                     # isi LLM_API_KEY (mis. Groq) & EMBED_MODEL
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt

# 1. Dataset kanonik (±4 detik setelah cache stemming terbentuk)
python -m pipeline.build_dataset          # cek data/processed/quality_report.md

# 2. Embedding IndoSBERT (butuh akses HuggingFace)
python -m pipeline.embed_chunks --model denaya/indoSBERT-large

# 3. Evaluasi
python -m eval.run_eval --queries eval/queries.json --model denaya/indoSBERT-large
python -m eval.ablation --model denaya/indoSBERT-large   # pilih strategi dense + fusion
python -m eval.calibrate --model denaya/indoSBERT-large  # ambang yakin / luar topik -> .env
python -m eval.tune_gate --model denaya/indoSBERT-large

# 4. Database + API
docker compose up -d db redis
python -m pipeline.load_db
uvicorn api.main:app --reload

pytest                                    # semua test
```

> **Dimensi embedding**: samakan `EMBED_DIM` di `.env` dan `VECTOR(768)` di `db/schema.sql`
> dengan model yang dipakai, sebelum migration pertama. `load_db` menolak dimensi yang tidak cocok.

## Sumber data (`data/raw/`)

| File | Dipakai untuk | Prioritas uraian |
|---|---|---|
| `oss/*.json` | uraian resmi OSS (571 kode) | 1 |
| `dataset_final.xlsx` | uraian gabungan semua sektor (sisa kode) | 2 |
| `lampiran/*.xlsx` | judul + ruang lingkup per sektor → `kbli_scopes` | 3 (cadangan) |

`build_dataset` membersihkan sel rusak di `dataset_final.xlsx` (`_x000D_`, "Seluruh" yang
tertempel, catatan ruang lingkup) dan menggabungkan baris dobel: varian artefak dibuang,
subklasifikasi berbeda (mis. 71102 IG001–IG005) digabung. Teks yang bertentangan dengan
OSS dicatat di `quality_report.md` untuk dicek manual.

Kalau nanti perlu mengambil ulang uraian dari OSS: `python -m tools.make_oss_script`,
jalankan `tools/ambil_uraian.js` di Console tab `https://oss.go.id`, simpan JSON-nya ke `data/raw/oss/`.

## Alur chat (`api/engine.py`)

| Situasi | Deteksi | Jawaban | LLM? |
|---|---|---|---|
| Sapaan / terima kasih | regex | template | – |
| Pertanyaan umum ("apa itu…", "terjemahkan…") | regex tanpa kata usaha | template tolak | – |
| Luar topik | coverage kosakata < ambang (+ skor dense) | template tolak | – |
| Yakin | margin skor top-1 vs top-2 | template + 2 alternatif | – |
| Ragu | selain di atas | tombol 3 pilihan (`select`) | – |
| Klik "jelaskan" | action `explain` | LLM, kode divalidasi | ya, potong kuota |

Klik `select` disimpan sebagai `message_feedback.selected_kode` → pasangan (query, kode)
untuk fine-tuning IndoSBERT nanti.

## Limit (`api/ratelimit.py`, Redis)

- `RATE_LIMIT_PER_MINUTE` — semua pesan, anti spam (429)
- `LLM_QUOTA_ANON_PER_DAY` / `LLM_QUOTA_USER_PER_DAY` — jatah penjelasan LLM, reset 00.00 WIB
- `LLM_GLOBAL_PER_DAY` — budget seluruh aplikasi; habis → semua user ke mode template
- Kuota dikembalikan kalau panggilan LLM gagal atau LLM menyebut kode di luar data.

## Status & yang belum

- [x] Preprocessing dua jalur + test kasus nyata
- [x] Dataset kanonik 1.402 kode, semuanya dengan uraian
- [x] Hybrid retrieval + RRF, confidence routing, topic gate (BM25: 92,5% di 40 sampel)
- [x] API chat/search/feedback, rate limit, kuota LLM, validasi output LLM
- [ ] Embedding IndoSBERT + kalibrasi ambang dense (jalankan di mesin dengan akses HuggingFace)
- [x] 64 query skripsi → `eval/queries.json`; BM25 v2 P@1 0.797 (bersih) — lihat `eval/baselines/README.md`
- [x] IndoSBERT-large hybrid (max/linear/0.6): P@1 0.922, MRR@5 0.935 di 64 query skripsi
- [ ] Kalibrasi ambang confidence (`eval.calibrate`) dan isi `.env`
- [x] 5 uraian bermasalah dikoreksi manual (`data/raw/overrides.json`)
- [ ] Auth (JWT) — sementara semua user dianggap tamu (cookie + IP)
- [ ] Knowledge base dokumen: worker ingestion + `search_documents`
- [ ] Frontend Next.js & admin dashboard

## Frontend

Lihat `web/README.md` (Next.js, `npm run dev` lalu buka http://localhost:3000).

## Login & admin

```bash
# buat akun admin pertama (password ditanya di terminal)
python -m api.create_admin --email kamu@email.com --name "Aza"
```

- Tamu: jatah penjelasan AI 5/hari. User login: 20/hari.
- Buka http://localhost:3000/admin (login dulu di /masuk).
  - **Ringkasan**: jumlah pencarian, pengunjung, 👍/👎, panggilan AI, kode terpopuler.
  - **Log pencarian**: filter "Pilih bukan #1", "Dapat 👎", "Ditolak" untuk menemukan query yang perlu diperbaiki.
  - **Pengguna**: ubah peran (user/editor/admin) atau nonaktifkan akun.
- `JWT_SECRET` di `.env` wajib diisi string acak ≥ 32 karakter.

## Knowledge base dokumen

Admin/editor upload PP, Permen, FAQ, atau SOP di **/admin → Dokumen**. Chatbot lalu bisa menjawab
pertanyaan aturan ("syarat izin usaha risiko rendah apa?") dari dokumen itu.

1. Upload (PDF teks, DOCX, TXT, MD; maks 25 MB). File yang sama tidak bisa di-upload dua kali.
2. Worker di dalam proses API mengekstrak teks, membuang header/footer & nomor halaman, memotong per
   BAB/Pasal (±280 kata, overlap 40 kata), lalu membuat embedding dengan `BAAI/bge-m3`.
3. Cek "Lihat potongan", lalu centang **Aktif**. Hanya dokumen aktif yang dipakai chatbot.

Di chat, pertanyaan aturan dijawab dengan **kutipan asli + sumber** (BAB › Pasal, halaman) tanpa LLM.
Tombol "Rangkum pakai AI" memakai jatah AI dan jawabannya wajib menyertakan rujukan [n] yang valid.
Pertanyaan yang menyebut "kode"/"KBLI" tetap masuk alur KBLI.

- Pertama kali dipakai, bge-m3 diunduh ±2,3 GB dan butuh ±2,5 GB RAM.
- Setelah ada dokumen aktif, setel ambang relevansi: `python -m eval.doc_calibrate`
- PDF hasil scan ditolak dengan pesan jelas (butuh OCR). Lampiran berupa tabel besar sebaiknya
  tidak ikut di-upload; upload batang tubuh peraturannya saja.
- Test Postgres pakai database terpisah (test ini menghapus data, dan hanya mau jalan kalau nama DB-nya
  mengandung "test"):
  `docker compose exec db createdb -U kbli kbli_test && docker compose exec -T db psql -U kbli -d kbli_test < db/schema.sql`
  `DATABASE_URL=postgresql://kbli:kbli@localhost:55432/kbli_test python -m pipeline.load_db`
  `TEST_DATABASE_URL=postgresql://kbli:kbli@localhost:55432/kbli_test python -m pytest -q`

## Dipasang di bawah website utama (`domain/chat`)

Frontend memakai `basePath` dari `NEXT_PUBLIC_BASE_PATH` (default `/chat`), jadi semua halaman
dan API berada di bawah satu path dan tidak bentrok dengan website utama:

| URL | Isi |
|---|---|
| `domain/chat` | halaman chat publik (tanpa login) |
| `domain/chat/admin` | login & dashboard admin (tidak ditautkan dari halaman publik) |
| `domain/chat/api/...` | backend FastAPI (diteruskan Next.js) |

1. Di `web/.env.local` set `NEXT_PUBLIC_BASE_PATH`, `KBLI_API_URL`, dan `NEXT_PUBLIC_HOME_URL`
   (tujuan tombol ← di header chat). **Nilai ini dibakar saat `npm run build`**, jadi build ulang
   kalau mengubahnya.
2. `npm run build && npm run start` (port 3000).
3. Tambahkan blok `location /chat` dari `deploy/nginx-chat.conf` ke Nginx domain yang sudah ada.
4. Arahkan tombol "Mulai Chat" di website utama ke `/chat`.

Kalau aplikasi diberi subdomain sendiri (mis. `chat.domain.go.id`), kosongkan `NEXT_PUBLIC_BASE_PATH=`.

## Data KBLI dari dashboard

Tab **Data KBLI** di admin:
- Cari & edit judul / uraian / kata kunci per kode, tambah kode baru, hapus kode (admin).
  Perubahan langsung dipakai pencarian tanpa restart.
- **Unduh Excel** → ubah di Excel → **Impor Excel**: muncul pratinjau (baru / berubah / dihapus)
  sebelum diterapkan. Embedding hanya dihitung ulang untuk kode yang judul/uraiannya berubah.
- **Riwayat versi**: setiap perubahan disimpan (30 versi terakhir, ±9 MB per versi) di
  `KBLI_VERSIONS_DIR` (default `data/kbli_versions`) dan bisa dipulihkan. "Data awal" = hasil pipeline.
- Kalau uvicorn dijalankan dengan beberapa worker, worker lain memuat versi baru dalam ±5 detik.
