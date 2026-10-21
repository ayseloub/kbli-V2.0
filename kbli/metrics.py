"""Metrik retrieval yang sama dengan skripsi: P@1, Recall@k, MRR@k, nDCG@k.

Relevansi biner; satu query boleh punya lebih dari satu kode benar.
"""
from __future__ import annotations

import math


def precision_at_1(ranked: list[str], relevant: set[str]) -> float:
    return 1.0 if ranked and ranked[0] in relevant else 0.0


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mrr_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    for i, kode in enumerate(ranked[:k], start=1):
        if kode in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, kode in enumerate(ranked[:k], start=1) if kode in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def evaluate(ranked: list[str], relevant: set[str], k: int = 5) -> dict[str, float]:
    return {
        "p@1": precision_at_1(ranked, relevant),
        f"recall@{k}": recall_at_k(ranked, relevant, k),
        f"mrr@{k}": mrr_at_k(ranked, relevant, k),
        f"ndcg@{k}": ndcg_at_k(ranked, relevant, k),
    }
