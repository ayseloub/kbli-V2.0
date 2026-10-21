from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateOut(BaseModel):
    kode: str
    judul: str
    ringkasan: str | None = None
    batasan: list[str] = []          # ruang lingkup lampiran selain "Seluruh"
    score: float | None = None


class SearchOut(BaseModel):
    query: str
    mode: str
    confidence: Literal["tinggi", "ragu", "luar_topik"]
    candidates: list[CandidateOut]


class ChatAction(BaseModel):
    type: Literal["select", "explain", "ask_ai"]   # ask_ai: rangkum jawaban dokumen pakai LLM
    kode: str | None = None


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str | None = Field(default=None, description="Teks user; boleh kosong kalau ada action")
    action: ChatAction | None = None


class QuotaOut(BaseModel):
    llm_remaining: int
    llm_limit: int


class SourceOut(BaseModel):
    n: int                               # nomor rujukan [n] di jawaban
    document_id: str
    title: str
    where: str | None = None             # mis. "BAB II › Pasal 12"
    pages: str | None = None             # mis. "hal. 4–5"
    snippet: str


class ChatOut(BaseModel):
    session_id: str
    message_id: int | None = None        # id pesan asisten, dipakai /feedback
    kind: Literal["greeting", "thanks", "answer", "clarify", "refuse", "explain", "doc_answer", "error"]
    reply: str
    candidates: list[CandidateOut] = []
    options: list[CandidateOut] = []     # top-k kode yang bisa dipilih user (tombol)
    sources: list[SourceOut] = []        # kutipan dokumen (kind doc_answer)
    can_explain: bool = False            # tampilkan tombol AI ("jelaskan" / "rangkum pakai AI")
    used_llm: bool = False
    quota: QuotaOut | None = None


class FeedbackIn(BaseModel):
    message_id: int
    rating: Literal[-1, 1] | None = None
    selected_kode: str | None = None
    comment: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    staff_only: bool = False             # halaman login admin: tolak akun biasa


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: Literal["admin", "editor", "user"]


class MeOut(BaseModel):
    user: UserOut | None
    quota: QuotaOut


# ---------------------------------------------------------------- admin
class UserUpdateIn(BaseModel):
    role: Literal["admin", "editor", "user"] | None = None
    is_active: bool | None = None


class DocumentUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None


class KbliCodeIn(BaseModel):
    judul: str | None = Field(default=None, max_length=300)
    uraian: str | None = Field(default=None, max_length=20000)
    kata_kunci: list[str] | None = Field(default=None, max_length=100)
