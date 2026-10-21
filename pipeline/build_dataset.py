"""Bangun dataset KBLI kanonik dari Excel lampiran + uraian OSS.

Input
  data/raw/lampiran/*.xlsx   kolom: Kode KBLI, Judul KBLI, Ruang Lingkup, [Kata Kunci]
                             nama file -> sektor (mis. pertanian.xlsx -> 'pertanian')
  data/raw/oss/*.json        output script konsol OSS: {"result": {kode: {uuid, uraian}}}

Output (data/processed/)
  kbli_codes.jsonl     satu baris per kode (sudah dedupe)
  kbli_scopes.jsonl    ruang lingkup per sektor dari lampiran
  kbli_chunks.jsonl    unit pencarian (judul / kalimat uraian / pengecualian)
  quality_report.md    ringkasan kualitas data untuk dicek manual

Pemakaian
  python -m pipeline.build_dataset [--raw data/raw] [--out data/processed]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from kbli.preprocess import chunk_kbli, clean_text, load_stem_cache, normalize_kode, save_stem_cache

# Urutan prioritas sumber judul (file sektor lebih bersih dari dataset lama)
SOURCE_PRIORITY = {"dataset_utama": 9}
SEKTOR_ALIASES = {r"^perindustrian.*": "perindustrian", r"^dataset_utama$": "dataset_utama"}
_RE_NOTE = re.compile(r"\s*(\(kecuali [^)]*\)|Catatan(\s*\([^)]*\))?:.*)$", re.IGNORECASE | re.DOTALL)
_SELURUH = {"seluruh", "seluruhnya"}


def sektor_from_filename(stem: str) -> str:
    for pattern, name in SEKTOR_ALIASES.items():
        if re.match(pattern, stem):
            return name
    return stem


def load_lampiran(raw_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted((raw_dir / "lampiran").glob("*.xlsx")):
        df = pd.read_excel(path, dtype=str).fillna("")
        df = df.rename(columns={"Kode KBLI": "kode_raw", "Judul KBLI": "judul",
                                "Ruang Lingkup": "ruang_lingkup", "Kata Kunci": "kata_kunci"})
        if "kata_kunci" not in df:
            df["kata_kunci"] = ""
        df["source_file"] = path.name
        df["sektor"] = sektor_from_filename(path.stem)
        frames.append(df[["kode_raw", "judul", "ruang_lingkup", "kata_kunci", "source_file", "sektor"]])
    if not frames:
        raise SystemExit(f"Tidak ada file Excel di {raw_dir / 'lampiran'}")
    df = pd.concat(frames, ignore_index=True)
    df["kode"] = df["kode_raw"].map(normalize_kode)
    df["judul"] = df["judul"].map(clean_text)
    df["ruang_lingkup"] = df["ruang_lingkup"].map(clean_text)
    return df


def load_oss(raw_dir: Path) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for path in sorted((raw_dir / "oss").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for kode, item in data.get("result", {}).items():
            k = normalize_kode(kode)
            if k and item.get("uraian"):
                merged[k] = {"uuid": item.get("uuid"), "uraian": clean_text(item["uraian"])}
    return merged


_RE_PASTE_PREFIX = re.compile(r"^(Se(?:l(?:u(?:r(?:u)?)?)?)?)(?=(Kelompok|Subgolongan|Golongan)\b)")
_RE_URAIAN_START = re.compile(r"^(Kelompok|Subgolongan|Golongan|Kegiatan|Usaha|Kode subklasifikasi)\b", re.IGNORECASE)
_RE_SCOPE_ONLY = re.compile(r"^(Selain|Khusus|Seluruh|Hanya)\b", re.IGNORECASE)


def _fix_cell(text: str) -> tuple[str, list[str]]:
    """Bersihkan satu sel uraian; kembalikan (uraian, catatan_ruang_lingkup)."""
    t = clean_text(text).strip().strip('"').strip()
    notes = []
    # "Seluru" + uraian + "h": kata "Seluruh" tertempel membungkus uraian
    m = _RE_PASTE_PREFIX.match(t)
    if m:
        prefix = m.group(1)
        suffix = "Seluruh"[len(prefix):]
        t = t[len(prefix):]
        if suffix and t.endswith(suffix):
            t = t[: -len(suffix)].rstrip()
    # Sel yang isinya ruang lingkup, bukan uraian ("Selain Penjualan Langsung; ...")
    if (len(t) < 120 and _RE_SCOPE_ONLY.match(t)) or (len(t) < 60 and not _RE_URAIAN_START.match(t)):
        return "", [t]
    m = re.search(r"\s*Catatan(?:\s*\([^)]*\))?:\s*(.+)$", t, re.DOTALL)
    if m:
        notes.append(m.group(1).strip().rstrip("."))
        t = t[:m.start()].strip()
    m = re.search(r"\s*\((kecuali [^)]*)\)\s*$", t, re.IGNORECASE)
    if m:
        notes.append("Seluruh " + m.group(1))
        t = t[:m.start()].strip()
    return t, notes


def _canonical(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def load_dataset_final(path: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """File gabungan (semua sektor, sudah berisi uraian). Baris dobel per kode:
    - nyaris sama (beda artefak) -> ambil yang paling bersih
    - isinya berbeda (mis. subklasifikasi 71102 IG001–IG005) -> digabung per paragraf
    """
    if not path.exists():
        return {}, {}
    df = pd.read_excel(path, dtype=str).fillna("")
    groups: dict[str, list[str]] = defaultdict(list)
    notes: dict[str, list[str]] = defaultdict(list)
    for kode_raw, text in zip(df["Kode KBLI"], df["Ruang Lingkup"]):
        kode = normalize_kode(kode_raw)
        if not kode or not text.strip():
            continue
        t, n = _fix_cell(text)
        notes[kode].extend(x for x in n if x not in notes[kode])
        if t:
            groups[kode].append(t)

    uraian: dict[str, list[str]] = {}
    # Kode yang selnya hanya berisi teks pendek ("Pembibitan kerbau potong; Budi daya ...")
    # tetap dapat uraian dari teks itu, kecuali bentuk batasan murni ("Selain ...").
    for kode, ns in notes.items():
        if kode not in groups:
            desc = [n for n in ns if not _RE_SCOPE_ONLY.match(n)]
            if desc:
                groups[kode] = [max(desc, key=len)]
    for kode, texts in groups.items():
        distinct: list[str] = []
        for t in sorted(texts, key=lambda s: (s.count(";"), -len(s))):  # paling sedikit artefak dulu
            if all(_similar(t, d) < 0.9 for d in distinct):
                distinct.append(t)
        uraian[kode] = distinct
    return uraian, dict(notes)


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _canonical(a), _canonical(b)).ratio()


def looks_like_uraian(text: str) -> bool:
    """Ruang lingkup lama di dataset_utama kadang berisi uraian lengkap."""
    return len(text) > 60 and text.lower() not in _SELURUH


def build(raw_dir: Path, out_dir: Path) -> dict:
    stem_cache = out_dir / "stem_cache.json"
    load_stem_cache(stem_cache)
    df = load_lampiran(raw_dir)
    oss = load_oss(raw_dir)
    final_uraian, final_notes = load_dataset_final(raw_dir / "dataset_final.xlsx")
    ov_path = raw_dir / "overrides.json"
    overrides = {normalize_kode(k): v for k, v in json.loads(ov_path.read_text(encoding="utf-8")).items()
                 if not k.startswith("_")} if ov_path.exists() else {}

    invalid = df[df["kode"].isna()]
    df = df[df["kode"].notna()].copy()
    df["prio"] = df["sektor"].map(lambda s: SOURCE_PRIORITY.get(s, 1))

    codes, scopes, chunks = [], [], []
    stats = Counter()
    lampiran_only = []
    conflicts: list[tuple[str, str, str]] = []

    for kode, g in df.sort_values(["kode", "prio"]).groupby("kode", sort=True):
        # Judul: ambil dari sumber berprioritas tertinggi, lalu yang paling sering muncul
        best = g[g["prio"] == g["prio"].min()]
        judul = Counter(best["judul"]).most_common(1)[0][0]

        if overrides.get(kode, {}).get("uraian"):
            uraian, source = clean_text(overrides[kode]["uraian"]), "manual"
            uuid = oss.get(kode, {}).get("uuid")
        elif kode in oss:
            uraian, source, uuid = oss[kode]["uraian"], "oss", oss[kode]["uuid"]
            # subklasifikasi tambahan di dataset_final yang tidak ada di uraian OSS
            # Uraian OSS dianggap resmi. Dari dataset_final hanya subklasifikasi tambahan
            # (mis. 71102 IG001–IG005) yang ditempel; perbedaan lain dicatat untuk dicek manual.
            others = [t for t in final_uraian.get(kode, []) if _similar(t, uraian) < 0.9]
            sub = [t for t in others if t.lower().startswith("kode subklasifikasi")]
            if sub:
                uraian = "\n\n".join([uraian, *sub])
            for t in others:
                if t not in sub:
                    conflicts.append((kode, judul, t[:160]))
        elif final_uraian.get(kode):
            uraian, source, uuid = "\n\n".join(final_uraian[kode]), "dataset_final", None
        else:
            fallback = [r for r in g["ruang_lingkup"] if looks_like_uraian(r)]
            uraian = _RE_NOTE.sub("", max(fallback, key=len)).strip() if fallback else None
            source, uuid = ("lampiran" if uraian else "kosong"), None
            lampiran_only.append((kode, judul, source))
        stats[f"uraian_{source}"] += 1

        kata_kunci = sorted({k.strip() for v in g["kata_kunci"] for k in re.split(r"[;,]", v) if k.strip()})
        kata_kunci = sorted(set(kata_kunci) | set(overrides.get(kode, {}).get("kata_kunci", [])))
        codes.append({"kode": kode, "judul": judul, "uraian": uraian, "uraian_source": source,
                      "oss_detail_uuid": uuid, "kata_kunci": kata_kunci,
                      "sektor": sorted(set(g["sektor"]))})

        seen = set()
        for _, row in g.iterrows():
            rl = row["ruang_lingkup"] or "Seluruh"
            # Ruang lingkup yang ternyata uraian lengkap tidak disimpan sebagai scope
            if row["sektor"] == "dataset_utama" and looks_like_uraian(rl):
                rl = "Seluruh"
            key = (row["sektor"], rl)
            if key in seen:
                continue
            seen.add(key)
            scopes.append({"kode": kode, "sektor": row["sektor"], "ruang_lingkup": rl,
                           "source_file": row["source_file"]})
        for note in final_notes.get(kode, []):
            if not any(s["kode"] == kode and s["ruang_lingkup"] == note for s in scopes[-10:]):
                scopes.append({"kode": kode, "sektor": "catatan_lampiran", "ruang_lingkup": note,
                               "source_file": "dataset_final.xlsx"})

        for c in chunk_kbli(kode, judul, uraian):
            d = asdict(c)
            d["search_text"] = c.search_text
            del d["search_tokens"]
            chunks.append(d)

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [("kbli_codes", codes), ("kbli_scopes", scopes), ("kbli_chunks", chunks)]:
        with open(out_dir / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    save_stem_cache(stem_cache)
    report = write_report(out_dir, df, invalid, codes, scopes, chunks, stats, lampiran_only, conflicts)
    return report


def write_report(out_dir, df, invalid, codes, scopes, chunks, stats, lampiran_only, conflicts=()) -> dict:
    ctype = Counter(c["chunk_type"] for c in chunks)
    lens = sorted((len(c["uraian"] or ""), c["kode"]) for c in codes)
    unbalanced = [c["kode"] for c in codes if c["uraian"] and c["uraian"].count("(") != c["uraian"].count(")")]
    per_sektor = Counter(s["sektor"] for s in scopes)
    summary = {
        "baris_excel": int(len(df) + len(invalid)),
        "kode_tidak_valid": int(len(invalid)),
        "kode_unik": len(codes),
        "uraian_oss": stats["uraian_oss"],
        "uraian_dataset_final": stats["uraian_dataset_final"],
        "uraian_manual": stats["uraian_manual"],
        "uraian_lampiran": stats["uraian_lampiran"],
        "uraian_kosong": stats["uraian_kosong"],
        "scope_rows": len(scopes),
        "chunks": dict(ctype),
    }
    lines = [
        "# Laporan kualitas dataset KBLI", "",
        "| Metrik | Nilai |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in summary.items()], "",
        "## Ruang lingkup per sektor", "",
        *[f"- {k}: {v}" for k, v in per_sektor.most_common()], "",
        f"## Kode tanpa uraian OSS ({len(lampiran_only)})",
        "Ambil lewat script konsol OSS, atau isi manual.", "",
        *[f"- {k} {j} — sumber: {s}" for k, j, s in lampiran_only], "",
        "## Uraian terpendek (cek apakah wajar)", "",
        *[f"- {k}: {n} karakter" for n, k in lens[:10]], "",
        f"## Uraian dataset_final yang berbeda dari OSS ({len(conflicts)})",
        "OSS dipakai. Cek apakah teks dataset_final ini salah tempel dari kode lain.", "",
        *[f"- {k} {j}: {t}" for k, j, t in conflicts], "",
        f"## Uraian dengan kurung tidak seimbang ({len(unbalanced)})", "",
        ", ".join(unbalanced) or "-", "",
    ]
    if len(invalid):
        lines += ["## Baris dengan kode tidak valid", "",
                  *[f"- {r.source_file}: {r.kode_raw!r}" for r in invalid.itertuples()], ""]
    (out_dir / "quality_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/processed")
    a = ap.parse_args()
    print(json.dumps(build(Path(a.raw), Path(a.out)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
