"""Endpoint admin: statistik, log pencarian, kode terpopuler, kelola user.

Semua endpoint di sini wajib login dengan role 'admin' (atau 'editor' untuk yang read-only).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool

from kbli.documents import SUPPORTED_EXT

from .docs import safe_filename
from .ratelimit import Identity
from .kbli_data import DataError
from .schemas import DocumentUpdateIn, KbliCodeIn, UserUpdateIn
from .store import SEARCH_FILTERS


def build_router(state_dep, identity_dep) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    def require(*roles: str):
        def dep(who=Depends(identity_dep)) -> Identity:
            ident, _ = who
            if not ident.logged_in:
                raise HTTPException(401, "Silakan login dulu.")
            if ident.role not in roles:
                raise HTTPException(403, "Akun ini tidak punya akses admin.")
            return ident
        return dep

    staff = require("admin", "editor")
    admin_only = require("admin")

    @router.get("/stats")
    def stats(days: int = Query(7, ge=1, le=365), st=Depends(state_dep), _=Depends(staff)):
        return st["store"].admin_stats(days)

    @router.get("/searches")
    def searches(filter: str = Query("all"), q: str | None = Query(None, max_length=200),
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 st=Depends(state_dep), _=Depends(staff)):
        if filter not in SEARCH_FILTERS:
            raise HTTPException(422, f"filter harus salah satu dari {SEARCH_FILTERS}")
        return st["store"].admin_searches(filter, limit, offset, q or None)

    @router.get("/top-codes")
    def top_codes(days: int = Query(30, ge=1, le=365), limit: int = Query(20, ge=1, le=100),
                  st=Depends(state_dep), _=Depends(staff)):
        return st["store"].admin_top_codes(days, limit)

    @router.get("/users")
    def users(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              st=Depends(state_dep), _=Depends(admin_only)):
        return st["store"].admin_users(limit, offset)

    @router.patch("/users/{user_id}")
    def update_user(user_id: str, body: UserUpdateIn, st=Depends(state_dep), me=Depends(admin_only)):
        if user_id == me.user_id and (body.role not in (None, "admin") or body.is_active is False):
            raise HTTPException(400, "Tidak bisa menurunkan atau menonaktifkan akun sendiri.")
        u = st["store"].admin_set_user(user_id, body.role, body.is_active)
        if not u:
            raise HTTPException(404, "User tidak ditemukan.")
        return u

    # ------------------------------------------------------------ dokumen
    def repo(st):
        r = st.get("docs")
        if r is None:
            raise HTTPException(503, "Fitur dokumen butuh database Postgres (STORE_BACKEND=postgres).")
        return r

    def doc_or_404(r, doc_id: str) -> dict:
        try:
            uuid.UUID(doc_id)
        except ValueError:
            raise HTTPException(404, "Dokumen tidak ditemukan.")
        d = r.get(doc_id)
        if not d:
            raise HTTPException(404, "Dokumen tidak ditemukan.")
        return d

    @router.get("/documents")
    def list_documents(st=Depends(state_dep), _=Depends(staff)):
        return repo(st).list()

    @router.post("/documents", status_code=201)
    async def upload_document(file: UploadFile = File(...), title: str = Form(..., min_length=3, max_length=200),
                              description: str | None = Form(None, max_length=1000),
                              effective_from: date | None = Form(None),
                              st=Depends(state_dep), me=Depends(staff)):
        r = repo(st)
        s = st["settings"]
        name = file.filename or "dokumen"
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXT:
            raise HTTPException(415, "Format belum didukung. Pakai PDF, DOCX, TXT, atau MD.")
        updir = Path(s.upload_dir)
        updir.mkdir(parents=True, exist_ok=True)
        limit = s.max_upload_mb * 1024 * 1024
        h, size = hashlib.sha256(), 0
        fd, tmp = tempfile.mkstemp(dir=updir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"File terlalu besar (maks {s.max_upload_mb} MB).")
                    h.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "File kosong.")
            sha = h.hexdigest()
            if dup := r.find_by_sha(sha):
                raise HTTPException(409, f"File yang sama sudah pernah di-upload: \"{dup['title']}\".")
            final = updir / f"{sha[:12]}_{safe_filename(name)}"
            os.replace(tmp, final)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        doc_id = r.create(title=title.strip(), description=(description or "").strip() or None, filename=name,
                          mime=SUPPORTED_EXT[ext], storage_path=str(final), sha=sha, uploaded_by=me.user_id,
                          effective_from=effective_from)
        return r.get(doc_id)

    @router.get("/documents/{doc_id}")
    def get_document(doc_id: str, limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0),
                     st=Depends(state_dep), _=Depends(staff)):
        r = repo(st)
        d = doc_or_404(r, doc_id)
        return {**d, "chunks": r.chunks(doc_id, limit, offset)}

    @router.patch("/documents/{doc_id}")
    def update_document(doc_id: str, body: DocumentUpdateIn, st=Depends(state_dep), _=Depends(staff)):
        r = repo(st)
        d = doc_or_404(r, doc_id)
        if body.is_active and d["status"] != "ready":
            raise HTTPException(409, "Dokumen belum selesai diproses, belum bisa diaktifkan.")
        return r.update(doc_id, body.model_dump(exclude_none=True))

    @router.post("/documents/{doc_id}/reprocess")
    def reprocess_document(doc_id: str, st=Depends(state_dep), _=Depends(staff)):
        r = repo(st)
        doc_or_404(r, doc_id)
        r.reprocess(doc_id)
        return r.get(doc_id)

    @router.delete("/documents/{doc_id}", status_code=204)
    def delete_document(doc_id: str, st=Depends(state_dep), _=Depends(admin_only)):
        r = repo(st)
        doc_or_404(r, doc_id)
        path = r.delete(doc_id)
        if path:
            Path(path).unlink(missing_ok=True)

    # --------------------------------------------------------- data KBLI
    def kd(st):
        return st["kbli_data"]

    def actor(st, me) -> str:
        u = st["store"].get_user(me.user_id) if me.user_id else None
        return u["name"] if u else "?"

    def data_call(fn, *args):
        try:
            return fn(*args)
        except DataError as e:
            raise HTTPException(422, str(e))

    @router.get("/kbli")
    def kbli_list(q: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0), st=Depends(state_dep), _=Depends(staff)):
        return kd(st).list_codes(q, limit, offset)

    @router.get("/kbli/{kode}")
    def kbli_get(kode: str, st=Depends(state_dep), _=Depends(staff)):
        c = kd(st).get(kode)
        if not c:
            raise HTTPException(404, "Kode tidak ditemukan.")
        return c

    @router.put("/kbli/{kode}")
    async def kbli_save(kode: str, body: KbliCodeIn, st=Depends(state_dep), me=Depends(staff)):
        # embedding kode yang berubah dihitung di thread supaya event loop tidak macet
        return await run_in_threadpool(data_call, kd(st).save_code, kode,
                                       body.model_dump(exclude_none=True), actor(st, me))

    @router.delete("/kbli/{kode}", status_code=204)
    async def kbli_delete(kode: str, st=Depends(state_dep), me=Depends(admin_only)):
        await run_in_threadpool(data_call, kd(st).delete_code, kode, actor(st, me))

    @router.get("/kbli-export")
    def kbli_export(st=Depends(state_dep), _=Depends(staff)):
        stamp = date.today().strftime("%Y%m%d")
        return Response(kd(st).export_xlsx(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="data_kbli_{stamp}.xlsx"'})

    @router.post("/kbli-import")
    async def kbli_import_preview(file: UploadFile = File(...), st=Depends(state_dep), me=Depends(staff)):
        if not (file.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(415, "Upload file Excel (.xlsx).")
        content = await file.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, "File Excel terlalu besar (maks 20 MB).")
        return await run_in_threadpool(data_call, kd(st).preview_import, content, file.filename, actor(st, me))

    @router.post("/kbli-import/{token}/apply", status_code=202)
    def kbli_import_apply(token: str, st=Depends(state_dep), me=Depends(staff)):
        data_call(kd(st).apply_import, token, actor(st, me))
        return kd(st).job_status()

    @router.get("/kbli-job")
    def kbli_job(st=Depends(state_dep), _=Depends(staff)):
        return kd(st).job_status()

    @router.get("/kbli-versions")
    def kbli_versions(st=Depends(state_dep), _=Depends(staff)):
        return kd(st).versions()

    @router.post("/kbli-versions/{vid}/activate")
    async def kbli_rollback(vid: str, st=Depends(state_dep), me=Depends(admin_only)):
        await run_in_threadpool(data_call, kd(st).rollback, vid, actor(st, me))
        return kd(st).versions()

    return router
