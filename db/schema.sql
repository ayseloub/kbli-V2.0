-- =====================================================================
-- KBLI v2 — PostgreSQL schema
-- Butuh: PostgreSQL 15+ dengan ekstensi pgvector (>= 0.5 untuk HNSW)
--
-- Domain:
--   1. KBLI (data terstruktur)       kbli_codes, kbli_scopes, kbli_chunks
--   2. Knowledge base (dokumen)      documents, document_chunks, ingestion_jobs
--   3. Pengguna & chat               users, chat_sessions, chat_messages,
--                                    retrieval_logs, message_feedback
--   4. Evaluasi                      eval_queries, eval_runs
--
-- Catatan dimensi embedding:
--   VECTOR(256)  -> denaya/indoSBERT-large (dim 256). Ganti kalau model berbeda
--   VECTOR(1024) -> sesuaikan dengan model dokumen (mis. bge-m3)
--   Kalau ganti model dengan dimensi beda, buat migration baru + re-embed.
--
-- Catatan teks pencarian (BM25 / full-text):
--   Kolom *_search_text diisi oleh pipeline Python (lowercase + Sastrawi
--   stemming + stopword removal), lalu di-index pakai konfigurasi 'simple'.
--   Jadi preprocessing keyword tetap satu sumber (Python), bukan dua.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- pencarian judul yang toleran typo

-- ---------------------------------------------------------------------
-- Enum
-- ---------------------------------------------------------------------
CREATE TYPE user_role          AS ENUM ('admin', 'editor', 'user');
CREATE TYPE kbli_chunk_type    AS ENUM ('judul', 'uraian', 'pengecualian', 'ruang_lingkup');
CREATE TYPE document_status    AS ENUM ('queued', 'parsing', 'embedding', 'ready', 'failed');
CREATE TYPE document_visibility AS ENUM ('public', 'internal');
CREATE TYPE job_status         AS ENUM ('queued', 'running', 'succeeded', 'failed');
CREATE TYPE message_role       AS ENUM ('user', 'assistant', 'tool', 'system');

-- ---------------------------------------------------------------------
-- Util: updated_at otomatis
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- 3a. Users (dibuat duluan karena direferensikan tabel lain)
-- =====================================================================
CREATE TABLE users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  password_hash  TEXT NOT NULL,
  role           user_role NOT NULL DEFAULT 'user',
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
-- 2a. Documents (dibuat sebelum KBLI karena kbli_scopes merujuk sumbernya)
-- =====================================================================
CREATE TABLE documents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title           TEXT NOT NULL,
  description     TEXT,
  original_filename TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  storage_path    TEXT NOT NULL,                 -- path di disk / object storage
  file_sha256     CHAR(64) NOT NULL,             -- cegah upload ganda
  page_count      INT,

  -- Versi & masa berlaku (regulasi sering direvisi)
  version         INT NOT NULL DEFAULT 1,
  supersedes_id   UUID REFERENCES documents(id) ON DELETE SET NULL,
  effective_from  DATE,
  effective_until DATE,

  status          document_status NOT NULL DEFAULT 'queued',
  status_message  TEXT,                          -- pesan error kalau 'failed'
  is_active       BOOLEAN NOT NULL DEFAULT FALSE,-- admin aktifkan setelah dicek
  visibility      document_visibility NOT NULL DEFAULT 'public',
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb, -- mis. nomor PP, sektor

  uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT documents_sha_version_uniq UNIQUE (file_sha256, version),
  CONSTRAINT documents_effective_range CHECK (
    effective_until IS NULL OR effective_from IS NULL OR effective_until >= effective_from)
);
CREATE INDEX documents_active_idx ON documents (is_active, status);
CREATE TRIGGER trg_documents_updated BEFORE UPDATE ON documents
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
-- 1. KBLI
-- =====================================================================

-- Satu baris = satu kode KBLI (kanonik, sudah di-dedupe)
CREATE TABLE kbli_codes (
  kode            CHAR(5) PRIMARY KEY CHECK (kode ~ '^[0-9]{5}$'),
  judul           TEXT NOT NULL,
  uraian          TEXT,                          -- uraian resmi dari OSS
  uraian_source   TEXT NOT NULL DEFAULT 'oss',   -- 'oss' | 'lampiran' | 'manual'
  oss_detail_uuid UUID,                          -- UUID halaman detail OSS
  kata_kunci      TEXT[] NOT NULL DEFAULT '{}',  -- sinonim / istilah awam tambahan
  sektor          TEXT[] NOT NULL DEFAULT '{}',  -- sektor lampiran yang memuat kode ini
  kbli_version    SMALLINT NOT NULL DEFAULT 2020,

  -- Hierarki turunan dari kode (golongan pokok 2 digit, golongan 3, subgolongan 4)
  golongan_pokok  CHAR(2) GENERATED ALWAYS AS (substr(kode, 1, 2)) STORED,
  golongan        CHAR(3) GENERATED ALWAYS AS (substr(kode, 1, 3)) STORED,
  subgolongan     CHAR(4) GENERATED ALWAYS AS (substr(kode, 1, 4)) STORED,
  kategori        CHAR(1),                       -- huruf A–U, diisi dari tabel referensi

  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX kbli_codes_golongan_idx ON kbli_codes (golongan);
CREATE INDEX kbli_codes_judul_trgm   ON kbli_codes USING gin (judul gin_trgm_ops);
CREATE TRIGGER trg_kbli_codes_updated BEFORE UPDATE ON kbli_codes
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Ruang lingkup + data perizinan per sektor (dari Lampiran I PP).
-- Satu kode bisa punya beberapa baris: beda sektor / beda ruang lingkup /
-- beda skala usaha. Kolom perizinan disiapkan untuk fase "izin apa yang
-- dibutuhkan" — boleh NULL dulu.
CREATE TABLE kbli_scopes (
  id                 BIGSERIAL PRIMARY KEY,
  kode               CHAR(5) NOT NULL REFERENCES kbli_codes(kode) ON DELETE CASCADE,
  sektor             TEXT NOT NULL,              -- 'pertanian', 'perindustrian', ...
  ruang_lingkup      TEXT NOT NULL,              -- 'Seluruh', 'Khusus Animasi', 'Selain: ...'
  is_seluruh         BOOLEAN GENERATED ALWAYS AS (lower(btrim(ruang_lingkup)) IN ('seluruh', 'seluruhnya')) STORED,

  skala_usaha        TEXT[],                     -- {'Mikro','Kecil','Menengah','Besar'}
  tingkat_risiko     TEXT,
  perizinan_berusaha TEXT,
  persyaratan        TEXT,
  jangka_waktu       TEXT,
  kewajiban          TEXT,
  pb_umku            TEXT,
  parameter          TEXT,
  kewenangan         TEXT,

  source_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
  source_page        INT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX kbli_scopes_kode_idx   ON kbli_scopes (kode);
CREATE INDEX kbli_scopes_sektor_idx ON kbli_scopes (sektor);

-- Unit pencarian KBLI. Uraian dipecah per kalimat supaya tidak terpotong
-- batas token SBERT; skor per kode = max dari chunk-chunknya.
-- Kalimat pengecualian ("...dimasukkan dalam kelompok X") disimpan dengan
-- chunk_type='pengecualian' dan TIDAK diikutkan saat retrieval biasa.
CREATE TABLE kbli_chunks (
  id               BIGSERIAL PRIMARY KEY,
  kode             CHAR(5) NOT NULL REFERENCES kbli_codes(kode) ON DELETE CASCADE,
  chunk_type       kbli_chunk_type NOT NULL,
  chunk_index      INT NOT NULL,
  content          TEXT NOT NULL,                -- teks asli (untuk ditampilkan)
  embed_text       TEXT NOT NULL,                -- teks yang di-embed (pembersihan minimal)
  search_text      TEXT NOT NULL,                -- teks untuk BM25 (stemmed, tanpa stopword)
  search_tsv       TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
  embedding        VECTOR(256),
  embedding_model  TEXT,                         -- nama model + versi, untuk audit
  weight           REAL NOT NULL DEFAULT 1.0,    -- mis. judul diberi bobot > 1
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT kbli_chunks_uniq UNIQUE (kode, chunk_type, chunk_index)
);
CREATE INDEX kbli_chunks_kode_idx ON kbli_chunks (kode);
CREATE INDEX kbli_chunks_tsv_idx  ON kbli_chunks USING gin (search_tsv);
CREATE INDEX kbli_chunks_emb_idx  ON kbli_chunks
  USING hnsw (embedding vector_cosine_ops)
  WHERE chunk_type <> 'pengecualian';

-- =====================================================================
-- 2b. Knowledge base: chunk dokumen & job ingestion
-- =====================================================================
CREATE TABLE document_chunks (
  id              BIGSERIAL PRIMARY KEY,
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index     INT NOT NULL,
  page_start      INT,
  page_end        INT,
  heading_path    TEXT[],                        -- mis. {'BAB II','Pasal 5'}
  content         TEXT NOT NULL,
  embed_text      TEXT NOT NULL,
  search_text     TEXT NOT NULL,
  search_tsv      TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
  token_count     INT,
  embedding       VECTOR(1024),
  embedding_model TEXT,
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb, -- mis. {"is_table": true}
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT document_chunks_uniq UNIQUE (document_id, chunk_index)
);
CREATE INDEX document_chunks_doc_idx ON document_chunks (document_id);
CREATE INDEX document_chunks_tsv_idx ON document_chunks USING gin (search_tsv);
CREATE INDEX document_chunks_emb_idx ON document_chunks
  USING hnsw (embedding vector_cosine_ops);

-- Antrian proses upload (parse -> chunk -> embed). Dikerjakan worker.
CREATE TABLE ingestion_jobs (
  id            BIGSERIAL PRIMARY KEY,
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  status        job_status NOT NULL DEFAULT 'queued',
  stage         TEXT,                            -- 'parsing' | 'chunking' | 'embedding'
  progress      REAL NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
  attempts      INT NOT NULL DEFAULT 0,
  error         TEXT,
  queued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ
);
CREATE INDEX ingestion_jobs_status_idx ON ingestion_jobs (status, queued_at);

-- =====================================================================
-- 3b. Chat
-- =====================================================================
CREATE TABLE chat_sessions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID REFERENCES users(id) ON DELETE SET NULL, -- NULL = tamu
  anon_id       TEXT,                            -- id cookie untuk tamu
  title         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chat_sessions_owner CHECK (user_id IS NOT NULL OR anon_id IS NOT NULL)
);
CREATE INDEX chat_sessions_user_idx ON chat_sessions (user_id, updated_at DESC);
CREATE TRIGGER trg_chat_sessions_updated BEFORE UPDATE ON chat_sessions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE chat_messages (
  id            BIGSERIAL PRIMARY KEY,
  session_id    UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          message_role NOT NULL,
  content       TEXT,
  tool_name     TEXT,                            -- 'search_kbli' | 'search_documents'
  tool_payload  JSONB,                           -- argumen / hasil tool call
  llm_provider  TEXT,
  llm_model     TEXT,
  tokens_in     INT,
  tokens_out    INT,
  latency_ms    INT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX chat_messages_session_idx ON chat_messages (session_id, id);

-- Jejak retrieval per tool call: bahan debug, evaluasi, dan fine-tuning.
CREATE TABLE retrieval_logs (
  id            BIGSERIAL PRIMARY KEY,
  message_id    BIGINT REFERENCES chat_messages(id) ON DELETE CASCADE,
  source        TEXT NOT NULL CHECK (source IN ('kbli', 'documents')),
  query_text    TEXT NOT NULL,
  config        JSONB NOT NULL,                  -- bobot fusion, model, top_k, dll.
  results       JSONB NOT NULL,                  -- [{"kode":"47911","score":0.82,"bm25":..,"dense":..}]
  latency_ms    INT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX retrieval_logs_created_idx ON retrieval_logs (created_at DESC);

-- Feedback user: rating + kode yang akhirnya dipilih
-- -> pasangan (query, kode) untuk fine-tuning IndoSBERT nanti.
CREATE TABLE message_feedback (
  id             BIGSERIAL PRIMARY KEY,
  message_id     BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  rating         SMALLINT CHECK (rating IN (-1, 1)),
  selected_kode  CHAR(5) REFERENCES kbli_codes(kode) ON DELETE SET NULL,
  comment        TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT message_feedback_one_per_message UNIQUE (message_id)
);

-- =====================================================================
-- 4. Evaluasi
-- =====================================================================
CREATE TABLE eval_queries (
  id             BIGSERIAL PRIMARY KEY,
  query_text     TEXT NOT NULL,
  expected_kodes CHAR(5)[] NOT NULL,             -- kode yang dianggap benar
  source         TEXT NOT NULL DEFAULT 'skripsi',-- 'skripsi' | 'feedback' | 'manual'
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
  id             BIGSERIAL PRIMARY KEY,
  name           TEXT NOT NULL,                  -- mis. 'v1-indosbert', 'v2-hybrid-rrf'
  config         JSONB NOT NULL,
  metrics        JSONB NOT NULL,                 -- {"p@1":..,"recall@5":..,"mrr@5":..,"ndcg@5":..}
  per_query      JSONB,                          -- hasil per query untuk analisis error
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- View bantu
-- =====================================================================

-- Dokumen yang boleh dipakai chatbot saat ini
CREATE VIEW active_documents AS
SELECT *
FROM documents
WHERE is_active
  AND status = 'ready'
  AND (effective_from  IS NULL OR effective_from  <= CURRENT_DATE)
  AND (effective_until IS NULL OR effective_until >= CURRENT_DATE);

-- Ringkasan KBLI siap tampil (kode + judul + uraian + daftar ruang lingkup)
CREATE VIEW kbli_overview AS
SELECT c.kode,
       c.judul,
       c.uraian,
       COALESCE(
         jsonb_agg(jsonb_build_object('sektor', s.sektor, 'ruang_lingkup', s.ruang_lingkup)
                   ORDER BY s.sektor) FILTER (WHERE s.id IS NOT NULL),
         '[]'::jsonb) AS ruang_lingkup
FROM kbli_codes c
LEFT JOIN kbli_scopes s ON s.kode = c.kode
WHERE c.is_active
GROUP BY c.kode;
