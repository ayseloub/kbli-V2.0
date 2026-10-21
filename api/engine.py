"""Otak chatbot: retrieval yang memutuskan, template yang menjawab, LLM hanya cadangan.

Alur satu pesan:
  1. sapaan / terima kasih          -> template
  2. retrieval hybrid -> confidence
       luar_topik                    -> template penolakan
       tinggi                        -> template jawaban + alternatif
       ragu                          -> tombol klarifikasi (3 kandidat)
  3. action 'select' (klik tombol)   -> template jawaban kode terpilih + catat sebagai feedback
  4. action 'explain' (klik tombol)  -> LLM menjelaskan kode itu (pakai kuota)

Pertanyaan aturan/prosedur ("syarat NIB apa saja?") dicari dulu di dokumen knowledge base:
  ada potongan relevan              -> kutipan asli + sumber (tanpa LLM)
  action 'ask_ai' (klik tombol)      -> LLM merangkum kutipan itu dengan rujukan [n] (pakai kuota)
  tidak ada yang relevan             -> lanjut ke alur KBLI biasa
"""
from __future__ import annotations

import logging
import re
import time

from kbli.config import Settings
from kbli.preprocess import normalize_kode
from kbli.retrieval import KbliIndex

from . import templates as T
from .llm import LLMClient, LLMError
from .ratelimit import Identity, Limiter
from .schemas import CandidateOut, ChatIn, ChatOut, QuotaOut, SourceOut
from .store import Store

log = logging.getLogger("kbli.engine")
_RE_WANTS_CODE = re.compile(r"\b(kbli|kode)\b", re.IGNORECASE)


class ChatEngine:
    def __init__(self, index: KbliIndex, limiter: Limiter, llm: LLMClient, store: Store, settings: Settings,
                 docs=None):
        self.index, self.limiter, self.llm, self.store, self.s = index, limiter, llm, store, settings
        self.docs = docs        # api.docs.DocRepo atau None (mode tanpa database)

    # ------------------------------------------------------------- helpers
    def _cand(self, kode: str, score: float | None = None) -> CandidateOut:
        c = self.index.get(kode)
        return CandidateOut(kode=kode, judul=c["judul"], ringkasan=T.summarize_uraian(c.get("uraian")),
                            batasan=self.index.special_scopes(kode), score=score)

    def _quota(self, ident: Identity) -> QuotaOut:
        return QuotaOut(llm_remaining=self.limiter.llm_remaining(ident), llm_limit=self.limiter.llm_limit(ident))

    def _can_explain(self, ident: Identity) -> bool:
        return self.llm.available and self.limiter.llm_remaining(ident) > 0

    def _reply(self, sid: str, kind: str, text: str, ident: Identity, **kw) -> ChatOut:
        mid = self.store.add_message(sid, "assistant", text, tool_payload={"kind": kind})
        return ChatOut(session_id=sid, message_id=mid, kind=kind, reply=text, quota=self._quota(ident), **kw)

    # ---------------------------------------------------------------- main
    def handle(self, ident: Identity, anon_id: str, body: ChatIn) -> ChatOut:
        sid = self.store.ensure_session(body.session_id, anon_id, user_id=ident.user_id)

        if body.action and body.action.type == "ask_ai":
            return self._ask_docs(sid, ident)
        if body.action:
            kode = normalize_kode(body.action.kode or "")
            if not kode or not self.index.get(kode):
                return self._reply(sid, "error", "Kode KBLI tidak dikenal.", ident)
            if body.action.type == "select":
                return self._select(sid, ident, kode)
            return self._explain(sid, ident, kode)

        text = (body.message or "").strip()
        if not text:
            return self._reply(sid, "error", "Pesan masih kosong.", ident)
        if len(text) > self.s.max_input_chars:
            return self._reply(sid, "error", f"Pesan terlalu panjang (maks {self.s.max_input_chars} karakter).", ident)

        mid = self.store.add_message(sid, "user", text)

        small = T.detect_smalltalk(text)
        if small == "greeting":
            return self._reply(sid, "greeting", T.pick(T.GREETINGS), ident)
        if small == "thanks":
            return self._reply(sid, "thanks", T.pick(T.THANKS), ident)
        doc_q = self._docs_ready() and T.is_doc_question(text) and not _RE_WANTS_CODE.search(text)
        if doc_q:
            out = self._doc_excerpts(sid, ident, mid, text)
            if out:
                return out
        if T.is_offtopic_question(text):
            return self._reply(sid, "refuse", T.pick(T.REFUSE), ident)

        t0 = time.perf_counter()
        res = self.index.search(text, top_k=self.s.chat_top_k, settings=self.s)
        latency = int((time.perf_counter() - t0) * 1000)
        self.store.log_retrieval(mid, "kbli", text, {"mode": res.mode, "confidence": res.confidence, **res.signals},
                                 [c.to_dict() | {"uraian": None} for c in res.candidates], latency)

        # Pertanyaan aturan yang tidak ketemu di dokumen: jangan tampilkan kode KBLI yang
        # kebetulan mirip ("syarat izin risiko rendah" -> Kawasan Industri). Kecuali KBLI yakin.
        if doc_q and res.confidence != "tinggi":
            return self._reply(sid, "doc_answer", T.answer_docs_none(), ident)
        if res.confidence == "luar_topik":
            return self._reply(sid, "refuse", T.pick(T.REFUSE), ident)

        cands = [self._cand(c.kode, c.score) for c in res.candidates]
        if res.confidence == "tinggi":
            # Yakin: tonjolkan #1, tapi tetap tampilkan semua top-k sebagai pilihan
            text_out = T.answer_confident(cands[0].model_dump(), n_more=len(cands) - 1)
            return self._reply(sid, "answer", text_out, ident, candidates=cands, options=cands,
                               can_explain=self._can_explain(ident))

        return self._reply(sid, "clarify", T.answer_clarify(len(cands)), ident, candidates=cands, options=cands)

    # -------------------------------------------------------------- actions
    def _select(self, sid: str, ident: Identity, kode: str) -> ChatOut:
        mid = self.store.add_message(sid, "user", None, tool_name="select", tool_payload={"kode": kode})
        self.store.add_feedback(mid, None, kode, None)   # pasangan (query, kode) untuk fine-tuning
        cand = self._cand(kode)
        return self._reply(sid, "answer", T.answer_selected(cand.model_dump()), ident,
                           candidates=[cand], can_explain=self._can_explain(ident))

    def _explain(self, sid: str, ident: Identity, kode: str) -> ChatOut:
        self.store.add_message(sid, "user", None, tool_name="explain", tool_payload={"kode": kode})
        cand = self._cand(kode)
        if not self.llm.available or not self.limiter.try_consume_llm(ident):
            return self._reply(sid, "explain", T.answer_llm_unavailable(), ident, candidates=[cand])

        c = self.index.get(kode)
        data = (f"KBLI {kode} – {c['judul']}\nUraian resmi: {c.get('uraian') or '-'}\n"
                f"Batasan ruang lingkup: {'; '.join(cand.batasan) or 'Seluruh'}")
        question = self.store.last_user_message(sid) or f"Jelaskan KBLI {kode}"
        t0 = time.perf_counter()
        try:
            text, meta = self.llm.explain(question, data, {kode})
        except LLMError as e:
            log.warning("LLM gagal, fallback ke template: %s", e)
            self.limiter.refund_llm(ident)
            return self._reply(sid, "explain", T.answer_llm_unavailable(), ident, candidates=[cand])
        latency = int((time.perf_counter() - t0) * 1000)
        mid = self.store.add_message(sid, "assistant", text, tool_name="explain", tool_payload={"kode": kode},
                               llm_provider=self.s.llm_base_url, llm_model=meta["model"],
                               tokens_in=meta["tokens_in"], tokens_out=meta["tokens_out"], latency_ms=latency)
        return ChatOut(session_id=sid, message_id=mid, kind="explain", reply=text, candidates=[cand], used_llm=True,
                       quota=self._quota(ident))

    # ------------------------------------------------------------ documents
    def _docs_ready(self) -> bool:
        if not (self.docs and self.s.docs_enabled):
            return False
        try:
            return self.docs.has_active()
        except Exception:  # noqa: BLE001  (DB bermasalah -> jangan ganggu alur KBLI)
            log.exception("Cek dokumen aktif gagal")
            return False

    def _doc_hits(self, question: str) -> list[dict]:
        hits = self.docs.search(question, self.s.doc_top_k * 2)
        return [h for h in hits if h["sim"] >= self.s.doc_min_score][: self.s.doc_top_k]

    @staticmethod
    def _sources(hits: list[dict], snippet_chars: int = 700) -> list[SourceOut]:
        out = []
        for i, h in enumerate(hits, 1):
            ps, pe = h.get("page_start"), h.get("page_end")
            pages = None if ps is None else (f"hal. {ps}" if ps == pe or pe is None else f"hal. {ps}–{pe}")
            text = h["content"]
            snippet = text if len(text) <= snippet_chars else text[:snippet_chars].rsplit(" ", 1)[0] + " …"
            out.append(SourceOut(n=i, document_id=h["document_id"], title=h["title"],
                                 where=" › ".join(h.get("heading_path") or []) or None, pages=pages,
                                 snippet=snippet))
        return out

    def _doc_excerpts(self, sid: str, ident: Identity, mid: int, text: str) -> ChatOut | None:
        t0 = time.perf_counter()
        try:
            hits = self._doc_hits(text)
        except Exception:  # noqa: BLE001
            log.exception("Pencarian dokumen gagal")
            return None
        latency = int((time.perf_counter() - t0) * 1000)
        self.store.log_retrieval(mid, "documents", text, {"top_k": self.s.doc_top_k, "min": self.s.doc_min_score},
                                 [{"chunk_id": h["chunk_id"], "document_id": h["document_id"],
                                   "sim": round(float(h["sim"]), 4), "rrf": round(float(h["rrf"]), 5)}
                                  for h in hits], latency)
        if not hits:
            return None
        return self._reply(sid, "doc_answer", T.answer_docs_excerpt(len(hits)), ident,
                           sources=self._sources(hits), can_explain=self._can_explain(ident))

    def _ask_docs(self, sid: str, ident: Identity) -> ChatOut:
        self.store.add_message(sid, "user", None, tool_name="ask_ai")
        question = self.store.last_user_message(sid)
        if not question or not self._docs_ready():
            return self._reply(sid, "error", "Silakan tuliskan pertanyaan Anda terlebih dahulu.", ident)
        hits = self._doc_hits(question)
        if not hits:
            return self._reply(sid, "doc_answer", T.answer_docs_none(), ident)
        sources = self._sources(hits)
        if not self.llm.available or not self.limiter.try_consume_llm(ident):
            return self._reply(sid, "doc_answer", T.answer_llm_unavailable(), ident, sources=sources)

        blocks = [f"{h['title']} — {' › '.join(h.get('heading_path') or [])}\n{h['content']}" for h in hits]
        t0 = time.perf_counter()
        try:
            text, meta = self.llm.answer_docs(question, blocks)
        except LLMError as e:
            log.warning("LLM dokumen gagal, fallback ke kutipan: %s", e)
            self.limiter.refund_llm(ident)
            return self._reply(sid, "doc_answer", T.answer_llm_unavailable(), ident, sources=sources)
        latency = int((time.perf_counter() - t0) * 1000)
        cited = {int(n) for n in re.findall(r"\[(\d{1,2})\]", text)}
        if cited:   # tampilkan hanya sumber yang benar-benar dirujuk (nomor tetap sama)
            sources = [s for s in sources if s.n in cited]
        mid = self.store.add_message(sid, "assistant", text, tool_name="ask_ai",
                                     tool_payload={"kind": "doc_answer", "sources": [s.document_id for s in sources]},
                                     llm_provider=self.s.llm_base_url, llm_model=meta["model"],
                                     tokens_in=meta["tokens_in"], tokens_out=meta["tokens_out"], latency_ms=latency)
        return ChatOut(session_id=sid, message_id=mid, kind="doc_answer", reply=text, sources=sources,
                       used_llm=True, quota=self._quota(ident))
