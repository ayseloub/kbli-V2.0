"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, docs, type DocChunk, type DocItem } from "@/lib/api";
import { Card, ErrorNote, fmt, fmtDate } from "./ui";

const ACCEPT = ".pdf,.docx,.txt,.md";
const PROCESSING = new Set(["queued", "parsing", "embedding"]);

function statusView(d: DocItem): { label: string; cls: string } {
  switch (d.status) {
    case "queued": return { label: "Antri", cls: "bg-paper text-muted" };
    case "parsing": return { label: "Membaca file…", cls: "bg-accent-soft text-accent" };
    case "embedding":
      return { label: `Memproses ${Math.round((d.job_progress ?? 0) * 100)}%`, cls: "bg-accent-soft text-accent" };
    case "ready": return { label: "Siap", cls: "bg-brand-soft text-brand-fg" };
    case "failed": return { label: "Gagal", cls: "bg-danger-soft text-danger" };
  }
}

export default function Documents({ canDelete }: { canDelete: boolean }) {
  const [items, setItems] = useState<DocItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await docs.list());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat dokumen.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  // selama ada dokumen yang diproses, cek status tiap 2,5 detik
  const processing = items?.some((d) => PROCESSING.has(d.status));
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [processing, load]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Aksi gagal.");
    }
  };

  const active = items?.filter((d) => d.is_active).length ?? 0;
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold">Dokumen</h2>
        <p className="text-xs text-muted">
          Dokumen aktif dipakai chatbot untuk menjawab pertanyaan soal aturan & prosedur perizinan.
          {items && ` ${fmt(active)} dari ${fmt(items.length)} dokumen aktif.`}
        </p>
      </div>

      <UploadCard onUploaded={load} />

      {error && <ErrorNote message={error} onRetry={load} />}

      {items && items.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line py-10 text-center text-sm text-muted">
          Belum ada dokumen. Upload PP, Permen, FAQ, atau SOP perizinan di atas.
        </p>
      )}

      <ul className="space-y-2.5">
        {items?.map((d) => {
          const st = statusView(d);
          return (
            <li key={d.id} className="rounded-2xl border border-line bg-surface p-4">
              <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
                <div className="min-w-0 flex-1 basis-64">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold leading-snug">{d.title}</h3>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.cls}`}>{st.label}</span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted">
                    {d.original_filename}
                    {d.status === "ready" && ` · ${fmt(d.page_count ?? 0)} hal. · ${fmt(d.chunk_count)} potongan`}
                    {` · ${fmtDate(d.created_at)}`}
                    {d.uploaded_by_name && ` · oleh ${d.uploaded_by_name}`}
                  </p>
                  {d.status === "failed" && d.status_message && (
                    <p className="mt-1.5 text-xs text-danger">{d.status_message}</p>
                  )}
                  {d.status === "embedding" && (
                    <div className="mt-2 h-1.5 max-w-xs rounded-full bg-paper">
                      <div className="h-1.5 rounded-full bg-accent transition-all"
                        style={{ width: `${Math.round((d.job_progress ?? 0) * 100)}%` }} />
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <label
                    className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${
                      d.status === "ready" ? "cursor-pointer border-line" : "cursor-not-allowed border-line opacity-50"
                    }`}
                    title={d.status === "ready" ? "Aktifkan agar dipakai chatbot" : "Tunggu sampai status Siap"}
                  >
                    <input
                      type="checkbox"
                      checked={d.is_active}
                      disabled={d.status !== "ready"}
                      onChange={(e) => act(() => docs.update(d.id, { is_active: e.target.checked }))}
                      className="h-4 w-4 accent-[var(--brand)]"
                    />
                    Aktif
                  </label>
                  {d.status === "ready" && (
                    <Btn onClick={() => setPreview(preview === d.id ? null : d.id)}>
                      {preview === d.id ? "Tutup" : "Lihat potongan"}
                    </Btn>
                  )}
                  {(d.status === "ready" || d.status === "failed") && (
                    <Btn onClick={() => act(() => docs.reprocess(d.id))}>Proses ulang</Btn>
                  )}
                  {canDelete && (
                    <Btn
                      danger
                      onClick={() => {
                        if (confirm(`Hapus "${d.title}"? File dan semua potongannya ikut terhapus.`)) {
                          if (preview === d.id) setPreview(null);
                          act(() => docs.remove(d.id));
                        }
                      }}
                    >
                      Hapus
                    </Btn>
                  )}
                </div>
              </div>
              {preview === d.id && <ChunkPreview id={d.id} total={d.chunk_count} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [effective, setEffective] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const choose = (f: File | undefined | null) => {
    if (!f) return;
    setFile(f);
    setError(null);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim());
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await docs.upload(file, title.trim(), description.trim() || undefined, effective || undefined);
      setFile(null);
      setTitle("");
      setDescription("");
      setEffective("");
      if (input.current) input.current.value = "";
      onUploaded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload gagal.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Upload dokumen">
      <form onSubmit={submit} className="space-y-3">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); choose(e.dataTransfer.files?.[0]); }}
          onClick={() => input.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed px-4 py-6 text-center text-sm transition ${
            drag ? "border-brand bg-brand-soft" : "border-line hover:border-brand"
          }`}
        >
          <input ref={input} type="file" accept={ACCEPT} className="hidden" onChange={(e) => choose(e.target.files?.[0])} />
          {file ? (
            <span><b>{file.name}</b> <span className="text-muted">· {(file.size / 1024 / 1024).toFixed(1)} MB</span></span>
          ) : (
            <span className="text-muted">
              Tarik file ke sini atau <span className="font-semibold text-brand-fg">pilih file</span>
              <br /><span className="text-xs">PDF (teks, bukan hasil scan), DOCX, TXT, atau MD</span>
            </span>
          )}
        </div>
        {file && (
          <div className="grid gap-3 sm:grid-cols-[1fr_180px]">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Judul</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={3} maxLength={200}
                placeholder="mis. PP 5 Tahun 2021 tentang Perizinan Berusaha Berbasis Risiko"
                className="w-full rounded-xl border border-line bg-paper px-3 py-2 outline-none focus:border-brand" />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Berlaku sejak <span className="font-normal text-muted">(opsional)</span></span>
              <input type="date" value={effective} onChange={(e) => setEffective(e.target.value)}
                className="w-full rounded-xl border border-line bg-paper px-3 py-2 outline-none focus:border-brand" />
            </label>
            <label className="block text-sm sm:col-span-2">
              <span className="mb-1 block font-medium">Keterangan <span className="font-normal text-muted">(opsional)</span></span>
              <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000}
                placeholder="mis. Versi konsolidasi, sumber jdih.go.id"
                className="w-full rounded-xl border border-line bg-paper px-3 py-2 outline-none focus:border-brand" />
            </label>
          </div>
        )}
        {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
        {file && (
          <div className="flex items-center gap-3">
            <button type="submit" disabled={busy || title.trim().length < 3}
              className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-ink hover:opacity-90 disabled:opacity-50">
              {busy ? "Mengunggah…" : "Upload & proses"}
            </button>
            <span className="text-xs text-muted">Setelah selesai diproses, cek potongannya lalu aktifkan.</span>
          </div>
        )}
      </form>
    </Card>
  );
}

function ChunkPreview({ id, total }: { id: string; total: number }) {
  const [chunks, setChunks] = useState<DocChunk[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const more = useCallback(async (offset: number) => {
    setLoading(true);
    try {
      const d = await docs.get(id, offset);
      setChunks((c) => (offset === 0 ? d.chunks : [...c, ...d.chunks]));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat potongan.");
    } finally {
      setLoading(false);
    }
  }, [id]);
  useEffect(() => { more(0); }, [more]);

  return (
    <div className="mt-4 border-t border-line pt-4">
      <p className="mb-3 text-xs text-muted">
        Potongan inilah yang dicari chatbot. Kalau teksnya berantakan (kolom tertukar, huruf hilang),
        biasanya PDF-nya hasil scan atau tata letaknya rumit. Coba versi DOCX atau PDF lain.
      </p>
      {error && <ErrorNote message={error} />}
      <ol className="space-y-2.5">
        {chunks.map((c) => (
          <li key={c.chunk_index} className="rounded-xl bg-paper p-3">
            <div className="mb-1 flex flex-wrap gap-x-2 text-[11px] text-muted">
              <span className="font-mono">#{c.chunk_index + 1}</span>
              {c.heading_path?.length ? <span className="font-medium text-ink">{c.heading_path.join(" › ")}</span> : null}
              {c.page_start != null && <span>hal. {c.page_start}{c.page_end && c.page_end !== c.page_start ? `–${c.page_end}` : ""}</span>}
              {c.token_count != null && <span>{c.token_count} kata</span>}
            </div>
            <p className="line-clamp-6 whitespace-pre-line text-sm leading-relaxed">{c.content}</p>
          </li>
        ))}
      </ol>
      {chunks.length < total && (
        <button type="button" disabled={loading} onClick={() => more(chunks.length)}
          className="mt-3 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm disabled:opacity-50">
          {loading ? "Memuat…" : `Muat lagi (${fmt(total - chunks.length)} tersisa)`}
        </button>
      )}
    </div>
  );
}

function Btn({ children, onClick, danger }: { children: React.ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium ${
        danger ? "border-danger/30 text-danger hover:bg-danger-soft" : "border-line hover:border-brand hover:text-brand-fg"
      }`}
    >
      {children}
    </button>
  );
}
