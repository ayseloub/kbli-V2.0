"""Alur dokumen lewat HTTP: upload -> worker -> aktifkan -> ditanya di chat -> rangkum AI.

Butuh Postgres (TEST_DATABASE_URL) dengan schema + data KBLI. Embedding dokumen pakai
hashing-1024 supaya tidak perlu mengunduh bge-m3.
"""
import json
import os
from pathlib import Path

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import build_state, create_app
from kbli.config import Settings
from tests.make_fixtures import make_pdf
from tests.test_api import INDEX

from tests.pg import pg_test_url

PG = pg_test_url()
pytestmark = pytest.mark.skipif(not PG, reason="TEST_DATABASE_URL (nama DB mengandung 'test') tidak di-set")
FIX = Path(__file__).parent / "fixtures" / "pp_tiruan.pdf"


@pytest.fixture()
def env(tmp_path):
    from api.store import PgStore

    if not FIX.exists():
        make_pdf(FIX)
    store = PgStore(PG)
    with store.pool.connection() as c:
        c.execute("DELETE FROM documents")
        c.execute("DELETE FROM chat_sessions")
        c.execute("DELETE FROM users")
    llm_replies = []

    def handler(request: httpx.Request):
        reply = llm_replies.pop(0) if llm_replies else "Untuk risiko rendah cukup NIB [1]."
        return httpx.Response(200, json={"model": "mock", "usage": {"prompt_tokens": 900, "completion_tokens": 60},
                                         "choices": [{"message": {"content": reply}}]})

    settings = Settings(_env_file=None, store_backend="postgres", embed_model="none", llm_api_key="test",
                        doc_embed_model="hashing-1024", doc_min_score=0.2, doc_worker_enabled=False,
                        upload_dir=str(tmp_path / "uploads"), allow_registration=True)
    app = create_app(lambda: build_state(settings, redis=fakeredis.FakeRedis(decode_responses=True),
                                         store=store, index=INDEX, llm_transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    client.__enter__()
    r = client.post("/auth/register", json={"name": "Admin", "email": "admin@contoh.id", "password": "rahasia123"})
    store.admin_set_user(r.json()["user"]["id"], "admin", None)
    yield client, app, llm_replies, tmp_path
    client.__exit__(None, None, None)


def upload(client, path=FIX, title="PP 5/2021 tiruan"):
    with open(path, "rb") as f:
        return client.post("/admin/documents", data={"title": title},
                           files={"file": (path.name, f, "application/pdf")})


def run_worker(app):
    from api.docs import IngestWorker

    assert IngestWorker(app.state.k["docs"]).run_once()


def test_full_document_flow(env):
    client, app, llm_replies, tmp_path = env

    r = upload(client)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "queued" and not doc["is_active"]
    assert upload(client).status_code == 409                               # file sama
    # belum siap -> belum boleh diaktifkan
    assert client.patch(f"/admin/documents/{doc['id']}", json={"is_active": True}).status_code == 409

    run_worker(app)
    detail = client.get(f"/admin/documents/{doc['id']}").json()
    assert detail["status"] == "ready" and detail["chunk_count"] >= 1 and detail["page_count"] == 3
    assert any("Pasal 12" in c["content"] and "Pasal 7" in " ".join(c["heading_path"]) for c in detail["chunks"])

    # dokumen belum aktif -> pertanyaan aturan tidak dijawab dari dokumen
    out = client.post("/chat", json={"message": "syarat perizinan usaha risiko rendah apa?"}).json()
    assert out["kind"] != "doc_answer"

    assert client.patch(f"/admin/documents/{doc['id']}", json={"is_active": True}).json()["is_active"]
    app.state.k["docs"]._active_cache = (0.0, False)

    out = client.post("/chat", json={"message": "syarat perizinan usaha risiko rendah apa?"}).json()
    assert out["kind"] == "doc_answer" and not out["used_llm"]
    assert out["sources"] and out["sources"][0]["title"] == "PP 5/2021 tiruan"
    assert out["sources"][0]["pages"].startswith("hal.")
    assert out["can_explain"]
    sid, before = out["session_id"], out["quota"]["llm_remaining"]

    # rangkum pakai AI -> kuota terpakai, rujukan [1] valid
    ai = client.post("/chat", json={"session_id": sid, "action": {"type": "ask_ai"}}).json()
    assert ai["used_llm"] and "[1]" in ai["reply"] and ai["quota"]["llm_remaining"] == before - 1
    assert [s["n"] for s in ai["sources"]] == [1]                         # hanya sumber yang dirujuk

    # LLM merujuk sumber yang tidak ada -> ditolak, kuota dikembalikan
    llm_replies.append("Menurut sumber [9] biayanya Rp1 juta.")
    bad = client.post("/chat", json={"session_id": sid, "action": {"type": "ask_ai"}}).json()
    assert not bad["used_llm"] and bad["sources"] and bad["quota"]["llm_remaining"] == before - 1

    # pertanyaan aturan yang tidak dibahas dokumen -> "belum membahas", bukan kode KBLI ngawur
    app.state.k["settings"].doc_min_score = 0.99
    nd = client.post("/chat", json={"message": "berapa denda telat lapor pajak tahunan?"}).json()
    assert nd["kind"] == "doc_answer" and not nd["options"] and "belum membahas" in nd["reply"]
    app.state.k["settings"].doc_min_score = 0.2

    # minta kode KBLI tetap ke alur KBLI walau ada kata "syarat"
    k = client.post("/chat", json={"message": "kode kbli untuk usaha depot air minum isi ulang"}).json()
    assert k["kind"] in ("answer", "clarify")

    # log retrieval dokumen tercatat
    with app.state.k["store"].pool.connection() as c:
        n = c.execute("SELECT count(*) FROM retrieval_logs WHERE source = 'documents'").fetchone()[0]
    assert n >= 1

    # hapus -> file ikut terhapus
    stored = Path(app.state.k["docs"]._one("SELECT storage_path FROM documents WHERE id = %s",
                                           (doc["id"],))["storage_path"])
    assert stored.exists()
    assert client.delete(f"/admin/documents/{doc['id']}").status_code == 204
    assert not stored.exists()
    assert client.get(f"/admin/documents/{doc['id']}").status_code == 404


def test_failed_document_reports_reason(env, tmp_path):
    client, app, *_ = env
    from reportlab.pdfgen import canvas

    scan = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(scan))
    c.rect(50, 50, 400, 600, fill=1)
    c.showPage()
    c.save()
    doc = upload(client, scan, "Hasil scan").json()
    run_worker(app)
    d = client.get(f"/admin/documents/{doc['id']}").json()
    assert d["status"] == "failed" and "scan" in d["status_message"]
    assert client.patch(f"/admin/documents/{doc['id']}", json={"is_active": True}).status_code == 409


def test_upload_validation_and_permissions(env, tmp_path):
    client, *_ = env
    bad = tmp_path / "data.xlsx"
    bad.write_bytes(b"PK..")
    with open(bad, "rb") as f:
        r = client.post("/admin/documents", data={"title": "Excel"}, files={"file": ("data.xlsx", f)})
    assert r.status_code == 415

    client.post("/auth/logout")
    client.post("/auth/register", json={"name": "Biasa", "email": "biasa@contoh.id", "password": "rahasia123"})
    assert upload(client).status_code == 403
    assert client.get("/admin/documents").status_code == 403
