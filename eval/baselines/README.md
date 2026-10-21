# Catatan evaluasi — 64 query skripsi (`eval/queries.json`)

Korpus: 1.402 kode KBLI (v2). Metrik: P@1, Recall@5, MRR@5, nDCG@5. Label 4 digit
(`9620`) dihitung sebagai subgolongan, yaitu semua kode 5 digit di bawahnya (= `96200`).

| Run | P@1 | Recall@5 | MRR@5 | nDCG@5 | Catatan |
|---|---|---|---|---|---|
| v2 BM25, sebelum tuning | 0.7969 | 0.9062 | 0.8344 | 0.8520 | **Angka bersih** — tidak ada penyesuaian dari test set |
| v2 BM25, setelah tuning leksikon | 0.8438 | 0.9844 | 0.8951 | 0.9173 | Optimistis: sinonim ditambah setelah melihat kegagalan di set ini |
| v2 IndoSBERT-large, dense per kalimat | 0.672 | 0.859 | 0.739 | 0.769 | strategi awal, kalah dari dokumen utuh |
| v2 IndoSBERT-large, dense dokumen utuh | 0.844 | 0.891 | 0.865 | 0.871 | |
| **v2 hybrid max/linear, w_bm25 0.6** | **0.922** | 0.953 | **0.935** | **0.940** | dipilih via `eval.ablation` di set yang sama (optimistis) |

Per kasus (P@1) — BM25 setelah tuning: 0.89 · 0.89 · 0.72 → hybrid: 0.96 · 0.89 · 0.89 (Variasi Deskripsi · Kode Mirip · Istilah Sehari-hari).

Konfigurasi & bobot dipilih pada 64 query yang sama. Untuk publikasi gunakan k-fold
(pilih konfigurasi di 4/5 data, ukur di 1/5 sisanya) atau set query baru.

**Untuk publikasi, pakai angka "sebelum tuning"**, atau buat set query baru yang belum
pernah dilihat (held-out) untuk melaporkan versi setelah tuning.

## Label yang perlu dicek ulang

| Query (awal) | Label | Jawaban sistem | Catatan |
|---|---|---|---|
| "Cari kode KBLI untuk usaha yang bergerak di bidang jasa desain komunikasi visual…" | 74142 (desain game) | 74130 | Isi query = uraian 74130 |
| "Ini buat jasa desain grafis umum… branding, iklan, dan materi cetak." | 74141 (desain film) | 74130 | Isi query = uraian 74130 |
| "Kepikiran mau cari kode buat studio rekaman yang bisa bikin master lagu…" | 59201 **dan** 59202 | 59201 | Query identik muncul 2x dengan label berbeda; salah satunya pasti salah |

Kalau ketiga label itu dikoreksi, P@1 BM25 naik ±3 poin. Laporkan sebagai koreksi label,
bukan peningkatan model.

## Kegagalan yang tersisa (diharapkan diperbaiki oleh IndoSBERT)

- "jasa foto dan video…" → 26710 / 47731 (industri & dagang alat fotografi) di atas 74201.
  BM25 tidak membedakan jasa vs barang; semantik kalimat yang membedakan.
- "jual software / piranti lunak" → 47413 (eceran piranti lunak) di atas 58200.
  Bisa diperdebatkan: "jualan piranti lunak" memang cocok dengan 47413.
- Query laundry yang menyebut "buat hotel atau salon" tertarik ke 96112 (salon).
