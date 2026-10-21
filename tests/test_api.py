"""Test API end-to-end dengan Redis palsu, store memori, dan LLM tiruan."""
import json

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import build_state, create_app
from api.store import MemoryStore
from kbli.config import Settings
from kbli.retrieval import KbliIndex

INDEX = KbliIndex.load("data/processed", embedder=None)   # BM25 saja, cepat


def make_client(llm_reply="Usaha kamu masuk KBLI 11052 karena ...", llm_status=200, **overrides):
    calls = []

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        if llm_status != 200:
            return httpx.Response(llm_status, json={"error": "x"})
        return httpx.Response(200, json={
            "model": "mock", "usage": {"prompt_tokens": 400, "completion_tokens": 80},
            "choices": [{"message": {"content": llm_reply}}]})

    settings = Settings(_env_file=None, store_backend="memory", embed_model="none",
                        llm_api_key="test", **overrides)
    store, redis = MemoryStore(), fakeredis.FakeRedis(decode_responses=True)
    app = create_app(lambda: build_state(settings, redis=redis, store=store, index=INDEX,
                                         llm_transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    client.__enter__()
    return client, store, calls


def chat(client, **body):
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_health():
    c, *_ = make_client()
    assert c.get("/health").json()["kode"] == len(INDEX.kode_list)


@pytest.mark.parametrize("msg,kind", [("halo", "greeting"), ("Selamat pagi!", "greeting"),
                                      ("makasih ya", "thanks")])
def test_smalltalk_without_retrieval(msg, kind):
    c, store, calls = make_client()
    out = chat(c, message=msg)
    assert out["kind"] == kind and not out["used_llm"]
    assert store.retrievals == [] and calls == []


def test_out_of_scope_is_refused():
    c, _, calls = make_client()
    out = chat(c, message="gimana cara debug error python di vscode")
    assert out["kind"] == "refuse"
    assert calls == []


def test_confident_answer_uses_template_not_llm():
    c, store, calls = make_client()
    out = chat(c, message="usaha depot air minum isi ulang")
    assert out["kind"] in ("answer", "clarify")
    assert "11052" in [x["kode"] for x in out["candidates"][:3]]
    assert calls == [] and not out["used_llm"]
    assert len(store.retrievals) == 1


def test_select_records_feedback_pair():
    c, store, _ = make_client()
    first = chat(c, message="usaha depot air minum isi ulang")
    out = chat(c, session_id=first["session_id"], action={"type": "select", "kode": "11052"})
    assert out["kind"] == "answer" and "11052" in out["reply"]
    assert store.feedback[-1]["selected_kode"] == "11052"


def test_explain_calls_llm_and_consumes_quota():
    c, _, calls = make_client()
    first = chat(c, message="usaha depot air minum isi ulang")
    before = first["quota"]["llm_remaining"]
    out = chat(c, session_id=first["session_id"], action={"type": "explain", "kode": "11052"})
    assert out["used_llm"] and out["quota"]["llm_remaining"] == before - 1
    sent = calls[0]["messages"][1]["content"]
    assert "11052" in sent and "depot air minum" in sent.lower()


def test_llm_inventing_codes_is_rejected_and_refunded():
    c, _, _ = make_client(llm_reply="Pakai KBLI 99999 saja.")
    first = chat(c, message="usaha depot air minum isi ulang")
    before = first["quota"]["llm_remaining"]
    out = chat(c, session_id=first["session_id"], action={"type": "explain", "kode": "11052"})
    assert not out["used_llm"] and "99999" not in out["reply"]
    assert out["quota"]["llm_remaining"] == before


def test_llm_provider_error_falls_back():
    c, _, _ = make_client(llm_status=503)
    out = chat(c, message="depot air isi ulang")
    out = chat(c, session_id=out["session_id"], action={"type": "explain", "kode": "11052"})
    assert out["kind"] == "explain" and not out["used_llm"]


def test_llm_quota_exhausted_falls_back():
    c, _, calls = make_client(llm_quota_anon_per_day=1)
    sid = chat(c, message="depot air isi ulang")["session_id"]
    assert chat(c, session_id=sid, action={"type": "explain", "kode": "11052"})["used_llm"]
    out = chat(c, session_id=sid, action={"type": "explain", "kode": "11052"})
    assert not out["used_llm"] and len(calls) == 1
    assert out["quota"]["llm_remaining"] == 0


def test_global_llm_budget():
    c, _, calls = make_client(llm_global_per_day=1, llm_quota_anon_per_day=10)
    sid = chat(c, message="depot air isi ulang")["session_id"]
    chat(c, session_id=sid, action={"type": "explain", "kode": "11052"})
    out = chat(c, session_id=sid, action={"type": "explain", "kode": "11052"})
    assert not out["used_llm"] and len(calls) == 1


def test_rate_limit():
    c, *_ = make_client(rate_limit_per_minute=3)
    codes = [c.post("/chat", json={"message": "halo"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_input_too_long_and_unknown_code():
    c, *_ = make_client(max_input_chars=20)
    assert chat(c, message="x" * 50)["kind"] == "error"
    assert chat(c, action={"type": "select", "kode": "00000"})["kind"] == "error"


def test_search_endpoint():
    c, *_ = make_client()
    r = c.get("/search", params={"q": "pabrik pupuk urea"}).json()
    assert r["mode"] == "bm25" and len(r["candidates"]) == 5
    assert "20122" in [x["kode"] for x in r["candidates"]]


def test_empty_llm_content_falls_back_and_refunds():
    c, _, calls = make_client(llm_reply="")
    sid = chat(c, message="depot air isi ulang")["session_id"]
    before = chat(c, message="halo", session_id=sid)["quota"]["llm_remaining"]
    out = chat(c, session_id=sid, action={"type": "explain", "kode": "11052"})
    assert not out["used_llm"] and out["quota"]["llm_remaining"] == before
    assert calls[0].get("reasoning_effort") == "low"


def test_always_offers_top_k_options():
    c, *_ = make_client()
    out = chat(c, message="usaha depot air minum isi ulang")
    assert out["kind"] in ("answer", "clarify")
    assert len(out["options"]) == 5 and out["options"] == out["candidates"]
    c2, *_ = make_client(chat_top_k=3)
    assert len(chat(c2, message="usaha depot air minum isi ulang")["options"]) == 3


def test_reply_has_message_id_for_feedback():
    c, store, _ = make_client()
    out = chat(c, message="usaha depot air minum isi ulang")
    assert out["message_id"]
    assert c.post("/feedback", json={"message_id": out["message_id"], "rating": 1}).status_code == 204
    assert store.feedback[-1]["rating"] == 1
