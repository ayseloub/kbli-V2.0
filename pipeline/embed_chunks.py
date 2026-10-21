"""Hitung embedding untuk semua chunk KBLI.

  python -m pipeline.embed_chunks --model denaya/indoSBERT-large
  python -m pipeline.embed_chunks --model hashing-256      # tanpa unduh model (test saja)

Output: data/processed/kbli_chunk_embeddings.npy (+ .json berisi nama model & dimensi).
Jalankan di laptop/VPS yang bisa mengunduh model dari HuggingFace.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from kbli.embedder import make_embedder
from kbli.preprocess import preprocess_dense
from kbli.retrieval import _read_jsonl


def doc_texts(codes: list[dict], chunks: list[dict]) -> list[str]:
    """Satu teks per kode: judul + kalimat uraian inti (tanpa pengecualian).
    Model memotong sendiri di batas max_seq_length-nya."""
    inti: dict[str, list[str]] = {}
    for c in chunks:
        if c["chunk_type"] == "uraian":
            inti.setdefault(c["kode"], []).append(preprocess_dense(c["content"]))
    return [f"{c['judul']}. {' '.join(inti.get(c['kode'], []))}".strip() for c in codes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dir", default="data/processed")
    a = ap.parse_args()

    d = Path(a.dir)
    chunks = _read_jsonl(d / "kbli_chunks.jsonl")
    codes = _read_jsonl(d / "kbli_codes.jsonl")
    emb = make_embedder(a.model)
    t = time.time()
    vecs = emb.encode([c["embed_text"] for c in chunks])
    np.save(d / "kbli_chunk_embeddings.npy", vecs.astype(np.float32))
    docs = emb.encode(doc_texts(codes, chunks))
    np.save(d / "kbli_doc_embeddings.npy", docs.astype(np.float32))
    (d / "kbli_chunk_embeddings.json").write_text(json.dumps(
        {"model": emb.name, "dim": emb.dim, "n_chunks": len(chunks), "n_docs": len(codes)}, indent=2))
    print(f"{len(chunks)} chunk + {len(codes)} dokumen, dim={emb.dim}, {time.time() - t:.1f} detik")


if __name__ == "__main__":
    main()
