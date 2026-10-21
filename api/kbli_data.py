"""Kelola data KBLI dari dashboard: edit per kode, impor/ekspor Excel, versi & rollback.

Setiap perubahan menghasilkan VERSI baru (folder lengkap yang bisa langsung dimuat KbliIndex):

  data/kbli_versions/
    CURRENT                  -> id versi aktif (ditulis atomik)
    20261020-101500-ab12/    -> kbli_codes.jsonl, kbli_scopes.jsonl, kbli_chunks.jsonl,
                                kbli_chunk_embeddings.npy/.json, kbli_doc_embeddings.npy,
                                stem_cache.json, version.json
    _staged/<token>.json     -> hasil impor Excel yang menunggu "Terapkan"
    _job.json                -> status job impor terakhir

Embedding hanya dihitung ulang untuk kode yang judul/uraiannya berubah; sisanya disalin.
Perubahan kata kunci tidak butuh embedding (hanya BM25), jadi edit kecil selesai < 1 detik.
Rollback = mengganti CURRENT ke versi lama.
"""
from __future__ import annotations

import io
import json
import logging
import os
import secrets
import shutil
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from kbli.preprocess import chunk_kbli, clean_text, load_stem_cache, normalize_kode, save_stem_cache
from kbli.retrieval import _read_jsonl

log = logging.getLogger("kbli.data")

FILES = ("kbli_codes.jsonl", "kbli_scopes.jsonl", "kbli_chunks.jsonl")
EDITABLE = ("judul", "uraian", "kata_kunci")


class DataError(Exception):
    """Pesan aman ditampilkan ke admin."""


def _write_jsonl(path: Path, rows: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _norm_kw(v) -> list[str]:
    if isinstance(v, str):
        v = v.replace(";", ",").split(",")
    return sorted({clean_text(x).lower() for x in (v or []) if clean_text(x)})


def _doc_text(code: dict, chunks: list[dict]) -> str:
    from kbli.preprocess import preprocess_dense
    inti = [preprocess_dense(c["content"]) for c in chunks if c["chunk_type"] == "uraian"]
    return f"{code['judul']}. {' '.join(inti)}".strip()


class KbliData:
    keep_versions = 30

    def __init__(self, base_dir: str | Path, seed_dir: str | Path, embedder=None, on_change=None):
        self.base = Path(base_dir)
        self.seed = Path(seed_dir)
        self.embedder = embedder          # embedder IndoSBERT yang sama dengan indeks (atau None)
        self.on_change = on_change        # callback(dir) setelah versi aktif berganti
        self._lock = threading.Lock()

    # ----------------------------------------------------------- lokasi
    def current_id(self) -> str | None:
        p = self.base / "CURRENT"
        return p.read_text().strip() if p.exists() else None

    def active_dir(self) -> Path:
        vid = self.current_id()
        return self.base / vid if vid and (self.base / vid).exists() else self.seed

    def _set_current(self, vid: str):
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self.base / f"CURRENT.{os.getpid()}.tmp"
        tmp.write_text(vid)
        os.replace(tmp, self.base / "CURRENT")

    # ------------------------------------------------------------ baca
    def load(self, d: Path | None = None) -> dict:
        d = d or self.active_dir()
        out = {name.split(".")[0]: _read_jsonl(d / name) if (d / name).exists() else [] for name in FILES}
        out["chunk_emb"] = out["doc_emb"] = out["meta"] = None
        if (d / "kbli_chunk_embeddings.npy").exists() and (d / "kbli_chunk_embeddings.json").exists():
            out["meta"] = json.loads((d / "kbli_chunk_embeddings.json").read_text())
            out["chunk_emb"] = np.load(d / "kbli_chunk_embeddings.npy")
            if (d / "kbli_doc_embeddings.npy").exists():
                out["doc_emb"] = np.load(d / "kbli_doc_embeddings.npy")
        return out

    def get(self, kode: str) -> dict | None:
        data = self.load()
        c = next((c for c in data["kbli_codes"] if c["kode"] == kode), None)
        if c:
            c = {**c, "scopes": [s for s in data["kbli_scopes"] if s["kode"] == kode]}
        return c

    def list_codes(self, q: str | None, limit: int, offset: int) -> dict:
        codes = self.load()["kbli_codes"]
        if q:
            ql = q.lower().strip()
            codes = [c for c in codes if c["kode"].startswith(ql) or ql in c["judul"].lower()
                     or ql in " ".join(c.get("kata_kunci") or [])]
        items = [{"kode": c["kode"], "judul": c["judul"], "kata_kunci": c.get("kata_kunci") or [],
                  "sektor": c.get("sektor") or [], "uraian_source": c.get("uraian_source"),
                  "uraian": (c.get("uraian") or "")[:220]} for c in codes[offset:offset + limit]]
        return {"total": len(codes), "items": items}

    def versions(self) -> list[dict]:
        cur = self.current_id()
        out = []
        if self.base.exists():
            for d in sorted(self.base.iterdir(), reverse=True):
                if d.is_dir() and (d / "version.json").exists():
                    v = json.loads((d / "version.json").read_text())
                    out.append({**v, "active": v["id"] == cur})
        out.append({"id": "awal", "note": "Data awal (hasil pipeline)", "source": "awal",
                    "created_at": None, "created_by": None, "stats": {}, "active": not cur})
        return out

    # ---------------------------------------------------------- tulis
    def save_code(self, kode: str, fields: dict, user: str | None) -> dict:
        kode = normalize_kode(kode)
        if not kode:
            raise DataError("Kode KBLI harus 5 digit.")
        with self._lock:
            data = self.load()
            codes = [dict(c) for c in data["kbli_codes"]]
            existing = next((c for c in codes if c["kode"] == kode), None)
            clean = {}
            if "judul" in fields and fields["judul"] is not None:
                clean["judul"] = clean_text(fields["judul"])
                if len(clean["judul"]) < 3:
                    raise DataError("Judul terlalu pendek.")
            if "uraian" in fields and fields["uraian"] is not None:
                clean["uraian"] = (fields["uraian"] or "").strip() or None
            if "kata_kunci" in fields and fields["kata_kunci"] is not None:
                clean["kata_kunci"] = _norm_kw(fields["kata_kunci"])
            if existing:
                if all(existing.get(k) == v for k, v in clean.items()):
                    return {**existing, "unchanged": True}
                existing.update(clean)
                if "uraian" in clean:
                    existing["uraian_source"] = "manual"
                note = f"Edit {kode}"
            else:
                if not clean.get("judul"):
                    raise DataError("Kode baru wajib punya judul.")
                codes.append({"kode": kode, "judul": clean["judul"], "uraian": clean.get("uraian"),
                              "uraian_source": "manual", "oss_detail_uuid": None,
                              "kata_kunci": clean.get("kata_kunci", []), "sektor": []})
                codes.sort(key=lambda c: c["kode"])
                note = f"Tambah {kode}"
            vid = self._new_version(data, codes, data["kbli_scopes"], note, user, "edit")
        self._activate(vid)
        return self.get(kode)

    def delete_code(self, kode: str, user: str | None):
        with self._lock:
            data = self.load()
            codes = [c for c in data["kbli_codes"] if c["kode"] != kode]
            if len(codes) == len(data["kbli_codes"]):
                raise DataError("Kode tidak ditemukan.")
            scopes = [s for s in data["kbli_scopes"] if s["kode"] != kode]
            vid = self._new_version(data, codes, scopes, f"Hapus {kode}", user, "edit")
        self._activate(vid)

    def rollback(self, vid: str, user: str | None):
        if vid == "awal":
            p = self.base / "CURRENT"
            p.unlink(missing_ok=True)
        else:
            if not (self.base / vid / "version.json").exists():
                raise DataError("Versi tidak ditemukan.")
            self._set_current(vid)
        log.info("Data KBLI dikembalikan ke versi %s oleh %s", vid, user)
        if self.on_change:
            self.on_change(self.active_dir())

    def _activate(self, vid: str):
        self._set_current(vid)
        if self.on_change:
            self.on_change(self.base / vid)

    def _new_version(self, old: dict, codes: list[dict], scopes: list[dict], note: str, user: str | None,
                     source: str, progress=None) -> str:
        """Tulis versi baru. Chunk & embedding dipakai ulang untuk kode yang judul/uraiannya sama."""
        old_codes = {c["kode"]: c for c in old["kbli_codes"]}
        old_chunks: dict[str, list[tuple[int, dict]]] = {}
        for i, ch in enumerate(old["kbli_chunks"]):
            old_chunks.setdefault(ch["kode"], []).append((i, ch))
        old_doc_pos = {c["kode"]: i for i, c in enumerate(old["kbli_codes"])}
        emb, dense = self.embedder, old["chunk_emb"] is not None and self.embedder is not None
        if dense and old["meta"] and old["meta"].get("model") != emb.name:
            raise DataError(f"Embedding data aktif dibuat dengan {old['meta'].get('model')}, "
                            f"bukan {emb.name}. Jalankan ulang pipeline embed_chunks dulu.")

        # urut waktu sampai milidetik; sufiks acak hanya pemecah seri
        vid = datetime.now().strftime("%Y%m%d-%H%M%S%f")[:-3] + "-" + secrets.token_hex(2)
        while self.current_id() and vid <= self.current_id():
            time.sleep(0.002)
            vid = datetime.now().strftime("%Y%m%d-%H%M%S%f")[:-3] + "-" + secrets.token_hex(2)
        tmp = self.base / f".{vid}.tmp"
        tmp.mkdir(parents=True)
        src_cache = self.active_dir() / "stem_cache.json"
        if src_cache.exists():
            shutil.copy(src_cache, tmp / "stem_cache.json")
            load_stem_cache(tmp / "stem_cache.json")

        chunks: list[dict] = []
        chunk_rows: list[np.ndarray | None] = []      # vektor lama atau None (perlu dihitung)
        doc_rows: list[np.ndarray | None] = []
        changed = added = 0
        for c in codes:
            o = old_codes.get(c["kode"])
            same_text = o is not None and o["judul"] == c["judul"] and o.get("uraian") == c.get("uraian")
            if same_text and c["kode"] in old_chunks:
                for i, ch in old_chunks[c["kode"]]:
                    chunks.append(ch)
                    chunk_rows.append(old["chunk_emb"][i] if dense else None)
                doc_rows.append(old["doc_emb"][old_doc_pos[c["kode"]]]
                                if dense and old["doc_emb"] is not None else None)
            else:
                added += o is None
                changed += o is not None
                for ch in chunk_kbli(c["kode"], c["judul"], c.get("uraian")):
                    d = asdict(ch)
                    d["search_text"] = ch.search_text
                    del d["search_tokens"]
                    chunks.append(d)
                    chunk_rows.append(None)
                doc_rows.append(None)
            c["sektor"] = sorted({s["sektor"] for s in scopes if s["kode"] == c["kode"]} | set(c.get("sektor") or []))

        removed = len(set(old_codes) - {c["kode"] for c in codes})
        need = [i for i, r in enumerate(chunk_rows) if r is None]
        if dense:
            for start in range(0, len(need), 64):
                part = need[start:start + 64]
                vecs = emb.encode([chunks[i]["embed_text"] for i in part])
                for i, v in zip(part, vecs):
                    chunk_rows[i] = v
                if progress:
                    progress(0.1 + 0.8 * min(1.0, (start + 64) / max(1, len(need))))
            by_kode: dict[str, list[dict]] = {}
            for ch in chunks:
                by_kode.setdefault(ch["kode"], []).append(ch)
            need_doc = [i for i, r in enumerate(doc_rows) if r is None]
            if need_doc:
                vecs = emb.encode([_doc_text(codes[i], by_kode.get(codes[i]["kode"], [])) for i in need_doc])
                for i, v in zip(need_doc, vecs):
                    doc_rows[i] = v
            np.save(tmp / "kbli_chunk_embeddings.npy", np.stack(chunk_rows).astype(np.float32))
            np.save(tmp / "kbli_doc_embeddings.npy", np.stack(doc_rows).astype(np.float32))
            (tmp / "kbli_chunk_embeddings.json").write_text(json.dumps(
                {"model": emb.name, "dim": emb.dim, "n_chunks": len(chunks), "n_docs": len(codes)}, indent=2))

        _write_jsonl(tmp / "kbli_codes.jsonl", codes)
        _write_jsonl(tmp / "kbli_scopes.jsonl", scopes)
        _write_jsonl(tmp / "kbli_chunks.jsonl", chunks)
        save_stem_cache(tmp / "stem_cache.json")
        stats = {"kode": len(codes), "ditambah": added, "diubah_teks": changed, "dihapus": removed,
                 "chunk": len(chunks), "chunk_dihitung_ulang": len(need) if dense else 0}
        (tmp / "version.json").write_text(json.dumps({
            "id": vid, "note": note, "source": source, "created_by": user,
            "created_at": datetime.now(timezone.utc).isoformat(), "stats": stats,
            "dense": dense}, ensure_ascii=False, indent=2))
        os.replace(tmp, self.base / vid)
        log.info("Versi data KBLI %s dibuat: %s (%s)", vid, note, stats)
        self._prune(keep=self.keep_versions, protect={vid, self.current_id()})
        return vid

    def _prune(self, keep: int, protect: set):
        """Simpan N versi terbaru saja (tiap versi ±9 MB)."""
        dirs = sorted((d for d in self.base.iterdir() if d.is_dir() and (d / "version.json").exists()),
                      reverse=True)
        for d in dirs[keep:]:
            if d.name not in protect:
                shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------ Excel
    def export_xlsx(self) -> bytes:
        import pandas as pd
        data = self.load()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame([{"Kode KBLI": c["kode"], "Judul KBLI": c["judul"], "Uraian": c.get("uraian") or "",
                           "Kata Kunci": ", ".join(c.get("kata_kunci") or [])} for c in data["kbli_codes"]]
                         ).to_excel(xw, sheet_name="KBLI", index=False)
            pd.DataFrame([{"Kode KBLI": s["kode"], "Sektor": s["sektor"], "Ruang Lingkup": s["ruang_lingkup"]}
                          for s in data["kbli_scopes"]]).to_excel(xw, sheet_name="Ruang Lingkup", index=False)
            pd.DataFrame({"Petunjuk": [
                "Sheet KBLI: satu baris per kode. Kolom wajib: Kode KBLI (5 digit), Judul KBLI.",
                "Uraian boleh kosong. Kata Kunci dipisah koma, mis.: laundry, binatu, cuci baju.",
                "Sheet Ruang Lingkup (opsional): batasan per sektor. Kalau sheet ini dihapus, ruang lingkup lama dipakai.",
                "Kode yang tidak ada di file ini akan DIHAPUS saat diterapkan. Pratinjau menampilkan jumlahnya dulu.",
            ]}).to_excel(xw, sheet_name="Petunjuk", index=False)
            for ws in xw.book.worksheets:
                for col, width in zip("ABCD", (12, 48, 90, 40)):
                    ws.column_dimensions[col].width = width
        return buf.getvalue()

    def preview_import(self, content: bytes, filename: str, user: str | None) -> dict:
        import pandas as pd
        try:
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str)
        except Exception as e:  # noqa: BLE001
            raise DataError(f"File Excel tidak bisa dibaca: {e}") from e
        main = sheets.get("KBLI")
        if main is None:
            main = next(iter(sheets.values()))
        main = main.fillna("")
        cols = {c.strip().lower(): c for c in main.columns}
        kcol = cols.get("kode kbli") or cols.get("kode")
        jcol = cols.get("judul kbli") or cols.get("judul")
        if not kcol or not jcol:
            raise DataError("Kolom 'Kode KBLI' dan 'Judul KBLI' wajib ada di sheet KBLI.")
        ucol, kwcol = cols.get("uraian"), cols.get("kata kunci")

        data = self.load()
        old = {c["kode"]: c for c in data["kbli_codes"]}
        errors, codes, seen = [], [], set()
        for i, row in main.iterrows():
            raw = str(row[kcol]).strip()
            if not raw and not str(row[jcol]).strip():
                continue
            kode = normalize_kode(raw)
            if not kode:
                errors.append(f"Baris {i + 2}: kode '{raw}' bukan 5 digit")
                continue
            if kode in seen:
                errors.append(f"Baris {i + 2}: kode {kode} ganda")
                continue
            seen.add(kode)
            judul = clean_text(row[jcol])
            if len(judul) < 3:
                errors.append(f"Baris {i + 2}: judul kode {kode} kosong")
                continue
            o = old.get(kode, {})
            uraian = (str(row[ucol]).strip() or None) if ucol else o.get("uraian")
            kw = _norm_kw(row[kwcol]) if kwcol else (o.get("kata_kunci") or [])
            if o and kw == _norm_kw(o.get("kata_kunci")):
                kw = o.get("kata_kunci") or []          # sama setelah dinormalisasi -> pertahankan aslinya
            src = o.get("uraian_source") if o and o.get("uraian") == uraian else "impor"
            codes.append({"kode": kode, "judul": judul, "uraian": uraian, "uraian_source": src,
                          "oss_detail_uuid": o.get("oss_detail_uuid"), "kata_kunci": kw,
                          "sektor": o.get("sektor") or []})
        if not codes:
            raise DataError("Tidak ada baris kode yang valid di file.")
        if len(errors) > 50:
            raise DataError(f"{len(errors)} baris bermasalah, mis.: " + "; ".join(errors[:5]))

        scope_sheet = sheets.get("Ruang Lingkup")
        if scope_sheet is not None:
            scope_sheet = scope_sheet.fillna("")
            sc = {c.strip().lower(): c for c in scope_sheet.columns}
            scopes = [{"kode": normalize_kode(r[sc["kode kbli"]]), "sektor": clean_text(r[sc["sektor"]]) or "umum",
                       "ruang_lingkup": clean_text(r[sc["ruang lingkup"]]) or "Seluruh", "source_file": filename}
                      for _, r in scope_sheet.iterrows()
                      if "kode kbli" in sc and normalize_kode(r[sc["kode kbli"]]) in seen]
        else:
            scopes = [s for s in data["kbli_scopes"] if s["kode"] in seen]

        new = {c["kode"]: c for c in codes}
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = []
        for k in sorted(set(new) & set(old)):
            diff = [f for f in EDITABLE if (new[k].get(f) or None) != (old[k].get(f) or None)]
            if diff:
                changed.append({"kode": k, "judul": new[k]["judul"], "fields": diff})
        retext = sum(1 for c in changed if {"judul", "uraian"} & set(c["fields"]))

        token = secrets.token_urlsafe(12)
        staged = self.base / "_staged"
        staged.mkdir(parents=True, exist_ok=True)
        (staged / f"{token}.json").write_text(json.dumps(
            {"codes": codes, "scopes": scopes, "filename": filename, "user": user,
             "base_version": self.current_id(), "created": time.time()}, ensure_ascii=False))
        return {
            "token": token, "filename": filename, "total": len(codes), "errors": errors[:50],
            "added": [{"kode": k, "judul": new[k]["judul"]} for k in added[:100]], "added_count": len(added),
            "removed": [{"kode": k, "judul": old[k]["judul"]} for k in removed[:100]],
            "removed_count": len(removed), "changed": changed[:200], "changed_count": len(changed),
            "unchanged_count": len(codes) - len(added) - len(changed),
            "reembed_count": len(added) + retext,
        }

    # ------------------------------------------------------------ job impor
    def job_status(self) -> dict:
        p = self.base / "_job.json"
        return json.loads(p.read_text()) if p.exists() else {"status": "idle"}

    def _write_job(self, **kw):
        self.base.mkdir(parents=True, exist_ok=True)
        p = self.base / "_job.json"
        tmp = self.base / f"_job.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({**kw, "updated_at": time.time()}, ensure_ascii=False))
        os.replace(tmp, p)

    def apply_import(self, token: str, user: str | None):
        """Mulai job impor di thread latar. Status lewat job_status()."""
        p = self.base / "_staged" / f"{token}.json"
        if not p.exists():
            raise DataError("Pratinjau sudah kedaluwarsa. Upload ulang file Excel-nya.")
        if self.job_status().get("status") == "running":
            raise DataError("Masih ada impor yang berjalan. Tunggu sampai selesai.")
        staged = json.loads(p.read_text())
        if staged["base_version"] != self.current_id():
            raise DataError("Data berubah sejak pratinjau dibuat. Upload ulang file Excel-nya.")
        self._write_job(status="running", progress=0.02, message="Menyiapkan…", filename=staged["filename"])

        def run():
            try:
                with self._lock:
                    data = self.load()
                    vid = self._new_version(
                        data, staged["codes"], staged["scopes"], f"Impor {staged['filename']}", user, "impor",
                        progress=lambda f: self._write_job(status="running", progress=round(f, 3),
                                                           message="Menghitung embedding…",
                                                           filename=staged["filename"]))
                self._write_job(status="running", progress=0.95, message="Memasang indeks baru…",
                                filename=staged["filename"])
                self._activate(vid)
                self._write_job(status="succeeded", progress=1, message="Selesai", version=vid,
                                filename=staged["filename"])
                p.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                log.exception("Impor KBLI gagal")
                self._write_job(status="failed", progress=0, message=str(e)[:300], filename=staged["filename"])

        threading.Thread(target=run, name="kbli-import", daemon=True).start()


class IndexWatcher(threading.Thread):
    """Muat ulang indeks kalau CURRENT diganti proses lain (mis. uvicorn dengan beberapa worker)."""

    def __init__(self, data: KbliData, reload, interval: float = 5.0):
        super().__init__(name="kbli-watcher", daemon=True)
        self.data, self.reload, self.interval = data, reload, interval
        self.seen = data.current_id()
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        while not self._halt.wait(self.interval):
            try:
                cur = self.data.current_id()
                if cur != self.seen:
                    self.seen = cur
                    self.reload(self.data.active_dir())
            except Exception:  # noqa: BLE001
                log.exception("Gagal memuat ulang indeks KBLI")
