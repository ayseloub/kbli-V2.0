"""Kalibrasi DOC_MIN_SCORE untuk model embedding dokumen yang kamu pakai.

  python -m eval.doc_calibrate [--questions eval/doc_questions.json]

Syarat: minimal satu dokumen sudah di-upload, selesai diproses, dan DIAKTIFKAN.
Script mencari tiap pertanyaan dan menampilkan skor kemiripan potongan teratas.
Ambang yang baik: di atas skor pertanyaan "tidak relevan", di bawah skor yang "relevan".
"""
from __future__ import annotations

import argparse
import json

from api.docs import DocEmbedder, DocRepo
from api.store import PgStore
from kbli.config import get_settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/doc_questions.json")
    a = ap.parse_args()
    s = get_settings()
    qs = json.load(open(a.questions))
    store = PgStore(s.database_url)
    repo = DocRepo(store.pool, s, DocEmbedder(s))
    try:
        if not repo.has_active():
            raise SystemExit("Belum ada dokumen aktif. Upload & aktifkan dulu di /admin → Dokumen.")
        res = {}
        for group in ("relevan", "tidak_relevan"):
            print(f"\n== {group} ==")
            res[group] = []
            for q in qs[group]:
                hits = repo.search(q, 1)
                top = float(hits[0]["sim"]) if hits else 0.0
                where = " › ".join(hits[0]["heading_path"] or [])[:60] if hits else "-"
                res[group].append(top)
                print(f"  {top:.3f}  {q[:55]:<55}  {where}")
        lo, hi = max(res["tidak_relevan"]), min(res["relevan"])
        print(f"\nSkor tertinggi 'tidak relevan': {lo:.3f}")
        print(f"Skor terendah  'relevan'      : {hi:.3f}")
        if hi > lo:
            print(f"\n✅ Terpisah rapi. Saran: DOC_MIN_SCORE={round((lo + hi) / 2, 2)}")
        else:
            rel = sorted(res["relevan"])
            pick = round(rel[len(rel) // 4], 2)
            print(f"\n⚠️  Rentangnya tumpang tindih. Saran awal: DOC_MIN_SCORE={pick} "
                  "(sebagian kecil pertanyaan relevan akan lolos ke alur KBLI). "
                  "Cek juga apakah pertanyaan 'relevan' memang dibahas di dokumenmu.")
        print(f"Sekarang: DOC_MIN_SCORE={s.doc_min_score}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
