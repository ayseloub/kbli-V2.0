"""Pipeline dokumen knowledge base: ekstrak -> bersihkan -> struktur -> potong (chunk).

Dirancang untuk regulasi Indonesia (PP, Permen, Perpres, SOP): struktur BAB / Bagian /
Paragraf / Pasal dikenali supaya setiap potongan membawa "alamat"-nya (mis.
"BAB II › Pasal 5") dan jawaban chatbot bisa menyebut sumber yang bisa dicek.

  pages  = extract(path, mime)          # list[Page]
  pages  = clean_pages(pages)           # buang header/footer berulang, nomor halaman, dll.
  chunks = chunk_document(pages, title) # list[DocChunk]
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .preprocess import CHAT_STOPWORDS

SUPPORTED_EXT = {".pdf": "application/pdf",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".txt": "text/plain", ".md": "text/markdown"}

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿­]")
_HMARK = "\x00H"          # penanda heading dari DOCX/Markdown: "\x00H2 Judul"


class DocumentError(Exception):
    """Error yang pesannya aman ditampilkan ke admin."""


@dataclass
class Page:
    no: int
    text: str


@dataclass
class DocChunk:
    index: int
    content: str
    heading_path: list[str]
    page_start: int
    page_end: int
    embed_text: str = ""
    search_text: str = ""
    word_count: int = 0
    metadata: dict = field(default_factory=dict)


# ===================================================================== extract
def extract(path: str | Path, mime: str | None = None) -> list[Page]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        return _extract_pdf(p)
    if ext == ".docx":
        return _extract_docx(p)
    if ext in (".txt", ".md"):
        raw = p.read_bytes().decode("utf-8", errors="replace")
        if ext == ".md":
            raw = re.sub(r"(?m)^(#{1,4})\s+(.+)$", lambda m: f"{_HMARK}{len(m.group(1))} {m.group(2)}", raw)
        return [Page(i + 1, t) for i, t in enumerate(raw.split("\f"))]
    raise DocumentError(f"Format {ext or mime} belum didukung. Pakai PDF, DOCX, TXT, atau MD.")


def _extract_pdf(p: Path) -> list[Page]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(p))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise DocumentError("PDF terkunci password.") from e
        pages = [Page(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    except PdfReadError as e:
        raise DocumentError(f"PDF rusak atau tidak bisa dibaca: {e}") from e
    if not pages:
        raise DocumentError("PDF tidak punya halaman.")
    chars = sum(len(pg.text.strip()) for pg in pages)
    if chars / len(pages) < 40:
        raise DocumentError("PDF ini sepertinya hasil scan (berisi gambar), teksnya tidak bisa diambil. "
                            "Butuh OCR dulu, atau upload versi PDF yang teksnya bisa diblok.")
    return pages


def _extract_docx(p: Path) -> list[Page]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        d = docx.Document(str(p))
    except Exception as e:  # noqa: BLE001
        raise DocumentError(f"DOCX tidak bisa dibuka: {e}") from e
    lines: list[str] = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(el, d)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "") if para.style is not None else ""
            m = re.match(r"(?:Heading|Judul)\s*(\d)", style)
            lines.append(f"{_HMARK}{m.group(1)} {text}" if m else text)
        elif tag == "tbl":
            for row in Table(el, d).rows:
                cells = []
                for c in row.cells:
                    t = " ".join(c.text.split())
                    if t and (not cells or cells[-1] != t):   # sel gabungan terulang
                        cells.append(t)
                if cells:
                    lines.append(" | ".join(cells))
    if not lines:
        raise DocumentError("DOCX kosong.")
    return [Page(1, "\n".join(lines))]


# ======================================================================= clean
_RE_PAGENUM = re.compile(
    r"^\s*(?:[-–—]\s*\d{1,4}\s*[-–—]|\d{1,4}|(?:halaman|hal\.?|page)\s*\d{1,4}(?:\s*(?:dari|of|/)\s*\d{1,4})?"
    r"|\d{1,4}\s*/\s*\d{1,4})\s*$", re.IGNORECASE)


def _norm_line(s: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", s.strip().lower()))


def clean_pages(pages: list[Page]) -> list[Page]:
    """Rapikan teks per halaman tanpa mengubah isi pasal."""
    split = []
    for pg in pages:
        t = unicodedata.normalize("NFKC", pg.text)
        t = _INVISIBLE.sub("", t).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
        split.append([ln.rstrip() for ln in t.split("\n")])

    # Header/footer berulang: baris di 3 teratas / 3 terbawah yang muncul di >= 50% halaman
    repeated: set[str] = set()
    if len(split) >= 3:
        edge = Counter()
        for lines in split:
            nonempty = [ln for ln in lines if ln.strip()]
            for ln in set(_norm_line(x) for x in nonempty[:3] + nonempty[-3:]):
                if ln:
                    edge[ln] += 1
        repeated = {ln for ln, n in edge.items() if n >= max(3, len(split) * 0.5)}

    out = []
    for pg, lines in zip(pages, split):
        kept = [ln for ln in lines
                if ln.strip() and not _RE_PAGENUM.match(ln) and _norm_line(ln) not in repeated]
        text = "\n".join(re.sub(r"[ ]{2,}", " ", ln.strip()) for ln in kept)
        text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)        # "perizin-\nan" -> "perizinan"
        out.append(Page(pg.no, text))
    return out


# =================================================================== structure
_HEADINGS = [  # (level, regex, butuh judul di baris berikut?)
    (1, re.compile(r"^BAB\s+(?:[IVXLC]+|\d+)\b\.?$", re.IGNORECASE), True),
    (2, re.compile(r"^Bagian\s+(?:Ke\w+|\w+)\s*$", re.IGNORECASE), True),
    (3, re.compile(r"^Paragraf\s+\d+\s*$", re.IGNORECASE), True),
    (4, re.compile(r"^Pasal\s+\d+[A-Z]?\s*$", re.IGNORECASE), False),
]
_RE_LIST_START = re.compile(r"^(\(\d+\)|\(?[a-z]\)|\d+\.|[a-z]\.|[-•▪●*]\s)")


@dataclass
class _Para:
    text: str
    page: int
    path: tuple[str, ...]


def _heading(line: str) -> tuple[int, bool] | None:
    if line.startswith(_HMARK):
        return int(line[2]), False
    for level, rx, needs_title in _HEADINGS:
        if rx.match(line):
            return level, needs_title
    return None


def _is_title_line(line: str) -> bool:
    return (len(line) <= 120 and not line.endswith((".", ";", ":", ","))
            and _heading(line) is None and not _RE_LIST_START.match(line))


def structure(pages: list[Page]) -> list[_Para]:
    """Ubah baris menjadi paragraf yang tahu posisinya di hierarki BAB/Pasal."""
    items = [(pg.no, ln) for pg in pages for ln in pg.text.split("\n") if ln.strip()]
    path: dict[int, str] = {}
    paras: list[_Para] = []
    buf: list[str] = []
    buf_page = 1

    def cur_path() -> tuple[str, ...]:
        return tuple(path[k] for k in sorted(path))

    def flush():
        nonlocal buf
        if buf:
            paras.append(_Para(" ".join(buf), buf_page, cur_path()))
            buf = []

    i = 0
    while i < len(items):
        page, line = items[i]
        h = _heading(line)
        if h:
            flush()
            level, needs_title = h
            label = line[3:].strip() if line.startswith(_HMARK) else re.sub(r"\s+", " ", line).rstrip(".")
            if needs_title and i + 1 < len(items) and _is_title_line(items[i + 1][1]):
                label = f"{label} – {items[i + 1][1].strip()}"
                i += 1
            for k in [k for k in path if k >= level]:
                del path[k]
            path[level] = label[:160]
            i += 1
            continue
        # gabungkan baris yang terpotong di tengah kalimat (khas PDF)
        starts_new = (not buf or buf[-1].endswith((".", ":", ";", "?", "!"))
                      or bool(_RE_LIST_START.match(line)))
        if starts_new:
            flush()
            buf_page = page
        buf.append(line.strip())
        i += 1
    flush()
    return paras


# ======================================================================== chunk
def _words(s: str) -> list[str]:
    return s.split()


def _label(paths: list[tuple[str, ...]]) -> list[str]:
    """Alamat potongan: awalan bersama + rentang level terakhir ("Pasal 5 – Pasal 7")."""
    paths = [p for p in paths if p]
    if not paths:
        return []
    common: list[str] = []
    for parts in zip(*paths):
        if all(x == parts[0] for x in parts):
            common.append(parts[0])
        else:
            break
    firsts = [p[len(common)] for p in paths if len(p) > len(common)]
    if firsts:
        a, b = firsts[0], firsts[-1]
        common.append(a if a == b else f"{a.split(' – ')[0]} s.d. {b.split(' – ')[0]}")
    return common


def _split_long(p: _Para, max_words: int, overlap_words: int) -> list[_Para]:
    """Paragraf yang terlalu panjang dipecah per kalimat, lalu per kata kalau perlu."""
    if len(_words(p.text)) <= max_words:
        return [p]
    out: list[_Para] = []
    piece: list[str] = []
    for s in re.split(r"(?<=[.;:])\s+", p.text):
        ws = _words(s)
        while len(ws) > max_words:
            if piece:
                out.append(_Para(" ".join(piece), p.page, p.path))
                piece = []
            out.append(_Para(" ".join(ws[:max_words]), p.page, p.path))
            ws = ws[max_words - overlap_words:]
        if piece and len(_words(" ".join(piece))) + len(ws) > max_words:
            out.append(_Para(" ".join(piece), p.page, p.path))
            piece = []
        piece.append(" ".join(ws))
    if piece:
        out.append(_Para(" ".join(piece), p.page, p.path))
    return out


def chunk_document(pages: list[Page], title: str, max_words: int = 280, overlap_words: int = 40,
                   min_words: int = 25) -> list[DocChunk]:
    """Potong per struktur. Satu potongan tidak melintasi BAB; pasal pendek digabung.

    Potongan lanjutan di BAB yang sama diawali ±40 kata terakhir potongan sebelumnya
    (overlap), supaya kalimat di perbatasan tidak kehilangan konteks.
    """
    paras = [q for p in structure(pages) for q in _split_long(p, max_words, overlap_words)]
    if not paras:
        raise DocumentError("Tidak ada teks yang bisa diambil dari dokumen.")

    def bab(p: _Para) -> str:
        return p.path[0] if p.path else ""

    def pasal_of(p: _Para) -> str | None:
        return next((x for x in reversed(p.path) if x.lower().startswith("pasal")), None)

    # 1) kelompokkan paragraf menjadi calon potongan
    groups: list[tuple[list[_Para], str, str | None]] = []   # (paragraf, overlap, pasal overlap)
    cur: list[_Para] = []
    carry, carry_pasal = "", None
    for p in paras:
        n_cur = sum(len(_words(x.text)) for x in cur)
        new_bab = bool(cur) and bab(p) != bab(cur[0])
        if cur and (new_bab or n_cur + len(_words(p.text)) > max_words):
            groups.append((cur, carry, carry_pasal))
            carry = "" if new_bab else " ".join(_words(cur[-1].text)[-overlap_words:])
            carry_pasal = None if new_bab else pasal_of(cur[-1])
            cur = []
        cur.append(p)
    if cur:
        groups.append((cur, carry, carry_pasal))

    # 2) tulis teks potongan (judul Pasal disisipkan supaya terbaca natural)
    chunks: list[DocChunk] = []
    for members, carry, carry_pasal in groups:
        body: list[str] = []
        if carry:
            body += [carry_pasal] if carry_pasal else []
            body.append("… " + carry)
        last_pasal = carry_pasal if carry else None
        for p in members:
            pasal = pasal_of(p)
            if pasal and pasal != last_pasal:
                body.append(pasal)
                last_pasal = pasal
            body.append(p.text)
        content = "\n".join(body)
        path = _label([p.path for p in members])
        where = " › ".join(path)
        embed = "\n".join(x for x in (title, where, content) if x)
        chunks.append(DocChunk(
            index=len(chunks), content=content, heading_path=path,
            page_start=members[0].page, page_end=members[-1].page,
            embed_text=embed, search_text=embed.lower(), word_count=len(_words(content)),
            metadata={"has_overlap": bool(carry)}))

    # 3) potongan yang sangat pendek digabung ke tetangga di BAB yang sama
    merged: list[DocChunk] = []
    for c in chunks:
        prev = merged[-1] if merged else None
        if (prev and c.word_count < min_words and prev.heading_path[:1] == c.heading_path[:1]
                and prev.word_count + c.word_count <= max_words + min_words):
            prev.content += "\n" + c.content
            prev.page_end = c.page_end
            prev.word_count += c.word_count
            prev.embed_text += "\n" + c.content
            prev.search_text = prev.embed_text.lower()
        else:
            merged.append(c)
    for i, c in enumerate(merged):
        c.index = i
    return merged


# ===================================================================== search
def fts_query(question: str) -> str | None:
    """Ubah pertanyaan bebas jadi tsquery 'simple' (OR, prefiks untuk kata panjang)."""
    toks = re.findall(r"[a-z0-9]+", question.lower())
    stop = CHAT_STOPWORDS | {"dan", "atau", "yang", "di", "ke", "dari", "untuk", "dengan", "pada", "itu",
                             "ini", "ada", "adalah", "dalam", "akan", "harus", "berapa", "apa", "saja"}
    toks = [t for t in toks if t not in stop and len(t) > 1]
    if not toks:
        return None
    return " | ".join(f"{t}:*" if len(t) >= 5 else t for t in dict.fromkeys(toks))
