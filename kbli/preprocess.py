"""Cleaning dan preprocessing teks KBLI.

Tiga tahap, dipakai bersama oleh pipeline offline dan API:

  Tahap 1 (record)  : normalize_kode
  Tahap 2 (teks)    : clean_text, split_sentences, classify_sentence, strip_boilerplate
  Tahap 3 (model)   : preprocess_bm25  -> token untuk BM25 (stem + stopword)
                      preprocess_dense -> kalimat untuk IndoSBERT (pembersihan minimal)

Aturan penting: query user WAJIB diproses dengan preprocess_bm25 /
preprocess_dense yang sama seperti dokumen.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# ---------------------------------------------------------------------------
# Tahap 1 — record
# ---------------------------------------------------------------------------

def normalize_kode(raw: object) -> str | None:
    """'‭56101‬' -> '56101', '2111' -> '02111', '123' -> None."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 4:          # nol depan hilang saat dibaca sebagai angka
        digits = digits.zfill(5)
    return digits if len(digits) == 5 else None


# ---------------------------------------------------------------------------
# Tahap 2 — cleaning teks (hasilnya boleh ditampilkan ke user)
# ---------------------------------------------------------------------------

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿­]")

# Akhiran yang lazim terpotong di PDF: "Peralat-an", "Repara-si", "Kendara -an"
_SPLIT_SUFFIX = r"(an|kan|nya|si|i|ah|er|ai|ri|ogi|ng|g|r|n)"
_RE_SPACE_HYPHEN = re.compile(r"(\w) -(\w)")
_RE_HYPHEN_SUFFIX = re.compile(rf"\b(\w{{3,}})-{_SPLIT_SUFFIX}\b")


def _fix_hyphen_suffix(m: re.Match) -> str:
    head, tail = m.group(1), m.group(2)
    # Kata ulang ("biji-bijian", "anak-anak") tetap pakai tanda hubung.
    if tail.lower().startswith(head.lower()[:3]):
        return m.group(0)
    return head + tail


def clean_text(text: str | None) -> str:
    """Normalisasi aman: tidak mengubah makna, hanya merapikan artefak."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = t.replace("_x000D_", "")                       # escape CR dari Excel
    t = _INVISIBLE.sub("", t)
    t = t.replace("\r", "\n")
    t = _RE_SPACE_HYPHEN.sub(r"\1\2", t)               # "Kendara -an" -> "Kendaraan"
    t = _RE_HYPHEN_SUFFIX.sub(_fix_hyphen_suffix, t)   # "Peralat-an" -> "Peralatan"
    t = re.sub(r",\s*;", ",", t)                       # ", ;" artefak gabungan baris
    t = re.sub(r"(\w)/ (\w)", r"\1/\2", t)             # "Tulis/ Gambar"
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s+([,.;:)])", r"\1", t)              # spasi sebelum tanda baca
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


# Singkatan yang tidak boleh memotong kalimat
_ABBR_PROTECT = ["s.d.", "s/d.", "dll.", "dsb.", "dst.", "Kab.", "No.", "Tbk.", "Ir.", "Dr.", "a.l.", "u.p."]


def split_sentences(text: str) -> list[str]:
    """Pecah uraian per kalimat. Kalimat sangat panjang dipecah lagi di ';'."""
    t = clean_text(text)
    if not t:
        return []
    for i, abbr in enumerate(_ABBR_PROTECT):
        t = t.replace(abbr, f"\x00{i}\x00")
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])|\n+", t)
    out: list[str] = []
    for p in parts:
        for i, abbr in enumerate(_ABBR_PROTECT):
            p = p.replace(f"\x00{i}\x00", abbr)
        p = p.strip(" -•")
        if not p:
            continue
        if len(p) > 350 and ";" in p:          # batas aman untuk SBERT
            out.extend(s.strip() for s in p.split(";") if s.strip())
        else:
            out.append(p)
    # gabungkan potongan yang terlalu pendek ke kalimat sebelumnya (kecuali judul bagian)
    merged: list[str] = []
    for s in out:
        if merged and len(s) < 25 and not _is_exclusion(s) and not section_header(s):
            merged[-1] = f"{merged[-1]} {s}"
        else:
            merged.append(s)
    return merged


_EXCLUSION_PATTERNS = [
    r"dimasukkan\s+(ke\s+)?(dalam\s+)?(kelompok|golongan|subgolongan|kategori)",
    r"dikelompokkan\s+(ke\s+)?(dalam\s+)?(kelompok|golongan|subgolongan)?\s*\d",
    r"diklasifikasikan\s+(ke\s+)?(dalam|pada)\s+(kelompok|golongan|subgolongan)",
    r"masuk\s+(ke\s+)?(dalam\s+)?(kelompok|golongan|subgolongan)\s+\d",
    r"termasuk\s+(dalam\s+)?(kelompok|golongan|subgolongan)\s+\d",
    r"\b(lihat|lht\.?)\s+(kelompok|golongan|subgolongan|kategori)",
    r"^(kelompok|subgolongan|golongan)\s+ini\s+tidak\s+mencakup\b",
]
_RE_EXCLUSION = re.compile("|".join(_EXCLUSION_PATTERNS), re.IGNORECASE)


def _is_exclusion(sentence: str) -> bool:
    return bool(_RE_EXCLUSION.search(sentence))


def classify_sentence(sentence: str) -> str:
    """'pengecualian' untuk kalimat rujukan ke kode lain, selain itu 'inti'."""
    return "pengecualian" if _is_exclusion(sentence) else "inti"


_RE_SECTION = re.compile(
    r"^(kelompok|subgolongan|golongan)\s+ini\s+(juga\s+|tidak\s+)?(mencakup|meliputi)\s*:?\s*$",
    re.IGNORECASE,
)


def section_header(line: str) -> str | None:
    """'Kelompok ini tidak mencakup' -> 'pengecualian'; 'Kelompok ini (juga) mencakup' -> 'inti'."""
    m = _RE_SECTION.match(line.strip())
    if not m:
        return None
    return "pengecualian" if m.group(2) and m.group(2).strip().lower() == "tidak" else "inti"


_RE_BOILERPLATE = re.compile(
    r"^(kelompok|subgolongan|golongan)\s+ini\s+(juga\s+)?(mencakup|meliputi)\s*:?\s*",
    re.IGNORECASE,
)


def strip_boilerplate(sentence: str) -> str:
    """'Kelompok ini mencakup usaha pembuatan karung goni.' -> 'usaha pembuatan karung goni.'"""
    return _RE_BOILERPLATE.sub("", sentence).strip()


# ---------------------------------------------------------------------------
# Tahap 3 — preprocessing per model
# ---------------------------------------------------------------------------

ABBREVIATIONS = {
    "ytdl": "yang tidak diklasifikasikan di tempat lain",
    "ybdi": "yang berhubungan dengan itu",
    "ppmse": "penyelenggara perdagangan melalui sistem elektronik",
    "psp": "penyelenggara sarana perantara",
    "umkm": "usaha mikro kecil menengah",
    "dll": "dan lain-lain",
    "dsb": "dan sebagainya",
    "s.d.": "sampai dengan",
    "s/d": "sampai dengan",
    "atbm": "alat tenun bukan mesin",
    "olshop": "toko online",
}
_RE_ABBR = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(k) for k in sorted(ABBREVIATIONS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)


def expand_abbreviations(text: str) -> str:
    return _RE_ABBR.sub(lambda m: ABBREVIATIONS[m.group(1).lower()], text)


# Stopword domain: muncul di hampir semua uraian sehingga tidak membedakan kode
DOMAIN_STOPWORDS = {
    "kelompok", "golongan", "subgolongan", "mencakup", "meliputi", "kegiatan", "usaha",
    "termasuk", "seperti", "lainnya", "lain", "macam", "berbagai", "jenis", "sejenis",
    "sejenisnya", "baik", "maupun", "tersebut", "misalnya", "antara", "yaitu",
    "cara", "buat", "kode", "kbli",
}
# Kata percakapan yang muncul di pertanyaan user tapi tidak membawa makna usaha
CHAT_STOPWORDS = {
    "gimana", "bagaimana", "gmn", "tolong", "dong", "sih", "nih", "deh", "kok", "ya", "yg",
    "gan", "kak", "min", "bang", "mas", "mbak", "bro", "sis", "aku", "saya", "gue", "gw",
    "kamu", "anda", "mau", "ingin", "pengen", "pingin", "siapa", "apa", "apakah", "kenapa",
    "mengapa", "kapan", "dimana", "mana", "sekarang", "nanti", "bisa", "boleh", "punya",
    "tau", "tahu", "kasih", "bantu", "cari", "mencari", "cariin", "carikan", "buatin", "minta", "jelasin", "jelaskan",
    "enak", "bagus", "terbaik", "paling", "kayak", "kaya", "aja", "saja", "udah", "sudah",
}


@lru_cache(maxsize=1)
def _sastrawi():
    from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
    from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory

    stemmer = StemmerFactory().create_stemmer()
    stopwords = set(StopWordRemoverFactory().get_stop_words()) | DOMAIN_STOPWORDS | CHAT_STOPWORDS
    return stemmer, stopwords


# Sastrawi lambat (~30-40 ms per kata baru), jadi hasil stemming di-cache
# di memori dan bisa disimpan/dimuat dari disk (dibangun saat pipeline offline).
_STEM_CACHE: dict[str, str] = {}


def load_stem_cache(path) -> int:
    import json
    from pathlib import Path

    p = Path(path)
    if p.exists():
        _STEM_CACHE.update(json.loads(p.read_text(encoding="utf-8")))
    return len(_STEM_CACHE)


def save_stem_cache(path) -> int:
    import json
    from pathlib import Path

    Path(path).write_text(json.dumps(_STEM_CACHE, ensure_ascii=False), encoding="utf-8")
    return len(_STEM_CACHE)


def _stem(token: str) -> str:
    hit = _STEM_CACHE.get(token)
    if hit is None:
        stemmer, _ = _sastrawi()
        hit = _STEM_CACHE[token] = stemmer.stem(token)
    return hit


def preprocess_bm25(text: str) -> list[str]:
    """Case folding -> ekspansi singkatan -> buang non-huruf -> stopword -> stemming."""
    _, stopwords = _sastrawi()
    t = expand_abbreviations(clean_text(text).lower())
    t = t.replace("-", " ")
    tokens = re.findall(r"[a-z]+", t)
    out = []
    for tok in tokens:
        if len(tok) < 2 or tok in stopwords:
            continue
        st = _stem(tok)
        if st and st not in stopwords:
            out.append(st)
    return out


def preprocess_dense(text: str) -> str:
    """Pembersihan minimal untuk IndoSBERT: konteks kalimat dipertahankan."""
    t = clean_text(text)
    t = strip_boilerplate(t)
    t = expand_abbreviations(t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# Chunking satu kode KBLI
# ---------------------------------------------------------------------------

@dataclass
class KbliChunk:
    kode: str
    chunk_type: str          # judul | uraian | pengecualian
    chunk_index: int
    content: str             # teks asli (ditampilkan)
    embed_text: str          # input IndoSBERT
    search_tokens: list[str] = field(default_factory=list)  # input BM25
    weight: float = 1.0

    @property
    def search_text(self) -> str:
        return " ".join(self.search_tokens)


TITLE_WEIGHT = 1.3


def chunk_kbli(kode: str, judul: str, uraian: str | None) -> list[KbliChunk]:
    judul_c = clean_text(judul)
    chunks = [KbliChunk(kode, "judul", 0, judul_c, preprocess_dense(judul_c),
                        preprocess_bm25(judul_c), TITLE_WEIGHT)]
    idx = {"uraian": 0, "pengecualian": 0}
    section = "inti"   # daftar di bawah "Kelompok ini tidak mencakup" = pengecualian semua
    for sent in split_sentences(uraian or ""):
        header = section_header(sent)
        if header:
            section = header
            continue
        excl = section == "pengecualian" or classify_sentence(sent) == "pengecualian"
        ctype = "pengecualian" if excl else "uraian"
        dense = preprocess_dense(sent)
        if not dense:
            continue
        # judul ditempelkan sebagai konteks agar kalimat pendek tetap bermakna
        embed = dense if ctype == "pengecualian" else f"{judul_c}: {dense}"
        chunks.append(KbliChunk(kode, ctype, idx[ctype], sent, embed, preprocess_bm25(sent)))
        idx[ctype] += 1
    return chunks


# Frasa permintaan di awal query ("Cari kode KBLI untuk usaha yang ...") tidak membawa
# makna usaha tapi menggeser vektor SBERT; dibuang khusus untuk jalur dense.
_RE_REQUEST = re.compile(
    r"^\s*(tolong\s+|saya\s+|aku\s+|bantu\s+)?"
    r"(mencari|cari(kan|in)?|ingin\s+mencari|mau\s+cari|buatin|carikan)?\s*"
    r"(kode\s*(kbli)?|kbli)?\s*(apa\s+yang\s+(cocok|digunakan|dipakai)\s+)?"
    r"(untuk|buat|bagi|yang\s+(cocok|berkaitan\s+dengan|digunakan\s+untuk))?\s*"
    r"((sebuah\s+)?(usaha|bisnis|perusahaan)\s+(yang\s+)?)?",
    re.IGNORECASE,
)


def preprocess_query_dense(text: str) -> str:
    t = clean_text(text)
    stripped = _RE_REQUEST.sub("", t, count=1).strip()
    if len(stripped) >= 8:          # jangan sampai query habis
        t = stripped
    return preprocess_dense(t)
