"""Manajemen data KBLI: versi, embedding inkremental, impor/ekspor Excel, rollback, hot-reload."""
import io
import json
import time
from pathlib import Path

import fakeredis
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.kbli_data import DataError, KbliData
from api.main import build_state, create_app
from api.store import MemoryStore
from kbli.config import Settings
from kbli.embedder import HashingEmbedder
from kbli.preprocess import chunk_kbli
from dataclasses import asdict

SEED = [
    ("96200", "Aktivitas Penatu", "Kelompok ini mencakup usaha jasa pencucian dan dry cleaning pakaian."),
    ("11052", "Industri Air Minum Isi Ulang", "Kelompok ini mencakup usaha depot air minum isi ulang."),
    ("47711", "Perdagangan Eceran Pakaian", "Kelompok ini mencakup usaha perdagangan eceran pakaian jadi."),
]


def make_seed(d: Path, emb: HashingEmbedder | None):
    d.mkdir(parents=True)
    codes, chunks = [], []
    for k, j, u in SEED:
        codes.append({"kode": k, "judul": j, "uraian": u, "uraian_source": "oss", "oss_detail_uuid": None,
                      "kata_kunci": [], "sektor": ["umum"]})
        for ch in chunk_kbli(k, j, u):
            x = asdict(ch)
            x["search_text"] = ch.search_text
            del x["search_tokens"]
            chunks.append(x)
    for name, rows in [("kbli_codes", codes), ("kbli_chunks", chunks),
                       ("kbli_scopes", [{"kode": k, "sektor": "umum", "ruang_lingkup": "Seluruh"} for k, *_ in SEED])]:
        (d / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    if emb:
        from pipeline.embed_chunks import doc_texts
        np.save(d / "kbli_chunk_embeddings.npy", emb.encode([c["embed_text"] for c in chunks]))
        np.save(d / "kbli_doc_embeddings.npy", emb.encode(doc_texts(codes, chunks)))
        (d / "kbli_chunk_embeddings.json").write_text(json.dumps(
            {"model": emb.name, "dim": emb.dim, "n_chunks": len(chunks), "n_docs": len(codes)}))


@pytest.fixture()
def kdata(tmp_path):
    emb = HashingEmbedder(64)
    make_seed(tmp_path / "seed", emb)
    return KbliData(tmp_path / "versions", tmp_path / "seed", embedder=emb)


def _rows_by_kode(data):
    out = {}
    for i, ch in enumerate(data["kbli_chunks"]):
        out.setdefault(ch["kode"], []).append(data["chunk_emb"][i])
    return out


def test_keyword_edit_reuses_all_embeddings(kdata):
    before = kdata.load()
    kdata.save_code("96200", {"kata_kunci": ["Laundry", "londri", " laundry "]}, "u1")
    after = kdata.load()
    assert after["kbli_codes"][0]["kata_kunci"] == ["laundry", "londri"]
    assert np.array_equal(before["chunk_emb"], after["chunk_emb"])
    v = kdata.versions()[0]
    assert v["active"] and v["stats"]["chunk_dihitung_ulang"] == 0 and v["created_by"] == "u1"


def test_text_edit_reembeds_only_that_code(kdata):
    before = _rows_by_kode(kdata.load())
    kdata.save_code("11052", {"uraian": "Kelompok ini mencakup usaha pengisian ulang air minum galon."}, None)
    data = kdata.load()
    after = _rows_by_kode(data)
    for k in ("96200", "47711"):
        assert all(np.array_equal(a, b) for a, b in zip(before[k], after[k]))
    assert not np.array_equal(before["11052"][-1], after["11052"][-1])
    assert data["meta"]["n_chunks"] == len(data["kbli_chunks"]) == data["chunk_emb"].shape[0]
    assert data["doc_emb"].shape[0] == 3
    assert next(c for c in data["kbli_codes"] if c["kode"] == "11052")["uraian_source"] == "manual"


def test_add_delete_and_validation(kdata):
    kdata.save_code("10391", {"judul": "Industri Tempe Kedelai", "uraian": "Pembuatan tempe."}, None)
    assert [c["kode"] for c in kdata.load()["kbli_codes"]] == ["10391", "11052", "47711", "96200"]
    kdata.delete_code("47711", None)
    assert "47711" not in [c["kode"] for c in kdata.load()["kbli_codes"]]
    with pytest.raises(DataError):
        kdata.save_code("123", {"judul": "x"}, None)
    with pytest.raises(DataError):
        kdata.save_code("99999", {"uraian": "tanpa judul"}, None)
    with pytest.raises(DataError):
        kdata.delete_code("00000", None)


def test_rollback(kdata):
    kdata.save_code("96200", {"judul": "Aktivitas Penatu (Laundry)"}, None)
    first = kdata.current_id()
    kdata.save_code("96200", {"judul": "SALAH KETIK"}, None)
    assert kdata.get("96200")["judul"] == "SALAH KETIK"
    kdata.rollback(first, None)
    assert kdata.get("96200")["judul"] == "Aktivitas Penatu (Laundry)"
    kdata.rollback("awal", None)
    assert kdata.get("96200")["judul"] == "Aktivitas Penatu"


def test_export_import_roundtrip_and_preview(kdata):
    x = pd.read_excel(io.BytesIO(kdata.export_xlsx()), sheet_name=None, dtype=str)
    assert set(x) >= {"KBLI", "Ruang Lingkup", "Petunjuk"}
    df = x["KBLI"].fillna("")
    df.loc[df["Kode KBLI"] == "96200", "Kata Kunci"] = "laundry, binatu"
    df.loc[df["Kode KBLI"] == "11052", "Judul KBLI"] = "Industri Air Minum Dalam Kemasan Isi Ulang"
    df = df[df["Kode KBLI"] != "47711"]
    df.loc[len(df)] = ["10391", "Industri Tempe Kedelai", "Pembuatan tempe dari kedelai.", ""]
    df.loc[len(df)] = ["12", "Kode salah", "", ""]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as xw:
        df.to_excel(xw, sheet_name="KBLI", index=False)
    p = kdata.preview_import(buf.getvalue(), "baru.xlsx", "u1")
    assert p["added_count"] == 1 and p["removed_count"] == 1 and p["changed_count"] == 2
    assert p["reembed_count"] == 2 and p["errors"] and "bukan 5 digit" in p["errors"][0]
    assert {c["kode"]: c["fields"] for c in p["changed"]} == {"11052": ["judul"], "96200": ["kata_kunci"]}

    kdata.apply_import(p["token"], "u1")
    for _ in range(100):
        if kdata.job_status()["status"] != "running":
            break
        time.sleep(0.05)
    assert kdata.job_status()["status"] == "succeeded"
    codes = {c["kode"]: c for c in kdata.load()["kbli_codes"]}
    assert set(codes) == {"10391", "11052", "96200"}
    assert codes["96200"]["kata_kunci"] == ["binatu", "laundry"]
    # scope kode yang dihapus ikut hilang, yang tersisa tetap
    assert {s["kode"] for s in kdata.load()["kbli_scopes"]} == {"11052", "96200"}
    with pytest.raises(DataError, match="kedaluwarsa"):
        kdata.apply_import(p["token"], "u1")


def test_roundtrip_keeps_messy_keywords_unchanged(kdata):
    kdata.save_code("96200", {"kata_kunci": ["Laundry  Kiloan"]}, None)
    data = kdata.load()
    data["kbli_codes"][2]["kata_kunci"] = ["Laundry  Kiloan", "BINATU"]      # data lama berantakan
    p = kdata.preview_import(kdata.export_xlsx(), "sama.xlsx", None)
    assert p["changed_count"] == 0


def test_old_versions_are_pruned(kdata):
    kdata.keep_versions = 3
    for i in range(6):
        kdata.save_code("96200", {"kata_kunci": [f"kata{i}"]}, None)
    real = [v for v in kdata.versions() if v["id"] != "awal"]
    assert len(real) <= 4 and real[0]["active"]          # 3 + versi aktif sebelumnya
    assert kdata.versions()[-1]["id"] == "awal" and not kdata.versions()[-1]["active"]


def test_stale_preview_rejected(kdata):
    p = kdata.preview_import(kdata.export_xlsx(), "sama.xlsx", None)
    assert p["changed_count"] == 0 and p["added_count"] == 0 and p["removed_count"] == 0
    kdata.save_code("96200", {"kata_kunci": ["x"]}, None)
    with pytest.raises(DataError, match="berubah sejak pratinjau"):
        kdata.apply_import(p["token"], None)


# ------------------------------------------------------------------------- API
def test_api_edit_is_searchable_immediately(tmp_path):
    from api.auth import hash_password

    make_seed(tmp_path / "seed", None)
    settings = Settings(_env_file=None, store_backend="memory", embed_model="none", llm_api_key="",
                        kbli_index_dir=str(tmp_path / "seed"), kbli_versions_dir=str(tmp_path / "versions"))
    store = MemoryStore()
    app = create_app(lambda: build_state(settings, redis=fakeredis.FakeRedis(decode_responses=True), store=store))
    c = TestClient(app)
    c.__enter__()
    u = store.create_user("a@x.id", "A", hash_password("rahasia123"), role="admin")
    assert c.post("/auth/login", json={"email": "a@x.id", "password": "rahasia123"}).status_code == 200

    assert c.get("/admin/kbli", params={"q": "penatu"}).json()["items"][0]["kode"] == "96200"
    r = c.put("/admin/kbli/96200", json={"kata_kunci": ["kilatcuci"]})
    assert r.status_code == 200 and r.json()["kata_kunci"] == ["kilatcuci"]
    top = c.get("/search", params={"q": "kilatcuci"}).json()["candidates"][0]["kode"]
    assert top == "96200"                                        # indeks langsung ter-update

    assert c.put("/admin/kbli/96200", json={"judul": "x"}).status_code == 422
    vers = c.get("/admin/kbli-versions").json()
    assert vers[0]["active"] and vers[0]["note"] == "Edit 96200"
    x = c.get("/admin/kbli-export")
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxml")

    # editor tidak boleh rollback / hapus
    store.users[u["id"]]["role"] = "editor"
    assert c.post(f"/admin/kbli-versions/{vers[0]['id']}/activate").status_code == 403
    assert c.delete("/admin/kbli/96200").status_code == 403
    store.users[u["id"]]["role"] = "user"
    assert c.get("/admin/kbli").status_code == 403
