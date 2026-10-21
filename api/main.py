"""FastAPI app KBLI v2.

  uvicorn api.main:app --reload

Endpoint
  GET  /health
  GET  /search?q=...&k=5          pencarian langsung (tanpa chat)
  POST /chat                      pesan / klik tombol (select, explain)
  POST /feedback                  rating jawaban
  POST /auth/register | /auth/login | /auth/logout,  GET /auth/me
  GET  /admin/stats | /admin/searches | /admin/top-codes | /admin/users   (role admin/editor)
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from kbli.config import Settings, get_settings
from kbli.embedder import make_embedder
from kbli.retrieval import KbliIndex

from . import admin as admin_routes
from .auth import TOKEN_COOKIE, create_token, decode_token, hash_password, validate_password, verify_password
from .engine import ChatEngine
from .llm import LLMClient
from .ratelimit import Identity, Limiter
from .schemas import ChatIn, ChatOut, FeedbackIn, LoginIn, MeOut, QuotaOut, RegisterIn, SearchOut, UserOut
from .docs import DocEmbedder, DocRepo, IngestWorker
from .kbli_data import IndexWatcher, KbliData
from .store import MemoryStore, PgStore

log = logging.getLogger("kbli.api")

# uvicorn hanya mengatur logger miliknya; tampilkan juga log aplikasi (indeks diganti,
# dokumen selesai diproses, sinkron DB, dll.) di terminal yang sama.
_app_log = logging.getLogger("kbli")
if not _app_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    _app_log.addHandler(_h)
    _app_log.setLevel(logging.INFO)
    _app_log.propagate = False
ANON_COOKIE = "kbli_anon"


def build_state(settings: Settings, *, redis=None, store=None, llm_transport=None, index=None,
                docs_repo=None) -> dict:
    """Dipisah dari lifespan supaya test bisa menyuntik redis/store/LLM palsu."""
    embedder = None
    kdata = KbliData(settings.kbli_versions_dir, settings.kbli_index_dir)
    kdata.keep_versions = max(2, settings.kbli_keep_versions)
    if index is None:
        embedder = None if settings.embed_model in ("", "none") else make_embedder(settings.embed_model)
        index = KbliIndex.load(kdata.active_dir(), embedder)
    kdata.embedder = embedder if embedder is not None else index.embedder
    if redis is None:
        import redis as redis_lib
        redis = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    if store is None:
        store = PgStore(settings.database_url) if settings.store_backend == "postgres" else MemoryStore()
    limiter = Limiter(redis, settings)
    llm = LLMClient(settings, transport=llm_transport)
    docs = None
    if docs_repo is not None:
        docs = docs_repo
    elif settings.docs_enabled and isinstance(store, PgStore) and settings.doc_embed_model not in ("", "none"):
        docs = DocRepo(store.pool, settings, DocEmbedder(settings))
    state = {
        "settings": settings, "index": index, "limiter": limiter, "store": store, "docs": docs,
        "engine": ChatEngine(index, limiter, llm, store, settings, docs=docs),
        "sem": asyncio.Semaphore(settings.max_concurrent_inference),
        "kbli_data": kdata, "kbli_dir": str(kdata.active_dir()),
    }
    reload_lock = threading.Lock()

    def reload_index(d):
        """Pasang indeks dari folder versi baru tanpa restart. Request yang sedang jalan tetap
        memakai indeks lama sampai selesai."""
        with reload_lock:
            if state["kbli_dir"] == str(d):
                return
            t0 = time.perf_counter()
            new = KbliIndex.load(d, kdata.embedder)
            state["index"] = new
            state["engine"].index = new
            state["kbli_dir"] = str(d)
            log.info("Indeks KBLI diganti ke %s (%d kode, %.1f dtk)", d, len(new.kode_list),
                     time.perf_counter() - t0)
        if isinstance(store, PgStore):
            def sync():
                try:
                    from pipeline.load_db import load_dir
                    log.info("Sinkron DB: %s", load_dir(Path(d), settings.database_url))
                except Exception:  # noqa: BLE001
                    log.exception("Sinkron data KBLI ke database gagal")
            threading.Thread(target=sync, name="kbli-db-sync", daemon=True).start()

    kdata.on_change = reload_index
    state["reload_index"] = reload_index
    return state


def create_app(state_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.k = state_factory() if state_factory else build_state(get_settings())
        idx = app.state.k["index"]
        log.info("Index KBLI: %d kode, dense=%s", len(idx.kode_list), idx.dense_ready)
        secret = app.state.k["settings"].jwt_secret
        if len(secret) < 32:
            raise RuntimeError("JWT_SECRET di .env minimal 32 karakter. "
                               'Buat: python -c "import secrets; print(secrets.token_urlsafe(48))"')
        if secret.startswith(("dev-secret", "ganti-dengan")):
            log.warning("JWT_SECRET masih default. Ganti di .env sebelum dipakai orang lain!")
        worker = None
        if app.state.k.get("docs") and app.state.k["settings"].doc_worker_enabled:
            worker = IngestWorker(app.state.k["docs"])
            worker.start()
            log.info("Worker ingestion dokumen berjalan")
        watcher = IndexWatcher(app.state.k["kbli_data"], app.state.k["reload_index"])
        watcher.start()
        try:
            yield
        finally:
            watcher.stop()
            if worker:
                worker.stop()
                worker.join(timeout=10)
            close = getattr(app.state.k["store"], "close", None)
            if close:
                close()

    app = FastAPI(title="KBLI v2", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    def state(request: Request) -> dict:
        return request.app.state.k

    def client_ip(request: Request) -> str:
        s: Settings = request.app.state.k["settings"]
        fwd = request.headers.get("x-forwarded-for")
        if s.trust_proxy_headers and fwd:
            # Entri paling kiri bisa dipalsukan pengunjung; yang bisa dipercaya adalah entri yang
            # ditambahkan proxy kita sendiri, dihitung dari kanan.
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            return parts[-min(max(s.trusted_proxy_hops, 1), len(parts))]
        return request.client.host if request.client else "unknown"

    def identity(request: Request, response: Response) -> tuple[Identity, str]:
        """User login -> 'u:<id>' (kuota user). Tamu -> cookie acak + IP (cookie saja mudah dihapus)."""
        k = request.app.state.k
        s: Settings = k["settings"]
        anon = request.cookies.get(ANON_COOKIE)
        if not anon:
            anon = secrets.token_urlsafe(16)
            response.set_cookie(ANON_COOKIE, anon, max_age=365 * 86400, httponly=True, samesite="lax",
                                secure=s.cookie_secure)
        token = request.cookies.get(TOKEN_COOKIE)
        if token and (claims := decode_token(token, s)):
            user = k["store"].get_user(claims["sub"])
            if user and user["is_active"]:
                return Identity(f"u:{user['id']}", True, user_id=user["id"], role=user["role"]), anon
        return Identity(f"a:{anon}:{client_ip(request)}", False), anon

    def set_login_cookie(response: Response, user: dict, s: Settings):
        response.set_cookie(TOKEN_COOKIE, create_token(user["id"], user["role"], s),
                            max_age=s.jwt_ttl_hours * 3600, httponly=True, samesite="lax", secure=s.cookie_secure)

    def user_out(u: dict) -> UserOut:
        return UserOut(id=u["id"], email=u["email"], name=u["name"], role=u["role"])

    def me_out(ident: Identity, st: dict) -> MeOut:
        lim: Limiter = st["limiter"]
        user = st["store"].get_user(ident.user_id) if ident.user_id else None
        return MeOut(user=user_out(user) if user else None,
                     quota=QuotaOut(llm_remaining=lim.llm_remaining(ident), llm_limit=lim.llm_limit(ident)))

    def guard_auth_rate(request: Request, st: dict):
        # anti brute force: per IP, terpisah dari limit chat
        if not st["limiter"].check_rate(Identity(f"auth:{client_ip(request)}", False)):
            raise HTTPException(429, "Terlalu banyak percobaan. Tunggu sebentar.")

    @app.post("/auth/register", response_model=MeOut)
    def register(body: RegisterIn, request: Request, response: Response, st=Depends(state)):
        s: Settings = st["settings"]
        if not s.allow_registration:
            raise HTTPException(403, "Pendaftaran sedang ditutup.")
        guard_auth_rate(request, st)
        if err := validate_password(body.password):
            raise HTTPException(422, err)
        try:
            user = st["store"].create_user(body.email.strip(), body.name.strip(), hash_password(body.password))
        except ValueError:
            raise HTTPException(409, "Email sudah terdaftar. Silakan login.")
        set_login_cookie(response, user, s)
        return me_out(Identity(f"u:{user['id']}", True, user_id=user["id"], role=user["role"]), st)

    @app.post("/auth/login", response_model=MeOut)
    def login(body: LoginIn, request: Request, response: Response, st=Depends(state)):
        guard_auth_rate(request, st)
        user = st["store"].get_user_by_email(body.email.strip())
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Email atau password salah.")
        if not user["is_active"]:
            raise HTTPException(403, "Akun ini dinonaktifkan.")
        if body.staff_only and user["role"] not in ("admin", "editor"):
            raise HTTPException(403, "Akun ini bukan akun admin.")
        set_login_cookie(response, user, st["settings"])
        return me_out(Identity(f"u:{user['id']}", True, user_id=user["id"], role=user["role"]), st)

    @app.post("/auth/logout", status_code=204)
    def logout(response: Response):
        response.delete_cookie(TOKEN_COOKIE)

    @app.get("/auth/me", response_model=MeOut)
    def me(st=Depends(state), who=Depends(identity)):
        return me_out(who[0], st)

    @app.get("/health")
    def health(k=Depends(state)):
        idx = k["index"]
        return {"status": "ok", "kode": len(idx.kode_list), "dense": idx.dense_ready}

    @app.get("/search", response_model=SearchOut)
    async def search(q: str = Query(min_length=2, max_length=1000), k: int = Query(5, ge=1, le=20),
                     st=Depends(state), who=Depends(identity)):
        ident, _ = who
        if not st["limiter"].check_rate(ident):
            raise HTTPException(429, "Terlalu banyak permintaan, coba lagi sebentar.")
        async with st["sem"]:
            res = await run_in_threadpool(st["index"].search, q, k, "hybrid", st["settings"])
        eng: ChatEngine = st["engine"]
        return SearchOut(query=q, mode=res.mode, confidence=res.confidence,
                         candidates=[eng._cand(c.kode, c.score) for c in res.candidates])

    @app.post("/chat", response_model=ChatOut)
    async def chat(body: ChatIn, st=Depends(state), who=Depends(identity)):
        ident, anon = who
        if not st["limiter"].check_rate(ident):
            raise HTTPException(429, "Terlalu banyak pesan, coba lagi sebentar.")
        async with st["sem"]:
            return await run_in_threadpool(st["engine"].handle, ident, anon, body)

    @app.post("/feedback", status_code=204)
    def feedback(body: FeedbackIn, st=Depends(state)):
        st["store"].add_feedback(body.message_id, body.rating, body.selected_kode, body.comment)

    app.include_router(admin_routes.build_router(state, identity))
    return app


app = create_app()
