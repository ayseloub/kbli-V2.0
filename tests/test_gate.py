"""Topic gate: gabungan coverage BM25 + aturan pertanyaan umum harus memisahkan sampel."""
import json
from pathlib import Path

from api.templates import is_offtopic_question
from kbli.config import Settings
from kbli.retrieval import KbliIndex

INDEX = KbliIndex.load("data/processed")
DATA = json.loads(Path("eval/topic_gate.json").read_text(encoding="utf-8"))
S = Settings(_env_file=None)


def gated_out(q: str) -> bool:
    return is_offtopic_question(q) or INDEX.search(q, settings=S).confidence == "luar_topik"


def test_no_legit_question_rejected():
    rejected = [q for q in DATA["in_scope"] if gated_out(q)]
    assert rejected == []


def test_most_offtopic_rejected():
    passed = [q for q in DATA["out_of_scope"] if not gated_out(q)]
    assert len(passed) <= 1, passed


def test_business_phrasing_is_not_blocked_by_rule():
    assert not is_offtopic_question("apa itu kbli untuk usaha laundry")
    assert not is_offtopic_question("kenapa jualan online butuh izin")


# Regresi: pertanyaan gaya pengguna asli (bukan dari data uji skripsi)
import pytest  # noqa: E402


@pytest.mark.parametrize("q,kode", [
    ("usaha laundry kiloan", "96200"),
    ("buka londri dekat kampus", "96200"),
    ("jualan MLM", "47999"),
    ("bengkel servis motor", "45407"),
])
def test_user_style_queries_bm25_top1(q, kode):
    assert INDEX.search(q, mode="bm25", settings=S).candidates[0].kode == kode
