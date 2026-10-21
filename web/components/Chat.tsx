"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, HOME_URL, type Candidate, type ChatIn, type ChatOut, type Quota, type Source } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import CodeOptions from "./CodeOptions";
import RichText from "./RichText";
import {
  Alert, ArrowLeft, Bot, Check, Copy, Doc, PaperPlane, Plus, Search, Sparkle, ThumbDown, ThumbUp,
} from "./Icons";
import ThemeToggle from "./ThemeToggle";

const MAX_CHARS = 1000;
const SESSION_KEY = "kbli_session";



type Msg = {
  id: string;
  role: "user" | "bot";
  text: string;
  at: Date;
  data?: ChatOut;
  picked?: string;          // kode yang dipilih user dari opsi pesan ini
  explained?: boolean;      // tombol AI sudah dipakai
  rated?: 1 | -1;
  error?: { retry: ChatIn };
};

const uid = () => Math.random().toString(36).slice(2, 10);
const clock = (d: Date) => new Intl.DateTimeFormat("id-ID", { hour: "2-digit", minute: "2-digit" }).format(d);

function readSession(): string | null {
  try { return sessionStorage.getItem(SESSION_KEY); } catch { return null; }
}
function writeSession(id: string | null) {
  try { id ? sessionStorage.setItem(SESSION_KEY, id) : sessionStorage.removeItem(SESSION_KEY); } catch { /* abaikan */ }
}

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const { me } = useMe();
  useEffect(() => { if (me) setQuota(me.quota); }, [me]);
  const sessionRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { sessionRef.current = readSession(); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy]);

  const patch = (id: string, p: Partial<Msg>) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...p } : m)));

  const send = useCallback(async (body: Omit<ChatIn, "session_id">, userText: string | null) => {
    if (userText) setMessages((ms) => [...ms, { id: uid(), role: "user", text: userText, at: new Date() }]);
    setBusy(true);
    try {
      const out = await api.chat({ ...body, session_id: sessionRef.current });
      sessionRef.current = out.session_id;
      writeSession(out.session_id);
      if (out.quota) setQuota(out.quota);
      setMessages((ms) => [...ms, { id: uid(), role: "bot", text: out.reply, data: out, at: new Date() }]);
    } catch (e) {
      const text = e instanceof ApiError ? e.message : "Terjadi kesalahan tak terduga.";
      setMessages((ms) => [...ms, { id: uid(), role: "bot", text, error: { retry: body }, at: new Date() }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }, []);

  const submit = (text = input) => {
    const t = text.trim();
    if (!t || busy) return;
    setInput("");
    send({ message: t.slice(0, MAX_CHARS) }, t);
  };

  const pick = (msg: Msg, c: Candidate) => {
    if (busy) return;
    patch(msg.id, { picked: c.kode });
    send({ action: { type: "select", kode: c.kode } }, `Pilih KBLI ${c.kode} – ${c.judul}`);
  };

  const explain = (msg: Msg, c: Candidate) => {
    if (busy) return;
    patch(msg.id, { explained: true });
    send({ action: { type: "explain", kode: c.kode } }, `Jelaskan lebih detail KBLI ${c.kode}`);
  };

  const askAi = (msg: Msg) => {
    if (busy) return;
    patch(msg.id, { explained: true });
    send({ action: { type: "ask_ai" } }, "Rangkum jawabannya dengan AI");
  };

  const retry = (msg: Msg) => {
    if (!msg.error || busy) return;
    setMessages((ms) => ms.filter((m) => m.id !== msg.id));
    send(msg.error.retry, null);
  };

  const rate = (msg: Msg, r: 1 | -1) => {
    const mid = msg.data?.message_id;
    if (!mid || msg.rated) return;
    patch(msg.id, { rated: r });
    api.feedback(mid, r).catch(() => patch(msg.id, { rated: undefined }));
  };

  const reset = () => {
    if (busy) return;
    sessionRef.current = null;
    writeSession(null);
    setMessages([]);
    setInput("");
    inputRef.current?.focus();
  };

  const empty = messages.length === 0;
  return (
    <div className="flex h-dvh flex-col bg-paper">
      {/* ---------- bilah atas ---------- */}
      <header className="z-10 border-b border-header-line bg-header text-white">
        <div className="flex h-14 items-center gap-2 px-2 sm:px-4">
          <a href={HOME_URL} aria-label="Kembali ke beranda" className="rounded-lg p-2 hover:bg-white/10">
            <ArrowLeft className="h-5 w-5" />
          </a>
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white text-[#303b91]">
            <Bot className="h-5 w-5" />
          </div>
          <div className="ml-1 min-w-0 flex-1">
            <h1 className="truncate text-[15px] font-semibold leading-tight">Asisten Virtual BKPM</h1>
            <p className="truncate text-xs text-white/80">
              <span className="font-medium text-[#7ee08a]">● Online</span> – Siap Membantu Anda
            </p>
          </div>
          {quota && (
            <span
              className="mr-1 hidden items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 text-xs sm:inline-flex"
              title="Jatah rangkuman/penjelasan AI per hari. Pencarian kode & kutipan dokumen tidak dibatasi."
            >
              <Sparkle className="h-3.5 w-3.5 text-[#9fe0a6]" />
              Jatah AI {quota.llm_remaining}/{quota.llm_limit}
            </span>
          )}
          <ThemeToggle />
          {!empty && (
            <button type="button" onClick={reset} title="Percakapan baru"
              className="inline-flex items-center gap-1.5 rounded-lg p-2 text-sm font-medium hover:bg-white/10 sm:px-3">
              <Plus className="h-5 w-5 sm:h-4 sm:w-4" /><span className="hidden sm:inline">Percakapan baru</span>
            </button>
          )}
        </div>
      </header>

      {/* ---------- percakapan ---------- */}
      <main className="min-h-0 flex-1 overflow-y-auto">
        {empty ? (
          <Welcome onPick={(t) => submit(t)} />
        ) : (
          <div className="mx-auto max-w-3xl space-y-7 px-4 pb-10 pt-8">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <UserRow key={m.id} text={m.text} at={m.at} />
              ) : (
                <BotMessage
                  key={m.id}
                  msg={m}
                  isLatest={i === messages.length - 1}
                  busy={busy}
                  onPick={(c) => pick(m, c)}
                  onExplain={(c) => explain(m, c)}
                  onAskAi={() => askAi(m)}
                  onRetry={() => retry(m)}
                  onRate={(r) => rate(m, r)}
                />
              ),
            )}
            {busy && (
              <BotRow at={null}>
                <div className="flex gap-1.5 py-2 text-muted" role="status" aria-label="Sedang memproses">
                  <span className="dot h-2 w-2 rounded-full bg-current" />
                  <span className="dot h-2 w-2 rounded-full bg-current" />
                  <span className="dot h-2 w-2 rounded-full bg-current" />
                </div>
              </BotRow>
            )}
            <div ref={endRef} />
          </div>
        )}
      </main>

      {/* ---------- kolom ketik ---------- */}
      <footer className="px-3 pb-3 sm:px-4 sm:pb-4">
        <form className="mx-auto max-w-3xl" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="flex items-end gap-2 rounded-3xl border border-line bg-surface p-2 pl-4 shadow-soft focus-within:border-brand-fg/50">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value.slice(0, MAX_CHARS))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
              }}
              rows={1}
              placeholder="Ceritakan usaha Anda atau tanyakan soal perizinan…"
              aria-label="Pesan"
              className="max-h-48 min-h-11 flex-1 resize-none bg-transparent py-2.5 text-[15px] outline-none placeholder:text-muted/80 [field-sizing:content]"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              aria-label="Kirim"
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-go text-go-ink transition hover:brightness-110 disabled:opacity-40"
            >
              <PaperPlane className="h-5 w-5" />
            </button>
          </div>
          <p className="mt-2 px-2 text-center text-[11px] text-muted">
            Hasil diambil dari uraian resmi KBLI 2020 dan dokumen resmi. Mohon verifikasi kembali di OSS sebelum mendaftar.
            {input.length > MAX_CHARS * 0.8 && <span className="ml-2">{input.length}/{MAX_CHARS}</span>}
          </p>
        </form>
      </footer>
    </div>
  );
}

const SUGGESTIONS = [
  { icon: "search", title: "Cari kode KBLI", text: "usaha laundry kiloan" },
  { icon: "search", title: "Usaha online", text: "jualan kue kering lewat Instagram" },
  { icon: "search", title: "Usaha jasa", text: "bengkel servis motor" },
  { icon: "doc", title: "Aturan perizinan", text: "syarat izin usaha risiko rendah" },
] as const;

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  return (
    <div className="animate-rise mx-auto flex min-h-full max-w-3xl flex-col justify-center px-4 py-10">
      <div className="mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-brand text-brand-ink shadow-soft">
        <Bot className="h-8 w-8" />
      </div>
      <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Selamat datang di Asisten Virtual BKPM</h2>
      <p className="mt-2 max-w-xl text-muted">
        Ceritakan kegiatan usaha Anda dengan bahasa sehari-hari. Saya carikan kode KBLI yang paling sesuai,
        atau jawab pertanyaan seputar perizinan berusaha.
      </p>
      <div className="mt-7 grid gap-2.5 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.text}
            type="button"
            onClick={() => onPick(s.text)}
            className="group flex items-start gap-3 rounded-2xl border border-line bg-surface p-4 text-left transition hover:border-brand-fg/40 hover:shadow-soft"
          >
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-fg">
              {s.icon === "doc" ? <Doc className="h-[18px] w-[18px]" /> : <Search className="h-[18px] w-[18px]" />}
            </span>
            <span className="min-w-0">
              <span className="block text-xs font-medium text-muted">{s.title}</span>
              <span className="block text-[15px] font-medium leading-snug group-hover:text-brand-fg">{s.text}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function BotRow({ at, children }: { at: Date | null; children: React.ReactNode }) {
  return (
    <div className="animate-rise flex gap-3 sm:gap-4">
      <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand text-brand-ink">
        <Bot className="h-[18px] w-[18px]" />
      </div>
      <div className="min-w-0 flex-1">
        {children}
        {at && <div className="mt-2 text-[11px] text-muted">{clock(at)}</div>}
      </div>
    </div>
  );
}

/** Jawaban bot ditulis langsung (tanpa gelembung) seperti aplikasi asisten AI. */
function Bubble({ children }: { children: React.ReactNode }) {
  return <div className="pt-1 text-[15px] leading-relaxed">{children}</div>;
}

function UserRow({ text, at }: { text: string; at: Date }) {
  return (
    <div className="animate-rise flex flex-col items-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg bg-brand px-4 py-2.5 text-[15px] text-brand-ink">
        {text}
      </div>
      <div className="mt-1.5 pr-1 text-[11px] text-muted">{clock(at)}</div>
    </div>
  );
}

type BotProps = {
  msg: Msg;
  isLatest: boolean;
  busy: boolean;
  onPick: (c: Candidate) => void;
  onExplain: (c: Candidate) => void;
  onAskAi: () => void;
  onRetry: () => void;
  onRate: (r: 1 | -1) => void;
};

function BotMessage({ msg, isLatest, busy, onPick, onExplain, onAskAi, onRetry, onRate }: BotProps) {
  if (msg.error) {
    return (
      <BotRow at={msg.at}>
        <div className="flex items-start gap-2.5 rounded-2xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
          <Alert className="mt-0.5 h-4 w-4" />
          <div className="flex-1">{msg.text}</div>
          {isLatest && (
            <button type="button" onClick={onRetry} disabled={busy} className="font-semibold underline underline-offset-2">
              Coba lagi
            </button>
          )}
        </div>
      </BotRow>
    );
  }

  const d = msg.data!;
  const options = d.options ?? [];
  // Balasan setelah user memilih kode: satu kartu kode yang bisa disalin
  const selected = d.kind === "answer" && options.length === 0 ? d.candidates[0] : undefined;
  const explainTarget = d.kind === "answer" ? (selected ?? options[0]) : undefined;
  const sources = d.sources ?? [];
  const isDoc = d.kind === "doc_answer";
  const aiUsed = d.used_llm && (d.kind === "explain" || isDoc);
  const canAskAi = isDoc && sources.length > 0 && !d.used_llm;
  const ratable = ["answer", "clarify", "explain", "doc_answer"].includes(d.kind) && d.message_id;
  const quotaOut = isLatest && (explainTarget || canAskAi) && !d.can_explain && d.quota?.llm_remaining === 0;

  return (
    <BotRow at={msg.at}>
      <Bubble>
        {aiUsed && (
          <span className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-accent">
            <Sparkle className="h-3.5 w-3.5" /> {isDoc ? "Dirangkum AI dari dokumen" : "Dijelaskan oleh AI"}
          </span>
        )}
        <RichText text={msg.text} />
      </Bubble>

      {selected && <SelectedCode c={selected} />}

      {options.length > 0 && (
        <CodeOptions
          options={options}
          highlightFirst={d.kind === "answer"}
          picked={msg.picked}
          disabled={busy || !isLatest}
          onPick={onPick}
        />
      )}

      {sources.length > 0 && <SourceList sources={sources} compact={d.used_llm} />}

      {aiUsed && (
        <p className="mt-2 text-xs text-muted">
          {isDoc
            ? "Rangkuman AI dapat keliru. Periksa kutipan asli pada sumber yang dirujuk."
            : "Penjelasan AI dapat keliru. Acuan resmi tetap uraian KBLI di atas."}
        </p>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {isLatest && explainTarget && d.can_explain && !msg.explained && (
          <AiButton onClick={() => onExplain(explainTarget)} disabled={busy} remaining={d.quota?.llm_remaining}>
            Jelaskan KBLI {explainTarget.kode} dengan AI
          </AiButton>
        )}
        {isLatest && canAskAi && d.can_explain && !msg.explained && (
          <AiButton onClick={onAskAi} disabled={busy} remaining={d.quota?.llm_remaining}>
            Rangkum jawabannya dengan AI
          </AiButton>
        )}
        {quotaOut && <span className="text-xs text-muted">Jatah AI hari ini sudah habis, akan terisi kembali besok.</span>}
        {ratable && (
          <div className="ml-auto flex items-center gap-1 text-muted">
            {msg.rated ? (
              <span className="text-xs">Terima kasih atas masukan Anda</span>
            ) : (
              <>
                <IconBtn label="Membantu" onClick={() => onRate(1)}><ThumbUp /></IconBtn>
                <IconBtn label="Kurang tepat" onClick={() => onRate(-1)}><ThumbDown /></IconBtn>
              </>
            )}
          </div>
        )}
      </div>
    </BotRow>
  );
}

function AiButton({ children, onClick, disabled, remaining }: {
  children: React.ReactNode; onClick: () => void; disabled: boolean; remaining?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 rounded-lg border border-go/60 bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent hover:border-go disabled:opacity-50"
    >
      <Sparkle className="h-3.5 w-3.5" />
      {children}
      {remaining !== undefined && <span className="text-xs opacity-75">· sisa {remaining}</span>}
    </button>
  );
}

function SelectedCode({ c }: { c: Candidate }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(c.kode);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard tidak tersedia */ }
  };
  return (
    <div className="mt-3 flex items-center gap-3 rounded-xl border border-brand/30 bg-brand-soft px-4 py-3">
      <span className="font-mono text-2xl font-semibold tracking-wider text-brand-fg">{c.kode}</span>
      <span className="min-w-0 flex-1 text-sm font-medium leading-snug">{c.judul}</span>
      <button
        type="button"
        onClick={copy}
        className="inline-flex items-center gap-1 rounded-lg bg-surface px-2.5 py-1.5 text-xs font-medium text-brand-fg shadow-sm hover:brightness-95"
      >
        {copied ? <><Check className="h-3.5 w-3.5" /> Tersalin</> : <><Copy className="h-3.5 w-3.5" /> Salin kode</>}
      </button>
    </div>
  );
}

function SourceList({ sources, compact }: { sources: Source[]; compact: boolean }) {
  const [open, setOpen] = useState<number | null>(compact ? null : sources[0]?.n ?? null);
  return (
    <div className="mt-3 space-y-2">
      {compact && <p className="text-xs font-semibold text-muted">Sumber</p>}
      {sources.map((s) => {
        const isOpen = open === s.n;
        return (
          <div key={s.n} id={`src-${s.n}`} className="rounded-xl border border-line bg-surface">
            <button
              type="button"
              onClick={() => setOpen(isOpen ? null : s.n)}
              aria-expanded={isOpen}
              className="flex w-full items-start gap-2.5 px-3.5 py-2.5 text-left"
            >
              <span className="mt-0.5 grid h-5 min-w-5 place-items-center rounded-md bg-brand px-1 font-mono text-[11px] font-semibold text-brand-ink">
                {s.n}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold leading-snug">{s.title}</span>
                <span className="block truncate text-xs text-muted">
                  {[s.where, s.pages].filter(Boolean).join(" · ")}
                </span>
              </span>
              <span className="mt-0.5 text-xs font-medium text-brand-fg">{isOpen ? "Tutup" : "Lihat"}</span>
            </button>
            {isOpen && (
              <blockquote className="whitespace-pre-line border-t border-line px-3.5 py-3 text-sm leading-relaxed text-ink/90">
                {s.snippet}
              </blockquote>
            )}
          </div>
        );
      })}
    </div>
  );
}

function IconBtn({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="rounded-md p-1.5 hover:bg-bubble hover:text-ink"
    >
      {children}
    </button>
  );
}
