"""Kalibrasi ambang confidence untuk konfigurasi retrieval aktif (.env).

  python -m eval.calibrate --model denaya/indoSBERT-large

- confident_min_score + confident_min_fused_margin:
    jawaban "tinggi" (langsung dijawab tanpa klarifikasi) harus benar >= --min-precision.
    Di antara ambang yang memenuhi, dipilih yang paling banyak menjawab langsung.
- out_of_scope_max_score:
    pakai eval/topic_gate.json + query eval; tidak boleh ada pertanyaan relevan yang ditolak.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from kbli.config import Settings
from kbli.embedder import make_embedder
from kbli.metrics import precision_at_1
from kbli.retrieval import KbliIndex

from .run_eval import expand_expected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--queries", default="eval/queries.json")
    ap.add_argument("--gate", default="eval/topic_gate.json")
    ap.add_argument("--min-precision", type=float, default=0.95)
    a = ap.parse_args()

    s = Settings()
    idx = KbliIndex.load(s.kbli_index_dir, make_embedder(a.model))
    if not idx.dense_ready:
        raise SystemExit("Embedding belum ada / tidak cocok dengan --model.")
    print(f"Konfigurasi: dense={s.dense_strategy} fusion={s.fusion} w_bm25={s.fusion_w_bm25}\n")

    # --- 1. Ambang "yakin" ---
    rows = []
    for q in json.loads(Path(a.queries).read_text(encoding="utf-8")):
        res = idx.search(q["query"], settings=s)
        ok = precision_at_1([c.kode for c in res.candidates], expand_expected(idx, q["expected"]))
        rows.append((res.signals["dense_top"], res.signals.get("fused_margin", 0.0),
                     res.signals.get("agree", False), ok))
    d1 = np.array([r[0] for r in rows]); fm = np.array([r[1] for r in rows])
    agree = np.array([r[2] for r in rows]); ok = np.array([r[3] for r in rows])

    best = None
    for cmin in np.arange(0.20, 0.91, 0.025):
        for fmin in np.arange(0.0, 0.41, 0.02):
            conf = (d1 >= cmin) & ((fm >= fmin) | agree)
            if conf.sum() == 0:
                continue
            prec = ok[conf].mean()
            if prec >= a.min_precision and (best is None or conf.sum() > best[2]):
                best = (round(float(cmin), 3), round(float(fmin), 3), int(conf.sum()), float(prec))
    print(f"P@1 keseluruhan: {ok.mean():.3f} ({int(ok.sum())}/{len(ok)})")
    if best:
        cmin, fmin, n, prec = best
        print(f"CONFIDENT_MIN_SCORE={cmin}\nCONFIDENT_MIN_FUSED_MARGIN={fmin}")
        print(f"  -> {n}/{len(ok)} query ({n/len(ok):.0%}) dijawab langsung, ketepatan {prec:.1%};"
              f" sisanya diberi tombol klarifikasi\n")
    else:
        print(f"Tidak ada ambang dengan ketepatan >= {a.min_precision:.0%}; turunkan --min-precision\n")

    # --- 2. Ambang luar topik ---
    gate = json.loads(Path(a.gate).read_text(encoding="utf-8"))
    def sig(q):
        r = idx.search(q, settings=s)
        return r.signals["dense_top"], r.signals["coverage"]
    ins = [sig(q) for q in gate["in_scope"]] + [(d, 1.0) for d in d1]   # query eval = relevan
    outs = [sig(q) for q in gate["out_of_scope"]]
    best_o = None
    for th in np.arange(0.10, 0.91, 0.01):
        rejected_in = sum(1 for d, c in ins if d < th and c < s.min_query_coverage)
        caught = sum(1 for d, c in outs if d < th and c < s.min_query_coverage)
        if rejected_in == 0 and (best_o is None or caught > best_o[1]):
            best_o = (round(float(th), 2), caught)
    print("Skor dense top-1  relevan: min %.3f median %.3f | luar topik: median %.3f max %.3f" % (
        min(d for d, _ in ins), float(np.median([d for d, _ in ins])),
        float(np.median([d for d, _ in outs])), max(d for d, _ in outs)))
    if best_o:
        print(f"OUT_OF_SCOPE_MAX_SCORE={best_o[0]}  -> {best_o[1]}/{len(outs)} luar topik tertangkap"
              f" (+ aturan pertanyaan umum di api/templates.py), 0 relevan tertolak")
    print("\nSalin baris NAMA=nilai di atas ke .env")


if __name__ == "__main__":
    main()
