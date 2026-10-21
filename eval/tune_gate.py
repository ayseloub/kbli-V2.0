"""Setel ambang topic gate (min_query_coverage) dari contoh relevan vs tidak relevan.

  python -m eval.tune_gate [--file eval/topic_gate.json] [--model ...]

Tambah contoh dari retrieval_logs secara berkala, terutama yang salah ditolak/diterima.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from kbli.embedder import make_embedder
from kbli.retrieval import KbliIndex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="eval/topic_gate.json")
    ap.add_argument("--index-dir", default="data/processed")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()

    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    idx = KbliIndex.load(a.index_dir, make_embedder(a.model) if a.model else None)
    rows = [(q, 1, idx.coverage(q)) for q in data["in_scope"]] + \
           [(q, 0, idx.coverage(q)) for q in data["out_of_scope"]]

    best = None
    for th in np.arange(0.05, 0.96, 0.05):
        tp = sum(1 for _, y, c in rows if y == 1 and c >= th)
        tn = sum(1 for _, y, c in rows if y == 0 and c < th)
        acc = (tp + tn) / len(rows)
        fn = sum(1 for _, y, c in rows if y == 1 and c < th)   # user sah tertolak (paling merugikan)
        key = (acc, -fn)
        if best is None or key > best[0]:
            best = (key, round(float(th), 2), tp, tn, fn)

    (acc, _), th, tp, tn, fn = best
    print(f"Ambang terbaik min_query_coverage = {th}  (akurasi {acc:.2%}, relevan tertolak {fn})\n")
    for q, y, c in sorted(rows, key=lambda r: r[2]):
        pred = 1 if c >= th else 0
        flag = "" if pred == y else "  <-- salah"
        print(f"{c:5.2f}  {'relevan' if y else 'luar   '}  {q}{flag}")


if __name__ == "__main__":
    main()
