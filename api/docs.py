"""Knowledge base dokumen: repositori Postgres, worker ingestion, dan pencarian hybrid.

Alur upload:
  admin upload -> file disimpan di UPLOAD_DIR -> baris `documents` (status queued, nonaktif)
  -> `ingestion_jobs` (queued) -> IngestWorker (thread di proses API) mengambil job dengan
  SELECT ... FOR UPDATE SKIP LOCKED -> ekstrak, bersihkan, potong, embedding -> status ready.
  Admin mengecek potongannya lalu mengaktifkan dokumen. Hanya dokumen aktif (view
  `active_documents`) yang dipakai chatbot.

Pencarian: gabungan full-text Postgres (to_tsvector 'simple') dan pgvector (cosine),
digabung dengan Reciprocal Rank Fusion.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from kbli.config import Settings
from kbli.documents import DocumentError, chunk_document, clean_pages, extract, fts_query
from kbli.embedder import make_embedder

log = logging.getLogger("kbli.docs")


def _vec(v) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


class DocEmbedder:
    """Model embedding dokumen dimuat sekali, saat pertama kali dibutuhkan (hemat RAM saat start)."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._model = None
        self._lock = threading.Lock()

    def get(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    log.info("Memuat model embedding dokumen %s …", self.s.doc_embed_model)
                    m = make_embedder(self.s.doc_embed_model)
                    if getattr(m, "model", None) is not None and hasattr(m.model, "max_seq_length"):
                        m.model.max_seq_length = 512
                    if m.dim != self.s.doc_embed_dim:
                        raise RuntimeError(f"Dimensi {self.s.doc_embed_model} = {m.dim}, "
                                           f"tapi DOC_EMBED_DIM / VECTOR di schema = {self.s.doc_embed_dim}")
                    self._model = m
        return self._model


class DocRepo:
    def __init__(self, pool, settings: Settings, embedder: DocEmbedder):
        from psycopg.rows import dict_row

        self.pool, self.s, self.emb = pool, settings, embedder
        self._dict_row = dict_row
        self._active_cache: tuple[float, bool] = (0.0, False)

    def _one(self, sql, params=()):
        with self.pool.connection() as c:
            return c.cursor(row_factory=self._dict_row).execute(sql, params).fetchone()

    def _all(self, sql, params=()):
        with self.pool.connection() as c:
            return c.cursor(row_factory=self._dict_row).execute(sql, params).fetchall()

    # ----------------------------------------------------------- admin CRUD
    def find_by_sha(self, sha: str):
        return self._one("SELECT id::text AS id, title FROM documents WHERE file_sha256 = %s", (sha,))

    def create(self, *, title, description, filename, mime, storage_path, sha, uploaded_by,
               effective_from=None, effective_until=None, metadata=None) -> str:
        import json
        with self.pool.connection() as c:
            row = c.execute(
                """INSERT INTO documents (title, description, original_filename, mime_type, storage_path,
                                          file_sha256, uploaded_by, effective_from, effective_until, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (title, description, filename, mime, storage_path, sha, uploaded_by,
                 effective_from, effective_until, json.dumps(metadata or {}))).fetchone()
            doc_id = str(row[0])
            c.execute("INSERT INTO ingestion_jobs (document_id) VALUES (%s)", (doc_id,))
        return doc_id

    _LIST_SQL = """
        SELECT d.id::text AS id, d.title, d.description, d.original_filename, d.mime_type, d.page_count,
               d.status::text AS status, d.status_message, d.is_active, d.effective_from, d.effective_until,
               d.created_at, d.updated_at, u.name AS uploaded_by_name,
               (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunk_count,
               j.stage AS job_stage, j.progress AS job_progress, j.status::text AS job_status
        FROM documents d
        LEFT JOIN users u ON u.id = d.uploaded_by
        LEFT JOIN LATERAL (SELECT stage, progress, status FROM ingestion_jobs
                           WHERE document_id = d.id ORDER BY id DESC LIMIT 1) j ON TRUE
    """

    def list(self):
        return self._all(self._LIST_SQL + " ORDER BY d.created_at DESC")

    def get(self, doc_id: str):
        return self._one(self._LIST_SQL + " WHERE d.id = %s", (doc_id,))

    def chunks(self, doc_id: str, limit: int, offset: int):
        return self._all("""SELECT chunk_index, page_start, page_end, heading_path, content, token_count
                            FROM document_chunks WHERE document_id = %s
                            ORDER BY chunk_index LIMIT %s OFFSET %s""", (doc_id, limit, offset))

    def update(self, doc_id: str, fields: dict):
        allowed = {"title", "description", "is_active", "effective_from", "effective_until"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get(doc_id)
        cols = ", ".join(f"{k} = %({k})s" for k in sets)
        with self.pool.connection() as c:
            c.execute(f"UPDATE documents SET {cols}, updated_at = now() WHERE id = %(id)s", {**sets, "id": doc_id})
        self._active_cache = (0.0, False)
        return self.get(doc_id)

    def reprocess(self, doc_id: str):
        with self.pool.connection() as c:
            c.execute("""UPDATE documents SET status = 'queued', status_message = NULL, is_active = FALSE,
                                updated_at = now() WHERE id = %s""", (doc_id,))
            c.execute("INSERT INTO ingestion_jobs (document_id) VALUES (%s)", (doc_id,))
        self._active_cache = (0.0, False)

    def delete(self, doc_id: str) -> str | None:
        row = self._one("DELETE FROM documents WHERE id = %s RETURNING storage_path", (doc_id,))
        self._active_cache = (0.0, False)
        return row["storage_path"] if row else None

    # --------------------------------------------------------------- worker
    def requeue_stuck(self):
        """Job 'running' saat server mati -> kembalikan ke antrian."""
        with self.pool.connection() as c:
            c.execute("UPDATE ingestion_jobs SET status = 'queued', stage = NULL WHERE status = 'running'")

    def claim_job(self):
        with self.pool.connection() as c:
            row = c.cursor(row_factory=self._dict_row).execute("""
                UPDATE ingestion_jobs SET status = 'running', started_at = now(), attempts = attempts + 1,
                                          stage = 'parsing', progress = 0
                WHERE id = (SELECT id FROM ingestion_jobs WHERE status = 'queued'
                            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING id, document_id::text AS document_id""").fetchone()
            if row:
                c.execute("UPDATE documents SET status = 'parsing', status_message = NULL WHERE id = %s",
                          (row["document_id"],))
            return row

    def job_progress(self, job_id: int, stage: str, progress: float, doc_status: str | None = None,
                     doc_id: str | None = None):
        with self.pool.connection() as c:
            c.execute("UPDATE ingestion_jobs SET stage = %s, progress = %s WHERE id = %s",
                      (stage, round(min(max(progress, 0), 1), 3), job_id))
            if doc_status and doc_id:
                c.execute("UPDATE documents SET status = %s WHERE id = %s", (doc_status, doc_id))

    def job_failed(self, job_id: int, doc_id: str, message: str):
        with self.pool.connection() as c:
            c.execute("""UPDATE ingestion_jobs SET status = 'failed', error = %s, finished_at = now()
                         WHERE id = %s""", (message, job_id))
            c.execute("""UPDATE documents SET status = 'failed', status_message = %s, is_active = FALSE,
                                updated_at = now() WHERE id = %s""", (message[:500], doc_id))

    def save_chunks(self, job_id: int, doc_id: str, chunks, vectors, model: str, page_count: int, summary: str):
        import json
        with self.pool.connection() as c:
            with c.transaction():
                c.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
                with c.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO document_chunks (document_id, chunk_index, page_start, page_end, heading_path,
                                                        content, embed_text, search_text, token_count, embedding,
                                                        embedding_model, metadata)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)""",
                        [(doc_id, ch.index, ch.page_start, ch.page_end, ch.heading_path, ch.content,
                          ch.embed_text, ch.search_text, ch.word_count, _vec(v), model, json.dumps(ch.metadata))
                         for ch, v in zip(chunks, vectors)])
                c.execute("""UPDATE documents SET status = 'ready', status_message = %s, page_count = %s,
                                    updated_at = now() WHERE id = %s""", (summary, page_count, doc_id))
                c.execute("""UPDATE ingestion_jobs SET status = 'succeeded', stage = 'done', progress = 1,
                                    finished_at = now() WHERE id = %s""", (job_id,))

    # --------------------------------------------------------------- search
    def has_active(self) -> bool:
        ts, val = self._active_cache
        if time.time() - ts > 30:
            row = self._one("SELECT EXISTS (SELECT 1 FROM active_documents WHERE visibility = 'public') AS x")
            val = bool(row["x"])
            self._active_cache = (time.time(), val)
        return val

    def search(self, question: str, k: int) -> list[dict]:
        qv = _vec(self.emb.get().encode([question])[0])
        fts = fts_query(question)
        lexical = """
          , l AS (SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.search_tsv, q.tq) DESC) AS rk
                  FROM document_chunks c
                  JOIN active_documents ad ON ad.id = c.document_id AND ad.visibility = 'public',
                       (SELECT to_tsquery('simple', %(fts)s) AS tq) q
                  WHERE c.search_tsv @@ q.tq
                  ORDER BY ts_rank_cd(c.search_tsv, q.tq) DESC LIMIT 30)""" if fts else \
            ", l AS (SELECT NULL::bigint AS id, NULL::bigint AS rk WHERE FALSE)"
        sql = f"""
          WITH d AS (SELECT c.id, 1 - (c.embedding <=> %(qv)s::vector) AS sim,
                            row_number() OVER (ORDER BY c.embedding <=> %(qv)s::vector) AS rk
                     FROM document_chunks c
                     JOIN active_documents ad ON ad.id = c.document_id AND ad.visibility = 'public'
                     ORDER BY c.embedding <=> %(qv)s::vector LIMIT 30)
          {lexical}
          SELECT c.id AS chunk_id, c.document_id::text AS document_id, doc.title, c.heading_path,
                 c.page_start, c.page_end, c.content,
                 coalesce(d.sim, 1 - (c.embedding <=> %(qv)s::vector)) AS sim,
                 coalesce(1.0 / (60 + d.rk), 0) + coalesce(1.0 / (60 + l.rk), 0) AS rrf,
                 d.rk AS dense_rank, l.rk AS lexical_rank
          FROM (SELECT id FROM d UNION SELECT id FROM l) u
          JOIN document_chunks c ON c.id = u.id
          JOIN documents doc ON doc.id = c.document_id
          LEFT JOIN d ON d.id = c.id
          LEFT JOIN l ON l.id = c.id
          ORDER BY rrf DESC LIMIT %(k)s"""
        return self._all(sql, {"qv": qv, "fts": fts, "k": k})


class IngestWorker(threading.Thread):
    """Thread latar yang memproses antrian ingestion satu per satu."""

    def __init__(self, repo: DocRepo, poll_s: float = 2.0):
        super().__init__(name="ingest-worker", daemon=True)
        self.repo, self.poll_s = repo, poll_s
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        try:
            self.repo.requeue_stuck()
        except Exception:  # noqa: BLE001
            log.exception("Gagal reset job yang macet")
        while not self._halt.is_set():
            try:
                if not self.run_once():
                    self._halt.wait(self.poll_s)
            except Exception:  # noqa: BLE001
                log.exception("Worker ingestion error")
                self._halt.wait(self.poll_s * 5)

    def run_once(self) -> bool:
        job = self.repo.claim_job()
        if not job:
            return False
        process_job(self.repo, job["id"], job["document_id"])
        return True


def process_job(repo: DocRepo, job_id: int, doc_id: str):
    doc = repo._one("SELECT title, storage_path, mime_type FROM documents WHERE id = %s", (doc_id,))
    if not doc:
        return
    t0 = time.perf_counter()
    try:
        pages = clean_pages(extract(doc["storage_path"], doc["mime_type"]))
        repo.job_progress(job_id, "chunking", 0.1)
        chunks = chunk_document(pages, doc["title"])
        repo.job_progress(job_id, "embedding", 0.15, doc_status="embedding", doc_id=doc_id)
        emb = repo.emb.get()
        vectors, batch = [], 16
        for i in range(0, len(chunks), batch):
            vectors.extend(emb.encode([c.embed_text for c in chunks[i:i + batch]]))
            repo.job_progress(job_id, "embedding", 0.15 + 0.8 * min(1, (i + batch) / len(chunks)))
        secs = time.perf_counter() - t0
        summary = f"{len(chunks)} potongan dari {len(pages)} halaman ({secs:.0f} detik)"
        repo.save_chunks(job_id, doc_id, chunks, vectors, emb.name, len(pages), summary)
        log.info("Dokumen %s siap: %s", doc_id, summary)
    except DocumentError as e:
        repo.job_failed(job_id, doc_id, str(e))
    except FileNotFoundError:
        repo.job_failed(job_id, doc_id, "File tidak ditemukan di server. Upload ulang dokumennya.")
    except Exception as e:  # noqa: BLE001
        from psycopg.errors import ForeignKeyViolation
        if isinstance(e, ForeignKeyViolation):
            log.info("Dokumen %s dihapus saat masih diproses, hasilnya dibuang", doc_id)
            return
        log.exception("Ingestion %s gagal", doc_id)
        repo.job_failed(job_id, doc_id, f"Error internal: {type(e).__name__}: {e}"[:300])


def safe_filename(name: str) -> str:
    import re
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")[:80] or "dokumen"
    return stem + ext
