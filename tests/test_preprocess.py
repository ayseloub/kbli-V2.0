"""Test preprocessing memakai kasus nyata dari data lampiran & OSS."""
import pytest

from kbli.preprocess import (
    chunk_kbli,
    classify_sentence,
    clean_text,
    normalize_kode,
    preprocess_bm25,
    preprocess_dense,
    split_sentences,
    strip_boilerplate,
)


@pytest.mark.parametrize("raw,expected", [
    ("‭56101‬", "56101"),   # karakter bidi tersembunyi dari Excel lama
    ("2111", "02111"),                # nol depan hilang
    (1110, "01110"),
    (" 47911 ", "47911"),
    ("123", None),
    ("", None),
    (None, None),
])
def test_normalize_kode(raw, expected):
    assert normalize_kode(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Industri Baterai Untuk Kendara -an Bermotor Listrik", "Industri Baterai Untuk Kendaraan Bermotor Listrik"),
    ("Industri Peralat-an Kedokteran", "Industri Peralatan Kedokteran"),
    ("Repara-si Alat Ukur", "Reparasi Alat Ukur"),
    ("pertanian biji-bijian penghasil minyak", "pertanian biji-bijian penghasil minyak"),
    ("Industri Mainan Anak-anak", "Industri Mainan Anak-anak"),
    ("Industri Kosmetik untuk Manusia , termasuk Pasta Gigi", "Industri Kosmetik untuk Manusia, termasuk Pasta Gigi"),
    ("Industri Pita Mesin Tulis/ Gambar", "Industri Pita Mesin Tulis/Gambar"),
    ("pengolahan lahan, penanaman,; pemeliharaan", "pengolahan lahan, penanaman, pemeliharaan"),
    ("Kelompok  ini​ mencakup", "Kelompok ini mencakup"),
    ("non-woven dan e-commerce", "non-woven dan e-commerce"),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_split_sentences_protects_abbreviations():
    text = ("Kelompok ini mencakup usaha industri minuman lainnya yang tidak termasuk dalam "
            "subgolongan 1101 s.d. 1105, seperti nira dan air tebu. Termasuk serbuk sekoteng.")
    sents = split_sentences(text)
    assert len(sents) == 2
    assert "s.d. 1105" in sents[0]


def test_split_long_sentence_on_semicolon():
    long = "Kelompok ini mencakup " + "; ".join(f"kegiatan nomor {i} yang cukup panjang deskripsinya" for i in range(12)) + "."
    assert len(split_sentences(long)) > 1


@pytest.mark.parametrize("sentence,label", [
    ("Kecuali pembuatan karung plastik masuk dalam kelompok 22220.", "pengecualian"),
    ("Sajadah/penutup lantai dari karpet/permadani dimasukkan dalam kelompok 13930.", "pengecualian"),
    ("Pembuatan kertas fotografi dimasukkan dalam kelompok 20299.", "pengecualian"),
    ("Jaring ikan dikelompokkan ke dalam 13942.", "pengecualian"),
    ("Subgolongan ini tidak mencakup produksi, reproduksi, distribusi buku dan musik.", "pengecualian"),
    ("Kelompok ini mencakup usaha pembuatan karung goni.", "inti"),
    ("Termasuk industri kretek mesin.", "inti"),
])
def test_classify_sentence(sentence, label):
    assert classify_sentence(sentence) == label


def test_strip_boilerplate():
    assert strip_boilerplate("Kelompok ini mencakup usaha pembuatan karung goni.") == "usaha pembuatan karung goni."
    assert strip_boilerplate("Subgolongan ini juga mencakup aktivitas penulisan teknis.") == "aktivitas penulisan teknis."


def test_preprocess_bm25_stems_and_drops_stopwords():
    toks = preprocess_bm25("Kelompok ini mencakup usaha perdagangan eceran pakaian melalui internet")
    assert "dagang" in toks and "ecer" in toks and "pakai" in toks
    assert "kelompok" not in toks and "usaha" not in toks and "ini" not in toks


def test_query_and_document_use_same_tokens():
    # aturan wajib: query diproses dengan fungsi yang sama
    assert preprocess_bm25("Perdagangan Eceran") == preprocess_bm25("perdagangan  eceran")


def test_preprocess_dense_keeps_context():
    out = preprocess_dense("Kelompok ini mencakup usaha industri lainnya YTDL.")
    assert out == "usaha industri lainnya yang tidak diklasifikasikan di tempat lain."


def test_chunk_kbli_separates_exclusions():
    chunks = chunk_kbli(
        "13926",
        "Industri Karung Bukan Goni",
        "Kelompok ini mencakup usaha pembuatan karung bagor (karung terigu/gula blacu) dan karung lainnya. "
        "Kecuali pembuatan karung plastik masuk dalam kelompok 22220.",
    )
    types = [c.chunk_type for c in chunks]
    assert types == ["judul", "uraian", "pengecualian"]
    assert chunks[0].weight > 1
    assert chunks[1].embed_text.startswith("Industri Karung Bukan Goni: usaha pembuatan karung bagor")
    assert "plastik" in chunks[2].content


def test_chunk_kbli_without_uraian_still_has_title():
    chunks = chunk_kbli("19213", "Industri Pengolahan Kembali Minyak Pelumas Bekas", None)
    assert len(chunks) == 1 and chunks[0].chunk_type == "judul"


def test_section_lists_after_tidak_mencakup_are_exclusions():
    uraian = ("Kelompok ini mencakup layanan konsultan, tetapi tidak mencakup bisnis rutin berdurasi pendek.\n"
              "Kelompok ini mencakup\n- audit tagihan dan informasi tarif angkutan;\n- konsultasi keamanan;\n"
              "Kelompok ini tidak mencakup\n- aktivitas pembukuan;\n- konsultansi dokter medis, lihat subgolongan 8620.")
    chunks = chunk_kbli("74909", "Aktivitas Profesional Lainnya", uraian)
    by_type = {t: [c.content for c in chunks if c.chunk_type == t] for t in ("uraian", "pengecualian")}
    assert any("bisnis rutin" in c for c in by_type["uraian"])       # "tetapi tidak mencakup" di tengah kalimat = inti
    assert any("konsultasi keamanan" in c for c in by_type["uraian"])
    assert any("pembukuan" in c for c in by_type["pengecualian"])    # tanpa kata "lihat", tetap pengecualian
    assert not any(c.lower().startswith("kelompok ini") and len(c) < 30 for c in by_type["uraian"])


def test_intent_service_nouns_penalize_industry_and_trade():
    from kbli.retrieval import KbliIndex
    idx = KbliIndex.load("data/processed")
    f = idx.intent_factors("usaha laundry kiloan", 0.7)
    pos = idx.kode_pos
    assert f[pos["28262"]] == 0.7      # Industri Mesin Cuci -> turun
    assert f[pos["96200"]] == 1.0      # Aktivitas penatu -> tetap
    # niat campur (jasa + jual) -> tidak ada penalti
    assert (idx.intent_factors("jual dan servis laptop", 0.7) == 1.0).all()
