"""Hybrid retrieval KBLI: BM25 + dense (IndoSBERT), digabung dengan RRF.

Index KBLI dimuat ke memori dari data/processed (±1.400 kode, ±10 ribu chunk),
jadi pencarian cukup numpy + rank_bm25 — tidak perlu query ke database.

  BM25  : satu dokumen per kode = judul (x2) + kalimat uraian inti + kata kunci
  Dense : skor per kode = max(cosine(query, chunk) * bobot chunk)
          kalimat 'pengecualian' tidak ikut dicari
  Fusion: Reciprocal Rank Fusion, k=60
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .embedder import Embedder
from .lexicon import QUERY_SYNONYMS, expand_query
from .preprocess import load_stem_cache, preprocess_bm25, preprocess_dense, preprocess_query_dense

log = logging.getLogger(__name__)

RRF_K = 60

import re as _re

_INTENT_RX = {
    # kata kerja/kata benda yang menandai usaha JASA (termasuk jenis usaha jasa yang umum)
    "jasa": _re.compile(r"\b(jasa|layanan|melayani|servis|service|sewa|rental|penyewaan|konsultan|konsultasi|"
                        r"laundry|londri|binatu|penatu|bengkel|salon|barbershop|pangkas|katering|catering|"
                        r"kos|kost|kosan|travel|ekspedisi|kurir|ojek|fotografer|fotografi|"
                        r"les|kursus|bimbel|reparasi|perbaikan|cuci|sablon)\b", _re.I),
    "produksi": _re.compile(r"\b(pabrik|produksi|memproduksi|bikin|membuat|pembuatan|industri|olahan)\b", _re.I),
    "dagang": _re.compile(r"\b(jual|jualan|menjual|toko|grosir|eceran|dagang|berdagang|distributor|reseller)\b", _re.I),
}
_INTENT_CONFLICT = {"jasa": ("industri", "dagang"), "produksi": ("jasa", "dagang"), "dagang": ("industri",)}


@dataclass
class Candidate:
    kode: str
    judul: str
    uraian: str | None
    score: float                       # skor fusion (atau skor metode tunggal)
    bm25: float = 0.0
    dense: float = 0.0
    bm25_rank: int | None = None
    dense_rank: int | None = None
    best_sentence: str | None = None   # kalimat uraian yang paling cocok (untuk alasan)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SearchResult:
    query: str
    mode: str
    candidates: list[Candidate]
    confidence: str                    # 'tinggi' | 'ragu' | 'luar_topik'
    signals: dict = field(default_factory=dict)


class KbliIndex:
    def __init__(self, codes: list[dict], chunks: list[dict],
                 embeddings: np.ndarray | None = None, embedder: Embedder | None = None,
                 scopes: list[dict] | None = None, doc_embeddings: np.ndarray | None = None):
        self.codes = {c["kode"]: c for c in codes}
        self.scopes: dict[str, list[dict]] = {}
        for s in scopes or []:
            self.scopes.setdefault(s["kode"], []).append(s)
        self.kode_list = list(self.codes)
        self.kode_pos = {k: i for i, k in enumerate(self.kode_list)}
        self.embedder = embedder

        # --- BM25 per kode ---
        docs: dict[str, list[str]] = {k: [] for k in self.kode_list}
        for ch in chunks:
            if ch["chunk_type"] == "pengecualian":
                continue
            toks = ch["search_text"].split()
            docs[ch["kode"]].extend(toks * (2 if ch["chunk_type"] == "judul" else 1))
        for k, c in self.codes.items():
            for kw in c.get("kata_kunci") or []:
                docs[k].extend(preprocess_bm25(kw))
        self.bm25 = BM25Okapi([docs[k] or ["_kosong_"] for k in self.kode_list])
        self.max_idf = max(self.bm25.idf.values())
        self.lexicon_stems = {t for key in QUERY_SYNONYMS for t in preprocess_bm25(key)}

        # --- Dense per chunk ---
        self.dense_ready = embeddings is not None and embedder is not None
        if self.dense_ready:
            keep = [i for i, ch in enumerate(chunks) if ch["chunk_type"] != "pengecualian"]
            self.chunk_emb = embeddings[keep]
            self.chunk_kode_idx = np.array([self.kode_pos[chunks[i]["kode"]] for i in keep])
            self.chunk_weight = np.array([chunks[i].get("weight", 1.0) for i in keep], dtype=np.float32)
            self.chunk_text = [chunks[i]["content"] for i in keep]
            self.chunk_type = [chunks[i]["chunk_type"] for i in keep]
        # embedding satu dokumen utuh per kode (judul + uraian inti), urutan = kode_list
        self.doc_emb = doc_embeddings if self.dense_ready else None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, index_dir: str | Path, embedder: Embedder | None = None) -> "KbliIndex":
        d = Path(index_dir)
        load_stem_cache(d / "stem_cache.json")
        codes = _read_jsonl(d / "kbli_codes.jsonl")
        chunks = _read_jsonl(d / "kbli_chunks.jsonl")
        scopes = _read_jsonl(d / "kbli_scopes.jsonl") if (d / "kbli_scopes.jsonl").exists() else []
        emb = doc_emb = None
        emb_path, meta_path = d / "kbli_chunk_embeddings.npy", d / "kbli_chunk_embeddings.json"
        if embedder is not None and emb_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("model") != embedder.name:
                log.warning("Embedding dibuat dengan %s, tapi embedder aktif %s — dense dimatikan",
                            meta.get("model"), embedder.name)
            elif meta.get("n_chunks") != len(chunks):
                log.warning("Jumlah embedding tidak cocok dengan chunk — jalankan ulang embed_chunks")
            else:
                emb = np.load(emb_path)
                doc_path = d / "kbli_doc_embeddings.npy"
                if doc_path.exists() and meta.get("n_docs") == len(codes):
                    doc_emb = np.load(doc_path)
        elif embedder is not None:
            log.warning("File embedding belum ada — mode dense dimatikan, hanya BM25")
        return cls(codes, chunks, emb, embedder if emb is not None else None, scopes, doc_emb)

    # ---------------------------------------------------------------- scoring
    def coverage(self, query: str) -> float:
        """Porsi (berbobot IDF) kata query yang dikenal kosakata KBLI.

        Kata yang tidak dikenal diberi bobot IDF maksimum, jadi query seperti
        'debug error python' (semua asing) mendapat coverage ~0, sedangkan
        'jualan seblak online' tetap tinggi karena 'jualan'/'online' ada di leksikon.
        """
        toks = preprocess_bm25(query)
        if not toks:
            return 0.0
        total = covered = 0.0
        for t in toks:
            known = t in self.bm25.idf or t in self.lexicon_stems
            w = max(self.bm25.idf.get(t, self.max_idf), 0.25)
            total += w
            covered += w if known else 0.0
        return covered / total

    def _bm25_scores(self, query: str) -> np.ndarray:
        toks = preprocess_bm25(expand_query(query))
        if not toks:
            return np.zeros(len(self.kode_list))
        return np.asarray(self.bm25.get_scores(toks), dtype=np.float32)

    def components(self, query: str, need_dense: bool = True) -> dict:
        """Hitung semua skor mentah sekali; dipakai search() dan eval/ablation."""
        comp = {"bm25": self._bm25_scores(query)}
        if need_dense and self.dense_ready:
            q = self.embedder.encode([preprocess_query_dense(query)])[0]
            sims = self.chunk_emb @ q
            weighted = sims * self.chunk_weight
            per_kode = np.full(len(self.kode_list), -1.0, dtype=np.float32)
            best = np.full(len(self.kode_list), -1, dtype=np.int64)
            order = np.argsort(weighted)          # naik; yang terakhir menimpa = maksimum
            per_kode[self.chunk_kode_idx[order]] = sims[order]
            best[self.chunk_kode_idx[order]] = order
            comp.update(chunk=per_kode, best=best)
            if self.doc_emb is not None:
                comp["doc"] = (self.doc_emb @ q).astype(np.float32)
        return comp

    def dense_from(self, comp: dict, strategy: str) -> np.ndarray:
        chunk, doc = comp["chunk"], comp.get("doc")
        if doc is None or strategy == "chunk":
            return chunk
        if strategy == "doc":
            return doc
        if strategy == "mean":
            return (chunk + doc) / 2
        return np.maximum(chunk, doc)                 # "max"

    @staticmethod
    def _ranks(x: np.ndarray) -> np.ndarray:
        r = np.empty(len(x), dtype=np.int64)
        r[np.argsort(-x, kind="stable")] = np.arange(1, len(x) + 1)
        return r

    @staticmethod
    def _minmax(x: np.ndarray, top: int = 100) -> np.ndarray:
        idx = np.argsort(-x)[:top]
        lo, hi = float(x[idx].min()), float(x[idx].max())
        out = np.zeros_like(x, dtype=np.float32)
        if hi > lo:
            out = np.clip((x - lo) / (hi - lo), 0, 1).astype(np.float32)
        return out

    def fuse(self, bm: np.ndarray, de: np.ndarray, fusion: str, w_bm25: float) -> np.ndarray:
        if fusion == "linear":
            fused = w_bm25 * self._minmax(bm) + (1 - w_bm25) * self._minmax(de)
        else:  # weighted RRF
            fused = w_bm25 / (RRF_K + self._ranks(bm)) + (1 - w_bm25) / (RRF_K + self._ranks(de))
        return fused

    def _kind(self, kode: str) -> str:
        j = self.codes[kode]["judul"].lower()
        if j.startswith(("industri", "pengolahan", "pembuatan")):
            return "industri"
        if j.startswith("perdagangan"):
            return "dagang"
        if j.startswith(("aktivitas", "jasa", "reparasi", "penyewaan", "aktivitas penyewaan")):
            return "jasa"
        return "lain"

    def intent_factors(self, query: str, penalty: float) -> np.ndarray:
        """Niat usaha dari kata kunci query -> turunkan skor kode yang jenisnya bertentangan.

        'jasa foto wisuda' (niat jasa) menurunkan Industri/Perdagangan alat fotografi,
        'pabrik keripik' (niat produksi) menurunkan kode jasa/perdagangan, dst.
        Hanya aktif jika niatnya tunggal (tidak campur).
        """
        if not hasattr(self, "_kinds"):
            self._kinds = np.array([self._kind(k) for k in self.kode_list])
        intents = {name for name, rx in _INTENT_RX.items() if rx.search(query)}
        f = np.ones(len(self.kode_list), dtype=np.float32)
        if len(intents) != 1:
            return f
        intent = intents.pop()
        for kind in _INTENT_CONFLICT[intent]:
            f[self._kinds == kind] = penalty
        return f

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid", settings=None,
               comp: dict | None = None) -> SearchResult:
        if mode in ("dense", "hybrid") and not self.dense_ready:
            mode = "bm25"
        strategy = getattr(settings, "dense_strategy", "max")
        fusion = getattr(settings, "fusion", "rrf")
        w_bm25 = getattr(settings, "fusion_w_bm25", 0.5)

        comp = comp or self.components(query, need_dense=mode != "bm25")
        bm = comp["bm25"]
        bm_rank = self._ranks(bm)
        de = de_rank = best = None
        if mode in ("dense", "hybrid"):
            de, best = self.dense_from(comp, strategy), comp["best"]
            de_rank = self._ranks(de)

        if mode == "bm25":
            fused = bm.copy()
        elif mode == "dense":
            fused = de.copy()
        else:
            fused = self.fuse(bm, de, fusion, w_bm25)
            if getattr(settings, "intent_rerank", False):
                fused = fused * self.intent_factors(query, getattr(settings, "intent_penalty", 0.70))
        if de is None:
            de = np.zeros(len(self.kode_list), dtype=np.float32)

        top = np.argsort(-fused, kind="stable")[:max(top_k, 2)]
        cands = []
        for i in top:
            k = self.kode_list[i]
            c = self.codes[k]
            sent = None
            if best is not None and best[i] >= 0 and self.chunk_type[best[i]] == "uraian":
                sent = self.chunk_text[best[i]]
            cands.append(Candidate(
                kode=k, judul=c["judul"], uraian=c.get("uraian"), score=float(fused[i]),
                bm25=float(bm[i]), dense=float(de[i]), bm25_rank=int(bm_rank[i]),
                dense_rank=int(de_rank[i]) if de_rank is not None else None, best_sentence=sent))

        srt = np.sort(fused)[::-1]
        fused_margin = float(srt[0] - srt[1]) if mode == "hybrid" and len(srt) > 1 else None
        confidence, signals = self._confidence(cands, bm, de if de_rank is not None else None,
                                               self.coverage(query), settings, fused_margin)
        return SearchResult(query, mode, cands[:top_k], confidence, signals)

    # ------------------------------------------------------------- confidence
    @staticmethod
    def _confidence(cands: list[Candidate], bm: np.ndarray, de: np.ndarray | None,
                    coverage: float, settings, fused_margin: float | None = None):
        oos_max = getattr(settings, "out_of_scope_max_score", 0.35)
        cov_min = getattr(settings, "min_query_coverage", 0.45)
        conf_min = getattr(settings, "confident_min_score", 0.60)
        conf_margin = getattr(settings, "confident_min_margin", 0.08)
        bm_margin = getattr(settings, "bm25_confident_rel_margin", 0.30)

        top_bm = float(bm.max()) if len(bm) else 0.0
        signals = {"bm25_top": top_bm, "coverage": round(coverage, 3)}
        if de is not None:
            srt = np.sort(de)[::-1]
            d1, d2 = float(srt[0]), float(srt[1]) if len(srt) > 1 else 0.0
            agree = cands[0].bm25_rank == 1 and cands[0].dense_rank == 1
            signals.update(dense_top=d1, dense_margin=d1 - d2, agree=agree)
            if d1 < oos_max and coverage < cov_min:
                return "luar_topik", signals
            fm_min = getattr(settings, "confident_min_fused_margin", 0.10)
            if fused_margin is not None:
                signals["fused_margin"] = round(fused_margin, 4)
                if d1 >= conf_min and (fused_margin >= fm_min or agree):
                    return "tinggi", signals
                return "ragu", signals
            if (d1 >= conf_min and d1 - d2 >= conf_margin) or (agree and d1 >= conf_min):
                return "tinggi", signals
            return "ragu", signals

        # Hanya BM25: coverage jadi penjaga topik utama
        if top_bm <= 0 or coverage < cov_min:
            return "luar_topik", signals
        b2 = cands[1].bm25 if len(cands) > 1 else 0.0
        rel_margin = (top_bm - b2) / top_bm
        signals["bm25_rel_margin"] = round(rel_margin, 3)
        return ("tinggi" if rel_margin >= bm_margin else "ragu"), signals

    def get(self, kode: str) -> dict | None:
        return self.codes.get(kode)

    def special_scopes(self, kode: str) -> list[str]:
        """Ruang lingkup lampiran yang bukan 'Seluruh' (batasan yang perlu disampaikan)."""
        out = []
        for s in self.scopes.get(kode, []):
            rl = s["ruang_lingkup"].strip()
            if rl.lower() not in ("seluruh", "seluruhnya") and rl not in out:
                out.append(rl)
        return out


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
