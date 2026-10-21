"""Login/registrasi + endpoint admin.

Test admin dengan query SQL sungguhan jalan kalau TEST_DATABASE_URL di-set
(Postgres dengan schema + data KBLI sudah dimuat), selain itu di-skip.
"""
import os

import fakeredis
import pytest
from fastapi.testclient import TestClient

from api.auth import hash_password, verify_password
from api.main import build_state, create_app
from api.store import MemoryStore
from kbli.config import Settings
from tests.test_api import INDEX


def make(store=None, **overrides):
    overrides.setdefault("allow_registration", True)
    settings = Settings(_env_file=None, store_backend="memory", embed_model="none", llm_api_key="", **overrides)
    store = store or MemoryStore()
    app = create_app(lambda: build_state(settings, redis=fakeredis.FakeRedis(decode_responses=True),
                                         store=store, index=INDEX))
    c = TestClient(app)
    c.__enter__()
    return c, store


def register(c, email="aza@example.com", pw="rahasia123", name="Aza"):
    return c.post("/auth/register", json={"name": name, "email": email, "password": pw})


def test_password_hash_roundtrip():
    h = hash_password("rahasia123")
    assert h.startswith("scrypt$") and verify_password("rahasia123", h)
    assert not verify_password("salah123", h) and not verify_password("x", "rusak")


def test_register_login_logout_and_quota():
    c, _ = make()
    assert c.get("/auth/me").json()["user"] is None
    assert c.get("/auth/me").json()["quota"]["llm_limit"] == 10           # tamu

    r = register(c)
    assert r.status_code == 200 and r.json()["user"]["role"] == "user"
    me = c.get("/auth/me").json()
    assert me["user"]["email"] == "aza@example.com" and me["quota"]["llm_limit"] == 20

    assert register(c).status_code == 409                                  # email ganda
    assert c.post("/auth/logout").status_code == 204
    assert c.get("/auth/me").json()["user"] is None

    assert c.post("/auth/login", json={"email": "AZA@example.com", "password": "salah1234"}).status_code == 401
    assert c.post("/auth/login", json={"email": "AZA@example.com", "password": "rahasia123"}).status_code == 200
    assert c.get("/auth/me").json()["user"]["name"] == "Aza"


@pytest.mark.parametrize("pw", ["pendek1", "hanyahuruf", "1234567890"])
def test_weak_password_rejected(pw):
    c, _ = make()
    assert register(c, pw=pw).status_code == 422


def test_tampered_token_is_guest():
    c, _ = make()
    register(c)
    c.cookies.set("kbli_token", c.cookies.get("kbli_token") + "x")
    assert c.get("/auth/me").json()["user"] is None


def test_deactivated_user_falls_back_to_guest():
    c, store = make()
    uid = register(c).json()["user"]["id"]
    store.users[uid]["is_active"] = False
    assert c.get("/auth/me").json()["user"] is None


def test_admin_login_rejects_regular_users():
    c, store = make()
    uid = register(c).json()["user"]["id"]
    c.post("/auth/logout")
    body = {"email": "aza@example.com", "password": "rahasia123", "staff_only": True}
    r = c.post("/auth/login", json=body)
    assert r.status_code == 403 and "bukan akun admin" in r.json()["detail"]
    assert c.get("/auth/me").json()["user"] is None                       # tidak ada cookie login
    store.users[uid]["role"] = "editor"
    assert c.post("/auth/login", json=body).status_code == 200


def test_client_ip_ignores_spoofed_forwarded_for():
    import fakeredis as fr
    redis = fr.FakeRedis(decode_responses=True)
    settings = Settings(_env_file=None, store_backend="memory", embed_model="none", llm_api_key="")
    app = create_app(lambda: build_state(settings, redis=redis, store=MemoryStore(), index=INDEX))
    c = TestClient(app)
    c.__enter__()
    c.post("/chat", json={"message": "halo"}, headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    keys = [k for k in redis.scan_iter("rl:a:*")]
    assert keys and ":203.0.113.9:" in keys[0] and "6.6.6.6" not in keys[0]


def test_registration_closed_by_default():
    settings = Settings(_env_file=None)
    assert settings.allow_registration is False and settings.llm_quota_anon_per_day == 10


def test_registration_can_be_closed():
    c, _ = make(allow_registration=False)
    assert register(c).status_code == 403


def test_admin_endpoints_require_admin():
    c, store = make()
    assert c.get("/admin/stats").status_code == 401
    uid = register(c).json()["user"]["id"]
    assert c.get("/admin/stats").status_code == 403
    store.users[uid]["role"] = "admin"
    assert c.get("/admin/stats").status_code == 200
    assert c.get("/admin/searches", params={"filter": "ngawur"}).status_code == 422
    # admin tidak boleh menurunkan diri sendiri
    assert c.patch(f"/admin/users/{uid}", json={"role": "user"}).status_code == 400


def test_logged_in_chat_session_belongs_to_user():
    c, store = make()
    uid = register(c).json()["user"]["id"]
    out = c.post("/chat", json={"message": "usaha depot air minum isi ulang"}).json()
    assert store.sessions[out["session_id"]]["user_id"] == uid
    assert out["quota"]["llm_limit"] == 20


# ------------------------------------------------------------ Postgres sungguhan
from tests.pg import pg_test_url

PG = pg_test_url()


@pytest.mark.skipif(not PG, reason="TEST_DATABASE_URL tidak di-set")
def test_admin_queries_on_postgres():
    from api.store import PgStore

    store = PgStore(PG)
    with store.pool.connection() as conn:
        # hapus hanya data uji (jangan TRUNCATE ... CASCADE: ikut mengosongkan tabel lain)
        conn.execute("DELETE FROM chat_sessions")
        conn.execute("DELETE FROM users")
    try:
        c, _ = make(store=store, docs_enabled=False)
        uid = register(c, email="admin@example.com").json()["user"]["id"]
        store.admin_set_user(uid, "admin", None)

        # 1) dijawab lalu user memilih kode selain #1, kasih 👎
        out = c.post("/chat", json={"message": "usaha depot air minum isi ulang"}).json()
        sid, top1 = out["session_id"], out["options"][0]["kode"]
        other = out["options"][1]["kode"]
        c.post("/feedback", json={"message_id": out["message_id"], "rating": -1})
        c.post("/chat", json={"session_id": sid, "action": {"type": "select", "kode": other}})
        # 2) pencarian kedua, tidak memilih apa-apa
        c.post("/chat", json={"session_id": sid, "message": "pabrik pupuk urea"})

        stats = c.get("/admin/stats", params={"days": 7}).json()
        assert stats["searches"] == 2 and stats["thumbs_down"] == 1 and stats["selections"] == 1
        assert len(stats["daily"]) == 7 and stats["daily"][-1]["searches"] == 2

        allq = c.get("/admin/searches").json()
        assert allq["total"] == 2
        first = allq["items"][-1]
        assert first["selected"] == other and first["top1"] == top1 and first["rating"] == -1
        assert len(first["results"]) == 5 and first["results"][0]["judul"]

        assert c.get("/admin/searches", params={"filter": "not_top1"}).json()["total"] == 1
        assert c.get("/admin/searches", params={"filter": "negative"}).json()["total"] == 1
        assert c.get("/admin/searches", params={"filter": "no_select"}).json()["total"] == 1
        assert c.get("/admin/searches", params={"q": "pupuk"}).json()["total"] == 1

        top = c.get("/admin/top-codes").json()
        assert top[0]["kode"] == other and top[0]["n"] == 1

        users = c.get("/admin/users").json()
        assert users["total"] == 1 and users["items"][0]["role"] == "admin"
    finally:
        store.close()
