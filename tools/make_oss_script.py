"""Buat script konsol OSS untuk kode yang belum punya uraian OSS.

  python -m tools.make_oss_script [--out tools/ambil_uraian.js] [--all]

Jalankan file .js hasilnya di Console DevTools pada tab https://oss.go.id,
lalu simpan JSON yang terunduh ke data/raw/oss/ dan jalankan ulang build_dataset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kbli.retrieval import _read_jsonl

TEMPLATE = Path(__file__).with_name("oss_console_template.js")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-dir", default="data/processed")
    ap.add_argument("--out", default="tools/ambil_uraian.js")
    ap.add_argument("--all", action="store_true", help="ambil ulang semua kode, bukan hanya yang belum ada")
    a = ap.parse_args()

    codes = _read_jsonl(Path(a.index_dir) / "kbli_codes.jsonl")
    todo = [c["kode"] for c in codes if a.all or c["uraian_source"] != "oss"]
    js = (TEMPLATE.read_text(encoding="utf-8")
          .replace("__CODES__", json.dumps(todo))
          .replace("__OUTFILE__", "kbli_uraian_oss.json"))
    Path(a.out).write_text(js, encoding="utf-8")
    print(f"{len(todo)} kode -> {a.out}")


if __name__ == "__main__":
    main()
