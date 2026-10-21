"""Embedder yang bisa diganti-ganti (IndoSBERT, bge-m3, atau hashing untuk test)."""
from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Kembalikan matriks (n, dim) float32 yang sudah dinormalisasi L2."""


def _l2norm(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (x / n).astype(np.float32)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, device: str | None = None, batch_size: int = 32):
        from sentence_transformers import SentenceTransformer  # import berat, ditunda

        self.name = model_name
        self.model = SentenceTransformer(model_name, device=device)
        getdim = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = int(getdim())
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=self.batch_size, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=len(texts) > 500)
        return vecs.astype(np.float32)


class HashingEmbedder:
    """Embedding n-gram karakter berbasis hashing.

    BUKAN model semantik — hanya untuk test dan menjalankan pipeline end-to-end
    di mesin yang belum bisa mengunduh model. Jangan dipakai untuk eval/produksi.
    """

    def __init__(self, dim: int = 256, n: int = 3):
        self.name = f"hashing-{dim}"
        self.dim = dim
        self.n = n

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            s = f" {t.lower()} "
            for j in range(len(s) - self.n + 1):
                h = int(hashlib.md5(s[j:j + self.n].encode()).hexdigest()[:8], 16)
                out[i, h % self.dim] += 1.0
        return _l2norm(out)


def make_embedder(model_name: str) -> Embedder:
    if model_name.startswith("hashing"):
        dim = int(model_name.split("-")[1]) if "-" in model_name else 256
        return HashingEmbedder(dim)
    return SentenceTransformerEmbedder(model_name)
