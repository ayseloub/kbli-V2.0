"""Muat data/processed ke PostgreSQL (kbli_codes, kbli_scopes, kbli_chunks).

  python -m pipeline.load_db [--dir data/processed]

Idempoten, satu transaksi. kbli_codes di-UPSERT (bukan TRUNCATE ... CASCADE) supaya
feedback user yang merujuk ke kode tidak ikut terhapus; scope & chunk diisi ulang.
Embedding ikut dimuat kalau dimensinya cocok dengan kolom VECTOR di schema.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import psycopg

from kbli.config import get_settings
from kbli.retrieval import _read_jsonl


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/processed")
    ap.add_argument("--database-url", default=None)
    a = ap.parse_args()
    print(load_dir(Path(a.dir), a.database_url or get_settings().database_url))


def load_dir(d: Path, url: str) -> str:
    """Muat satu folder data KBLI ke Postgres. Dipakai CLI dan dashboard admin."""
    codes = _read_jsonl(d / "kbli_codes.jsonl")
    scopes = _read_jsonl(d / "kbli_scopes.jsonl")
    chunks = _read_jsonl(d / "kbli_chunks.jsonl")

    emb, model = None, None
    if (d / "kbli_chunk_embeddings.npy").exists():
        meta = json.loads((d / "kbli_chunk_embeddings.json").read_text())
        emb, model = np.load(d / "kbli_chunk_embeddings.npy"), meta["model"]

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("""SELECT atttypmod FROM pg_attribute
                       WHERE attrelid = 'kbli_chunks'::regclass AND attname = 'embedding'""")
        db_dim = cur.fetchone()[0]
        if emb is not None and emb.shape[1] != db_dim:
            print(f"! Dimensi embedding {emb.shape[1]} != VECTOR({db_dim}) di DB — embedding dilewati")
            emb = None

        cur.execute("TRUNCATE kbli_chunks, kbli_scopes RESTART IDENTITY")
        cur.executemany(
            """INSERT INTO kbli_codes (kode, judul, uraian, uraian_source, oss_detail_uuid, kata_kunci, sektor)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (kode) DO UPDATE SET
                 judul = EXCLUDED.judul, uraian = EXCLUDED.uraian, uraian_source = EXCLUDED.uraian_source,
                 oss_detail_uuid = EXCLUDED.oss_detail_uuid, kata_kunci = EXCLUDED.kata_kunci,
                 sektor = EXCLUDED.sektor""",
            [(c["kode"], c["judul"], c["uraian"], c["uraian_source"], c["oss_detail_uuid"],
              c.get("kata_kunci") or [], c.get("sektor") or []) for c in codes])
        # kode yang sudah tidak ada di dataset dihapus (feedback -> selected_kode jadi NULL)
        cur.execute("DELETE FROM kbli_codes WHERE NOT (kode = ANY(%s))", ([c["kode"] for c in codes],))
        cur.executemany(
            "INSERT INTO kbli_scopes (kode, sektor, ruang_lingkup) VALUES (%s, %s, %s)",
            [(s["kode"], s["sektor"], s["ruang_lingkup"]) for s in scopes])
        cur.executemany(
            """INSERT INTO kbli_chunks (kode, chunk_type, chunk_index, content, embed_text, search_text,
                                        weight, embedding, embedding_model)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)""",
            [(c["kode"], c["chunk_type"], c["chunk_index"], c["content"], c["embed_text"],
              c["search_text"], c["weight"],
              _vec(emb[i]) if emb is not None else None, model if emb is not None else None)
             for i, c in enumerate(chunks)])
        conn.commit()
    return (f"OK: {len(codes)} kode, {len(scopes)} scope, {len(chunks)} chunk"
            f"{' + embedding' if emb is not None else ''}")


if __name__ == "__main__":
    main()
