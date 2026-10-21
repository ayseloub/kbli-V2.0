"""Evaluasi retrieval KBLI dengan metrik skripsi (P@1, Recall@5, MRR@5, nDCG@5).

  python -m eval.run_eval --queries eval/queries.json [--model denaya/indoSBERT-large]

Format queries.json:
  [{"query": "jualan baju online", "expected": ["47911"]}, ...]

Mode yang dibandingkan: bm25, dense (kalau embedding tersedia), hybrid.
Hasil per query disimpan di eval/results/<timestamp>.json untuk analisis error.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from statistics import mean

from kbli.config import Settings
from kbli.embedder import make_embedder
from kbli.metrics import evaluate
from kbli.preprocess import normalize_kode
from kbli.retrieval import KbliIndex


def expand_expected(index: KbliIndex, expected: list[str]) -> set[str]:
    """Label 5 digit = kode persis; label 4 digit (subgolongan, mis. '9620') = semua kode
    5 digit di bawahnya. Jangan pakai normalize_kode di sini: '9620' bukan '09620'."""
    out: set[str] = set()
    for x in expected:
        x = str(x).strip()
        if len(x) == 4 and x.isdigit():
            out |= {k for k in index.kode_list if k.startswith(x)}
        elif (k := normalize_kode(x)):
            out.add(k)
    return out


def run(index: KbliIndex, queries: list[dict], mode: str, k: int = 5, settings=None) -> dict:
    per_query = []
    for q in queries:
        relevant = expand_expected(index, q["expected"])
        res = index.search(q["query"], top_k=k, mode=mode, settings=settings)
        ranked = [c.kode for c in res.candidates]
        m = evaluate(ranked, relevant, k)
        per_query.append({"query": q["query"], "expected": sorted(relevant), "got": ranked,
                          "kasus": q.get("kasus", "-"), "confidence": res.confidence, **m})
    metrics = {key: round(mean(r[key] for r in per_query), 4) for key in per_query[0]
               if key not in ("query", "expected", "got", "confidence", "kasus")}
    return {"mode": res.mode, "metrics": metrics, "per_query": per_query}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="eval/queries.json")
    ap.add_argument("--index-dir", default="data/processed")
    ap.add_argument("--model", default=None, help="nama embedder; kosong = BM25 saja")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-intent", action="store_true", help="matikan intent rerank (pembanding)")
    a = ap.parse_args()

    queries = json.loads(Path(a.queries).read_text(encoding="utf-8"))
    embedder = make_embedder(a.model) if a.model else None
    index = KbliIndex.load(a.index_dir, embedder)

    modes = ["bm25"] + (["dense", "hybrid"] if index.dense_ready else [])
    settings = Settings()      # konfigurasi dari .env (strategi dense, fusion, intent rerank)
    if a.no_intent:
        settings.intent_rerank = False
    print(f"Konfigurasi: dense={settings.dense_strategy} fusion={settings.fusion} "
          f"w_bm25={settings.fusion_w_bm25} intent_rerank={settings.intent_rerank}")
    results = {m: run(index, queries, m, a.k, settings) for m in modes}

    keys = list(results[modes[0]]["metrics"])
    print(f"\n{len(queries)} query · k={a.k}\n")
    print(f"{'mode':<8}" + "".join(f"{x:>11}" for x in keys))
    for m in modes:
        print(f"{m:<8}" + "".join(f"{results[m]['metrics'][x]:>11.4f}" for x in keys))

    kasus = sorted({r["kasus"] for r in results[modes[0]]["per_query"]})
    if len(kasus) > 1:
        print("\nP@1 per kasus")
        for ks in kasus:
            row = [r for r in results[modes[0]]["per_query"] if r["kasus"] == ks]
            cells = []
            for m in modes:
                rows = [r for r in results[m]["per_query"] if r["kasus"] == ks]
                cells.append(f"{m}={mean(r['p@1'] for r in rows):.3f}")
            print(f"  {ks:<36} n={len(row):<3} " + "  ".join(cells))

    out = Path("eval/results")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"model": a.model, "results": results}, ensure_ascii=False, indent=2))
    print(f"\nDetail per query: {path}")


if __name__ == "__main__":
    main()
