"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError, kbli, type ImportJob, type ImportPreview, type KbliFull, type KbliRow, type KbliVersion, type Page,
} from "@/lib/api";
import { Card, ErrorNote, fmt, fmtDate } from "./ui";

const LIMIT = 25;
const SOURCE_LABEL: Record<string, string> = {
  oss: "OSS", manual: "Diedit manual", impor: "Impor Excel", dataset_final: "Dataset", lampiran: "Lampiran PP",
  kosong: "Kosong",
};
const FIELD_LABEL: Record<string, string> = { judul: "judul", uraian: "uraian", kata_kunci: "kata kunci" };

export default function KbliData({ isAdmin }: { isAdmin: boolean }) {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<KbliRow> | null>(null);
  const [editing, setEditing] = useState<string | "baru" | null>(null);
  const [versions, setVersions] = useState<KbliVersion[]>([]);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => { setQuery(q.trim()); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const loadList = useCallback(async () => {
    try { setPage(await kbli.list(query, offset)); } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat data KBLI.");
    }
  }, [query, offset]);
  const loadVersions = useCallback(async () => {
    try { setVersions(await kbli.versions()); } catch { /* ditampilkan di daftar */ }
  }, []);
  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => { loadVersions(); kbli.job().then(setJob).catch(() => {}); }, [loadVersions]);

  // pantau job impor
  useEffect(() => {
    if (job?.status !== "running") return;
    const t = setInterval(async () => {
      try {
        const j = await kbli.job();
        setJob(j);
        if (j.status !== "running") {
          if (j.status === "succeeded") flash(`Impor ${j.filename} selesai diterapkan.`);
          loadList();
          loadVersions();
        }
      } catch { /* coba lagi di putaran berikut */ }
    }, 1500);
    return () => clearInterval(t);
  }, [job?.status, loadList, loadVersions]);

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 4000);
  };

  const onImportFile = async (f: File | undefined) => {
    if (!f) return;
    setError(null);
    setPreview(null);
    try { setPreview(await kbli.preview(f)); } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal membaca file.");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const applyImport = async () => {
    if (!preview) return;
    if (preview.removed_count > 0 &&
        !confirm(`${preview.removed_count} kode akan DIHAPUS karena tidak ada di file. Lanjutkan?`)) return;
    try {
      setJob(await kbli.apply(preview.token));
      setPreview(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal menerapkan impor.");
    }
  };

  const restore = async (v: KbliVersion) => {
    if (!confirm(`Kembalikan data KBLI ke versi "${v.note}"? Pencarian langsung memakai data versi itu.`)) return;
    try {
      setVersions(await kbli.activate(v.id));
      loadList();
      setEditing(null);
      flash("Data dikembalikan ke versi yang dipilih.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memulihkan versi.");
    }
  };

  const running = job?.status === "running";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Data KBLI</h2>
          <p className="text-xs text-muted">
            Perubahan langsung dipakai pencarian tanpa restart. Setiap perubahan tersimpan sebagai versi dan bisa dikembalikan.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a href={kbli.exportUrl} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium hover:border-brand hover:text-brand-fg">
            Unduh Excel
          </a>
          <button type="button" disabled={running} onClick={() => fileRef.current?.click()}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium hover:border-brand hover:text-brand-fg disabled:opacity-50">
            Impor Excel
          </button>
          <input ref={fileRef} type="file" accept=".xlsx" className="hidden" onChange={(e) => onImportFile(e.target.files?.[0])} />
          <button type="button" onClick={() => setEditing("baru")}
            className="rounded-lg bg-go px-3 py-2 text-sm font-semibold text-go-ink hover:brightness-95">
            + Tambah kode
          </button>
        </div>
      </div>

      {notice && <p role="status" className="rounded-xl bg-accent-soft px-4 py-2.5 text-sm font-medium text-accent">{notice}</p>}
      {error && <ErrorNote message={error} onRetry={() => { setError(null); loadList(); }} />}
      {running && <JobProgress job={job!} />}
      {job?.status === "failed" && <ErrorNote message={`Impor gagal: ${job.message}`} />}
      {preview && <PreviewCard p={preview} onApply={applyImport} onCancel={() => setPreview(null)} />}

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
        <section className="min-w-0 space-y-3">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari kode, judul, atau kata kunci…"
            aria-label="Cari data KBLI"
            className="w-full rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm outline-none focus:border-brand" />
          <div className="overflow-hidden rounded-2xl border border-line bg-surface">
            <ul className="divide-y divide-line">
              {page?.items.map((r) => (
                <li key={r.kode}>
                  <button type="button" onClick={() => setEditing(r.kode)}
                    className={`flex w-full gap-3 px-4 py-3 text-left hover:bg-paper ${editing === r.kode ? "bg-brand-soft" : ""}`}>
                    <span className="mt-0.5 h-fit rounded-md bg-brand px-1.5 py-0.5 font-mono text-xs font-semibold text-brand-ink">{r.kode}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium leading-snug">{r.judul}</span>
                      <span className="mt-0.5 block truncate text-xs text-muted">{r.uraian || "— uraian kosong —"}</span>
                      {r.kata_kunci.length > 0 && (
                        <span className="mt-1 flex flex-wrap gap-1">
                          {r.kata_kunci.slice(0, 5).map((k) => (
                            <span key={k} className="max-w-[16rem] truncate rounded-full bg-bubble px-2 py-0.5 text-[11px] text-muted">{k}</span>
                          ))}
                          {r.kata_kunci.length > 5 && <span className="text-[11px] text-muted">+{r.kata_kunci.length - 5}</span>}
                        </span>
                      )}
                    </span>
                    {r.uraian_source && (
                      <span className="hidden shrink-0 text-[11px] text-muted sm:block">{SOURCE_LABEL[r.uraian_source] ?? r.uraian_source}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
            {page && page.items.length === 0 && <p className="py-10 text-center text-sm text-muted">Tidak ada kode yang cocok.</p>}
            {!page && <p className="py-10 text-center text-sm text-muted">Memuat…</p>}
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
        </section>

        <aside className="space-y-4 lg:sticky lg:top-32">
          {editing ? (
            <Editor
              key={editing}
              kode={editing === "baru" ? null : editing}
              isAdmin={isAdmin}
              onClose={() => setEditing(null)}
              onSaved={(msg, kode) => { flash(msg); loadList(); loadVersions(); setEditing(kode); }}
            />
          ) : (
            <Versions versions={versions} isAdmin={isAdmin} onRestore={restore} />
          )}
        </aside>
      </div>
    </div>
  );
}

function Editor({ kode, isAdmin, onClose, onSaved }: {
  kode: string | null; isAdmin: boolean; onClose: () => void; onSaved: (msg: string, kode: string | null) => void;
}) {
  const isNew = kode === null;
  const [orig, setOrig] = useState<KbliFull | null>(null);
  const [newKode, setNewKode] = useState("");
  const [judul, setJudul] = useState("");
  const [uraian, setUraian] = useState("");
  const [kw, setKw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isNew) return;
    kbli.get(kode!).then((c) => {
      setOrig(c);
      setJudul(c.judul);
      setUraian(c.uraian ?? "");
      setKw((c.kata_kunci ?? []).join(", "));
    }).catch((e) => setError(e instanceof ApiError ? e.message : "Gagal memuat kode."));
  }, [kode, isNew]);

  const kwList = kw.split(",").map((s) => s.trim()).filter(Boolean);
  const dirty = isNew
    ? newKode.length > 0 && judul.trim().length > 0
    : orig !== null && (judul !== orig.judul || uraian !== (orig.uraian ?? "") ||
        kwList.join("|").toLowerCase() !== (orig.kata_kunci ?? []).join("|").toLowerCase());

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const target = isNew ? newKode.trim() : kode!;
    try {
      const c = await kbli.save(target, { judul, uraian, kata_kunci: kwList });
      setOrig(c);
      onSaved(isNew ? `Kode ${c.kode} ditambahkan.` : `Perubahan ${c.kode} disimpan dan langsung dipakai pencarian.`, c.kode);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Gagal menyimpan.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!kode || !confirm(`Hapus kode ${kode} dari data? Bisa dikembalikan lewat Riwayat versi.`)) return;
    try {
      await kbli.remove(kode);
      onSaved(`Kode ${kode} dihapus.`, null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Gagal menghapus.");
    }
  };

  return (
    <Card
      title={isNew ? "Tambah kode KBLI" : `Edit KBLI ${kode}`}
      action={<button type="button" onClick={onClose} className="text-xs font-medium text-muted hover:text-ink">Tutup</button>}
    >
      {!isNew && !orig && !error && <p className="text-sm text-muted">Memuat…</p>}
      {(isNew || orig) && (
        <form onSubmit={save} className="space-y-3">
          {isNew && (
            <Field label="Kode (5 digit)">
              <input value={newKode} onChange={(e) => setNewKode(e.target.value.replace(/\D/g, "").slice(0, 5))}
                inputMode="numeric" required className={inputCls + " font-mono"} placeholder="mis. 10391" />
            </Field>
          )}
          <Field label="Judul">
            <input value={judul} onChange={(e) => setJudul(e.target.value)} required minLength={3} className={inputCls} />
          </Field>
          <Field label="Uraian" hint="Mengubah judul/uraian membuat ulang embedding kode ini (± beberapa detik).">
            <textarea value={uraian} onChange={(e) => setUraian(e.target.value)} rows={9}
              className={inputCls + " resize-y leading-relaxed"} />
          </Field>
          <Field label="Kata kunci" hint="Pisahkan dengan koma. Istilah sehari-hari yang sering dipakai pengguna, mis.: laundry, binatu, cuci kiloan.">
            <textarea value={kw} onChange={(e) => setKw(e.target.value)} rows={3} className={inputCls + " resize-y"} />
          </Field>
          {orig && orig.scopes.length > 0 && (
            <div className="rounded-xl bg-paper p-3 text-xs">
              <p className="mb-1 font-semibold text-muted">Ruang lingkup per sektor</p>
              <ul className="space-y-0.5">
                {orig.scopes.map((s, i) => <li key={i}><b className="font-medium">{s.sektor}:</b> {s.ruang_lingkup}</li>)}
              </ul>
              <p className="mt-1.5 text-muted">Ruang lingkup diubah lewat Impor Excel (sheet &quot;Ruang Lingkup&quot;).</p>
            </div>
          )}
          {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
          <div className="flex items-center gap-2 pt-1">
            <button type="submit" disabled={busy || !dirty}
              className="rounded-xl bg-go px-4 py-2 text-sm font-semibold text-go-ink hover:brightness-95 disabled:opacity-45">
              {busy ? "Menyimpan…" : "Simpan"}
            </button>
            <button type="button" onClick={onClose} className="rounded-xl px-3 py-2 text-sm text-muted hover:text-ink">Batal</button>
            {!isNew && isAdmin && (
              <button type="button" onClick={remove} className="ml-auto rounded-xl px-3 py-2 text-sm font-medium text-danger hover:bg-danger-soft">
                Hapus kode
              </button>
            )}
          </div>
        </form>
      )}
      {error && !orig && !isNew && <ErrorNote message={error} />}
    </Card>
  );
}

function Versions({ versions, isAdmin, onRestore }: {
  versions: KbliVersion[]; isAdmin: boolean; onRestore: (v: KbliVersion) => void;
}) {
  return (
    <Card title="Riwayat versi">
      {versions.length === 0 ? <p className="text-sm text-muted">Belum ada riwayat.</p> : (
        <ol className="space-y-2">
          {versions.slice(0, 15).map((v) => (
            <li key={v.id} className={`rounded-xl border px-3 py-2.5 ${v.active ? "border-go bg-accent-soft" : "border-line"}`}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium leading-snug">{v.note}</p>
                  <p className="text-[11px] text-muted">
                    {v.created_at ? fmtDate(v.created_at) : "Data dari pipeline"}
                    {v.created_by && ` · ${v.created_by}`}
                    {v.stats?.kode ? ` · ${fmt(v.stats.kode)} kode` : ""}
                  </p>
                </div>
                {v.active ? (
                  <span className="rounded-full bg-go px-2 py-0.5 text-[11px] font-semibold text-go-ink">Aktif</span>
                ) : isAdmin ? (
                  <button type="button" onClick={() => onRestore(v)}
                    className="rounded-lg border border-line px-2 py-1 text-[11px] font-medium hover:border-brand hover:text-brand-fg">
                    Pulihkan
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
      <p className="mt-3 text-[11px] text-muted">Klik sebuah kode di daftar untuk mengeditnya.</p>
    </Card>
  );
}

function PreviewCard({ p, onApply, onCancel }: { p: ImportPreview; onApply: () => void; onCancel: () => void }) {
  const nothing = p.added_count + p.changed_count + p.removed_count === 0;
  return (
    <Card title={`Pratinjau impor: ${p.filename}`}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Stat label="Kode di file" value={p.total} />
        <Stat label="Baru" value={p.added_count} tone="go" />
        <Stat label="Berubah" value={p.changed_count} tone="brand" />
        <Stat label="Dihapus" value={p.removed_count} tone={p.removed_count ? "danger" : undefined} />
        <Stat label="Tetap" value={p.unchanged_count} />
      </div>
      {p.errors.length > 0 && (
        <div className="mt-3 rounded-xl bg-danger-soft p-3 text-xs text-danger">
          <p className="mb-1 font-semibold">{p.errors.length} baris dilewati:</p>
          <ul className="list-inside list-disc space-y-0.5">{p.errors.slice(0, 8).map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}
      <div className="mt-3 grid gap-3 text-xs sm:grid-cols-3">
        <ChangeList title="Baru" items={p.added.map((a) => `${a.kode} ${a.judul}`)} more={p.added_count - p.added.length} />
        <ChangeList title="Berubah"
          items={p.changed.map((c) => `${c.kode} — ${c.fields.map((f) => FIELD_LABEL[f] ?? f).join(", ")}`)}
          more={p.changed_count - p.changed.length} />
        <ChangeList title="Dihapus" items={p.removed.map((a) => `${a.kode} ${a.judul}`)} more={p.removed_count - p.removed.length} />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={onApply} disabled={nothing}
          className="rounded-xl bg-go px-4 py-2 text-sm font-semibold text-go-ink hover:brightness-95 disabled:opacity-45">
          Terapkan
        </button>
        <button type="button" onClick={onCancel} className="rounded-xl px-3 py-2 text-sm text-muted hover:text-ink">Batal</button>
        <span className="text-xs text-muted">
          {nothing ? "Tidak ada perubahan." : `${fmt(p.reembed_count)} kode perlu dihitung ulang embedding-nya.`}
        </span>
      </div>
    </Card>
  );
}

function ChangeList({ title, items, more }: { title: string; items: string[]; more: number }) {
  return (
    <div className="rounded-xl bg-paper p-3">
      <p className="mb-1 font-semibold">{title}</p>
      {items.length === 0 ? <p className="text-muted">–</p> : (
        <ul className="max-h-40 space-y-0.5 overflow-y-auto">
          {items.map((t) => <li key={t} className="truncate">{t}</li>)}
          {more > 0 && <li className="text-muted">+{fmt(more)} lainnya</li>}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "go" | "brand" | "danger" }) {
  const color = tone === "go" ? "text-accent" : tone === "brand" ? "text-brand-fg" : tone === "danger" ? "text-danger" : "";
  return (
    <div className="rounded-xl border border-line px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={`text-xl font-bold tabular-nums ${color}`}>{fmt(value)}</div>
    </div>
  );
}

function JobProgress({ job }: { job: ImportJob }) {
  const pct = Math.round((job.progress ?? 0) * 100);
  return (
    <div className="rounded-2xl border border-line bg-surface p-4" role="status">
      <div className="mb-2 flex justify-between text-sm">
        <span className="font-medium">Menerapkan {job.filename}… <span className="text-muted">{job.message}</span></span>
        <span className="tabular-nums text-muted">{pct}%</span>
      </div>
      <div className="h-2 rounded-full bg-bubble"><div className="h-2 rounded-full bg-go transition-all" style={{ width: `${pct}%` }} /></div>
      <p className="mt-2 text-xs text-muted">Pencarian tetap memakai data lama sampai proses ini selesai.</p>
    </div>
  );
}

const inputCls = "w-full rounded-xl border border-line bg-bubble px-3 py-2 text-sm outline-none focus:border-brand focus:bg-surface";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}
