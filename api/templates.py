"""Jawaban berbasis template — dipakai untuk sebagian besar percakapan tanpa LLM."""
from __future__ import annotations

import random
import re

from kbli.preprocess import split_sentences, strip_boilerplate

_RE_GREETING = re.compile(
    r"^\s*(halo+|hal+o|hai+|hi+|hey|hello|helo|pagi|siang|sore|malam|selamat\s+(pagi|siang|sore|malam)|"
    r"assalamu'?alaikum|permisi|p)\b[\s!.,]*$", re.IGNORECASE)
_RE_THANKS = re.compile(r"\b(makasih|terima\s*kasih|thanks|thank you|thx|tq|mksh)\b", re.IGNORECASE)


# Pertanyaan pengetahuan/umum tanpa niat usaha ("apa itu blockchain", "terjemahkan ...").
# Kosakatanya bisa ada di KBLI, jadi BM25 tidak bisa membedakan — aturan ini menutup celahnya
# sampai topic gate berbasis IndoSBERT dikalibrasi.
_RE_KNOWLEDGE_Q = re.compile(
    r"^\s*(apa\s+itu|apa\s+arti|kenapa|mengapa|terjemahkan|translate|tulis(kan)?|buatkan|jelaskan\s+(teori|sejarah|rumus)|"
    r"hitung|berapa\s+hasil|siapa)\b", re.IGNORECASE)
_RE_BUSINESS = re.compile(
    r"\b(usaha|bisnis|jual|jualan|jasa|industri|pabrik|toko|produksi|buka|kbli|izin|nib|oss|dagang|ternak|budidaya|kode)\b",
    re.IGNORECASE)


# Pertanyaan soal aturan/prosedur perizinan -> dijawab dari dokumen knowledge base
_RE_DOC_QUESTION = re.compile(
    r"\b(syarat|persyaratan|prosedur|tahapan|langkah|cara\s+(daftar|mendaftar|mengurus|urus|membuat|bikin|mengajukan)|"
    r"izin\s+apa|perizinan|perijinan|aturan|peraturan|regulasi|ketentuan|pasal|undang|uu|pp|permen|perpres|"
    r"sanksi|denda|wajib|kewajiban|dokumen\s+apa|berapa\s+(lama|biaya)|biaya|tarif|masa\s+berlaku|"
    r"sertifikat\s+standar|tingkat\s+risiko|risiko\s+(rendah|menengah|tinggi)|nib|oss|amdal|ukl|upl|sppl|pbg|slf|"
    r"umk|umkm|modal\s+minimal|pma|pmdn)\b", re.IGNORECASE)


def is_doc_question(text: str) -> bool:
    return bool(_RE_DOC_QUESTION.search(text))


def is_offtopic_question(text: str) -> bool:
    return bool(_RE_KNOWLEDGE_Q.match(text)) and not _RE_BUSINESS.search(text)


def detect_smalltalk(text: str) -> str | None:
    if _RE_GREETING.match(text):
        return "greeting"
    if _RE_THANKS.search(text) and len(text) < 60:
        return "thanks"
    return None


GREETINGS = [
    "Halo! Saya dapat membantu Anda mencari kode KBLI yang sesuai. Silakan ceritakan kegiatan usaha Anda, misalnya \"jualan kue kering lewat Instagram\".",
    "Selamat datang! Jelaskan kegiatan usaha Anda sejelas mungkin, dan saya akan mencarikan kode KBLI yang paling sesuai.",
]
THANKS = [
    "Sama-sama! Jika ada kegiatan usaha lain yang ingin dicek kodenya, silakan tuliskan.",
    "Senang dapat membantu. Semoga proses perizinan Anda lancar!",
]
REFUSE = [
    "Mohon maaf, saya khusus membantu seputar KBLI dan perizinan berusaha. Silakan ceritakan usaha yang ingin Anda daftarkan.",
    "Mohon maaf, pertanyaan tersebut di luar layanan ini. Saya dapat membantu mencari kode KBLI, misalnya \"usaha laundry kiloan\" atau \"produksi keripik pisang\".",
]


def pick(options: list[str]) -> str:
    return random.choice(options)


def summarize_uraian(uraian: str | None, max_chars: int = 220) -> str | None:
    if not uraian:
        return None
    first = split_sentences(uraian)
    if not first:
        return None
    s = strip_boilerplate(first[0])
    s = s[:1].upper() + s[1:]
    return s if len(s) <= max_chars else s[:max_chars].rsplit(" ", 1)[0] + "…"


def answer_confident(top: dict, n_more: int = 0, opener: str | None = None) -> str:
    lines = [opener or f"Usaha Anda paling sesuai dengan **KBLI {top['kode']} – {top['judul']}**."]
    if top.get("ringkasan"):
        lines.append(f"Kode ini mencakup: {top['ringkasan']}")
    if top.get("batasan"):
        lines.append("Perhatikan batasan ruang lingkupnya: " + "; ".join(top["batasan"]) + ".")
    if n_more:
        lines.append(f"Jika kurang sesuai, silakan pilih salah satu dari {n_more} kode lain yang mirip di bawah.")
    return "\n\n".join(lines)


def answer_clarify(n: int = 5) -> str:
    return pick([
        f"Terdapat {n} kode yang mirip. Mana yang paling menggambarkan usaha Anda?",
        f"Saya menemukan {n} kemungkinan, diurutkan dari yang paling mendekati. Silakan pilih yang paling sesuai dengan kegiatan usaha Anda:",
    ])


def answer_selected(item: dict) -> str:
    return answer_confident(item, opener=f"Anda memilih **KBLI {item['kode']} – {item['judul']}**.")


def answer_docs_excerpt(n: int) -> str:
    return (f"Saya menemukan {n} bagian dokumen yang paling berkaitan. Berikut kutipan aslinya "
            "beserta sumbernya:" if n > 1 else
            "Saya menemukan bagian dokumen yang berkaitan. Berikut kutipan aslinya:")


def answer_docs_none() -> str:
    return ("Mohon maaf, dokumen yang tersedia belum membahas hal tersebut. Silakan coba dengan kata lain, "
            "atau periksa langsung di oss.go.id.")


def answer_llm_unavailable() -> str:
    return ("Penjelasan detail sedang tidak tersedia (kuota harian habis atau layanan AI sibuk). "
            "Informasi di atas sudah diambil langsung dari uraian resmi KBLI.")
