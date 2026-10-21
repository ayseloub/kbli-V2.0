"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { admin, ApiError, type Page, type SearchFilter, type SearchRow } from "@/lib/api";
import { ErrorNote, fmt, fmtDate } from "./ui";

const FILTERS: { id: SearchFilter; label: string; hint: string }[] = [
  { id: "all", label: "Semua", hint: "Semua pencarian" },
  { id: "not_top1", label: "Pilih bukan #1", hint: "User memilih kode selain urutan pertama — kandidat perbaikan ranking" },
  { id: "negative", label: "Dapat 👎", hint: "Jawaban yang dinilai kurang tepat" },
  { id: "refuse", label: "Ditolak", hint: "Dianggap luar topik — cek apakah sebenarnya pertanyaan KBLI" },
  { id: "clarify", label: "Diberi pilihan", hint: "Sistem ragu dan menampilkan 5 pilihan" },
  { id: "no_select", label: "Tidak memilih", hint: "User tidak mengklik kode apa pun" },
];

const KIND: Record<string, { label: string; cls: string }> = {
  answer: { label: "Yakin", cls: "bg-brand-soft text-brand-fg" },
  clarify: { label: "Pilihan", cls: "bg-accent-soft text-accent" },
  refuse: { label: "Ditolak", cls: "bg-danger-soft text-danger" },
};

const LIMIT = 50;

export default function SearchLog() {
  const [filter, setFilter] = useState<SearchFilter>("all");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<SearchRow> | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // debounce kotak pencarian
  useEffect(() => {
    const t = setTimeout(() => { setQuery(q.trim()); setOffset(0); }, 350);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPage(await admin.searches({ filter, q: query || undefined, limit: LIMIT, offset }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat log.");
    }
  }, [filter, query, offset]);

  useEffect(() => { load(); }, [load]);

  const active = FILTERS.find((f) => f.id === filter)!;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold">Log pencarian</h2>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Cari teks query…"
          aria-label="Cari teks query"
          className="w-full rounded-xl border border-line bg-surface px-3.5 py-2 text-sm outline-none focus:border-brand sm:w-64"
        />
      </div>

      <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            title={f.hint}
            aria-pressed={filter === f.id}
            onClick={() => { setFilter(f.id); setOffset(0); setOpen(null); }}
            className={`whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium ${
              filter === f.id ? "border-brand bg-brand text-brand-ink" : "border-line bg-surface hover:border-brand"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted">{active.hint}{page && ` · ${fmt(page.total)} pencarian`}</p>

      {error && <ErrorNote message={error} onRetry={load} />}

      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-4 py-2.5 font-medium">Waktu</th>
              <th className="px-4 py-2.5 font-medium">Query user</th>
              <th className="px-4 py-2.5 font-medium">Hasil #1</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Dipilih</th>
              <th className="px-4 py-2.5 text-center font-medium">Nilai</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {page?.items.map((r) => {
              const top = r.results?.[0];
              const kind = KIND[r.reply_kind ?? ""];
              const isOpen = open === r.id;
              return (
                <Fragment key={r.id}>
                  <tr
                    onClick={() => setOpen(isOpen ? null : r.id)}
                    className="cursor-pointer align-top hover:bg-paper/60"
                    aria-expanded={isOpen}
                  >
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-muted">{fmtDate(r.created_at)}</td>
                    <td className="max-w-xs px-4 py-3"><span className="line-clamp-2">{r.query}</span></td>
                    <td className="px-4 py-3">
                      {top ? (
                        <span className="flex items-baseline gap-1.5">
                          <code className="font-mono text-xs font-semibold">{top.kode}</code>
                          <span className="line-clamp-1 text-xs text-muted">{top.judul}</span>
                        </span>
                      ) : "–"}
                    </td>
                    <td className="px-4 py-3">
                      {kind ? <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${kind.cls}`}>{kind.label}</span> : "–"}
                    </td>
                    <td className="px-4 py-3">
                      {r.selected ? (
                        <code className={`font-mono text-xs font-semibold ${r.selected !== r.top1 ? "text-accent" : ""}`}>
                          {r.selected}{r.selected !== r.top1 && " ≠ #1"}
                        </code>
                      ) : <span className="text-muted">–</span>}
                    </td>
                    <td className="px-4 py-3 text-center">{r.rating === 1 ? "👍" : r.rating === -1 ? "👎" : ""}</td>
                  </tr>
                  {isOpen && (
                    <tr className="bg-paper/60">
                      <td />
                      <td colSpan={5} className="px-4 pb-4 pt-1">
                        <ol className="space-y-1 text-xs">
                          {r.results?.map((c, i) => (
                            <li key={c.kode} className={`flex gap-2 ${c.kode === r.selected ? "font-semibold text-brand-fg" : ""}`}>
                              <span className="w-4 text-right text-muted">{i + 1}</span>
                              <code className="font-mono">{c.kode}</code>
                              <span className="flex-1">{c.judul}</span>
                              <span className="font-mono text-muted">{c.score?.toFixed(3)}</span>
                              {c.kode === r.selected && <span>← dipilih</span>}
                            </li>
                          ))}
                        </ol>
                        <p className="mt-2 text-[11px] text-muted">
                          Keyakinan: {r.confidence ?? "–"} · {r.latency_ms ?? "–"} ms · sesi {r.session_id.slice(0, 8)}
                        </p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {page && page.items.length === 0 && (
          <p className="py-10 text-center text-sm text-muted">Tidak ada pencarian untuk filter ini.</p>
        )}
        {!page && !error && <p className="py-10 text-center text-sm text-muted">Memuat…</p>}
      </div>

      {page && page.total > LIMIT && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <span className="text-muted">{offset + 1}–{Math.min(offset + LIMIT, page.total)} dari {fmt(page.total)}</span>
          <button type="button" disabled={offset === 0} onClick={() => setOffset(offset - LIMIT)}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 disabled:opacity-40">Sebelumnya</button>
          <button type="button" disabled={offset + LIMIT >= page.total} onClick={() => setOffset(offset + LIMIT)}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 disabled:opacity-40">Berikutnya</button>
        </div>
      )}
    </div>
  );
}
