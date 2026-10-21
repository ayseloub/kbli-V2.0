"""Ablation retrieval: strategi dense x metode fusion x bobot BM25, sekali jalan.

  python -m eval.ablation --model denaya/indoSBERT-large [--queries eval/queries.json]

Embedding query dihitung sekali per query, lalu semua kombinasi dievaluasi dari
skor yang sama — cepat walau kombinasinya banyak. Hasil diurutkan menurut P@1, lalu MRR@5.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from kbli.embedder import make_embedder
from kbli.metrics import evaluate
from kbli.retrieval import KbliIndex

from .run_eval import expand_expected

STRATEGIES = ["chunk", "doc", "max", "mean"]
FUSIONS = ["rrf", "linear"]
WEIGHTS = [0.5, 0.6, 0.7, 0.8, 0.9]


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--queries", default="eval/queries.json")
    ap.add_argument("--index-dir", default="data/processed")
    ap.add_argument("--top", type=int, default=15, help="jumlah konfigurasi terbaik yang ditampilkan")
    a = ap.parse_args()

    idx = KbliIndex.load(a.index_dir, make_embedder(a.model))
    if not idx.dense_ready:
        raise SystemExit("Embedding belum ada / tidak cocok. Jalankan pipeline.embed_chunks dengan model yang sama.")
    strategies = STRATEGIES if idx.doc_emb is not None else ["chunk"]
    if idx.doc_emb is None:
        print("! kbli_doc_embeddings.npy belum ada — jalankan ulang embed_chunks untuk strategi doc/max/mean\n")

    queries = json.loads(Path(a.queries).read_text(encoding="utf-8"))
    pre = [(q, expand_expected(idx, q["expected"]), idx.components(q["query"])) for q in queries]

    rows = []

    def score(name, mode, cfg):
        per = []
        for q, rel, comp in pre:
            res = idx.search(q["query"], top_k=5, mode=mode, settings=cfg, comp=comp)
            m = evaluate([c.kode for c in res.candidates], rel, 5)
            m["kasus"] = q.get("kasus", "-")
            per.append(m)
        agg = {k: mean(p[k] for p in per) for k in ("p@1", "recall@5", "mrr@5", "ndcg@5")}
        kasus = sorted({p["kasus"] for p in per})
        agg["per_kasus"] = {k: mean(p["p@1"] for p in per if p["kasus"] == k) for k in kasus}
        rows.append((name, agg))

    score("bm25", "bm25", Cfg())
    for st in strategies:
        score(f"dense/{st}", "dense", Cfg(dense_strategy=st))
        for fu in FUSIONS:
            for w in WEIGHTS:
                score(f"hybrid/{st}/{fu}/w{w}", "hybrid", Cfg(dense_strategy=st, fusion=fu, fusion_w_bm25=w))

    rows.sort(key=lambda r: (r[1]["p@1"], r[1]["mrr@5"]), reverse=True)
    kasus = list(rows[0][1]["per_kasus"])
    short = [k.split(" - ")[0].replace("Kasus ", "K") for k in kasus]
    print(f"{len(queries)} query · model {a.model}\n")
    print(f"{'konfigurasi':<28}{'P@1':>7}{'R@5':>7}{'MRR@5':>7}{'nDCG@5':>8}  " + " ".join(f"{s:>5}" for s in short))
    shown = rows[:a.top] + [r for r in rows if r[0] in ("bm25",) or r[0].startswith("dense/")]
    seen = set()
    for name, m in shown:
        if name in seen:
            continue
        seen.add(name)
        print(f"{name:<28}{m['p@1']:>7.3f}{m['recall@5']:>7.3f}{m['mrr@5']:>7.3f}{m['ndcg@5']:>8.3f}  "
              + " ".join(f"{m['per_kasus'][k]:>5.2f}" for k in kasus))

    out = Path("eval/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "ablation.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nSemua {len(rows)} konfigurasi: eval/results/ablation.json")
    print(f"Terbaik: {rows[0][0]}  -> set di .env: DENSE_STRATEGY / FUSION / FUSION_W_BM25")


if __name__ == "__main__":
    main()
