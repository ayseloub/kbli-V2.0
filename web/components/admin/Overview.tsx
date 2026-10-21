"use client";

import { useCallback, useEffect, useState } from "react";
import { admin, ApiError, type Stats } from "@/lib/api";
import { Card, ErrorNote, fmt, Segmented } from "./ui";

const KIND_LABEL: Record<string, string> = {
  answer: "Dijawab yakin",
  clarify: "Diberi pilihan",
  refuse: "Ditolak (luar topik)",
  explain: "Minta penjelasan AI",
  greeting: "Sapaan",
  thanks: "Terima kasih",
  error: "Error",
};

export default function Overview() {
  const [days, setDays] = useState(7);
  const [stats, setStats] = useState<Stats | null>(null);
  const [top, setTop] = useState<{ kode: string; judul: string; n: number }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, t] = await Promise.all([admin.stats(days), admin.topCodes(days)]);
      setStats(s);
      setTop(t);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat data.");
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  const rated = stats ? stats.thumbs_up + stats.thumbs_down : 0;
  const kinds = stats
    ? Object.entries(stats.by_kind).filter(([k]) => ["answer", "clarify", "refuse", "explain"].includes(k))
        .sort((a, b) => b[1] - a[1])
    : [];
  const kindMax = Math.max(1, ...kinds.map(([, n]) => n));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-bold">Ringkasan</h2>
        <Segmented
          label="Rentang waktu"
          value={days}
          onChange={setDays}
          options={[{ value: 7, label: "7 hari" }, { value: 30, label: "30 hari" }, { value: 90, label: "90 hari" }]}
        />
      </div>

      {error && <ErrorNote message={error} onRetry={load} />}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="Pencarian" value={stats && fmt(stats.searches)} />
        <Tile label="Pengunjung" value={stats && fmt(stats.visitors)} sub={stats && `${fmt(stats.sessions)} sesi`} />
        <Tile label="Kode dipilih user" value={stats && fmt(stats.selections)} />
        <Tile
          label="Puas (👍)"
          value={stats && (rated ? `${Math.round((stats.thumbs_up / rated) * 100)}%` : "–")}
          sub={stats && `${fmt(stats.thumbs_up)} 👍 · ${fmt(stats.thumbs_down)} 👎`}
        />
        <Tile
          label="Panggilan AI"
          value={stats && fmt(stats.llm_calls)}
          sub={stats && `${fmt(stats.tokens_in + stats.tokens_out)} token`}
        />
      </div>

      <Card title="Pencarian per hari">
        {stats ? <DailyBars data={stats.daily} /> : <div className="h-40" />}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Cara sistem menjawab">
          {kinds.length === 0 ? (
            <Empty />
          ) : (
            <ul className="space-y-2.5">
              {kinds.map(([k, n]) => (
                <li key={k}>
                  <div className="mb-1 flex justify-between text-sm">
                    <span>{KIND_LABEL[k] ?? k}</span>
                    <span className="font-mono tabular-nums text-muted">{fmt(n)}</span>
                  </div>
                  <div className="h-2 rounded-full bg-paper">
                    <div className="h-2 rounded-full bg-brand" style={{ width: `${(n / kindMax) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Kode paling sering dipilih">
          {top.length === 0 ? (
            <Empty />
          ) : (
            <ol className="divide-y divide-line">
              {top.slice(0, 10).map((c) => (
                <li key={c.kode} className="flex items-center gap-3 py-2 text-sm">
                  <span className="rounded-md bg-brand px-1.5 py-0.5 font-mono text-xs font-semibold text-brand-ink">{c.kode}</span>
                  <span className="min-w-0 flex-1 truncate">{c.judul}</span>
                  <span className="font-mono tabular-nums text-muted">{fmt(c.n)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string | null | undefined; sub?: string | null }) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value ?? <span className="text-muted">…</span>}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

function Empty() {
  return <p className="py-6 text-center text-sm text-muted">Belum ada data di rentang ini.</p>;
}

function DailyBars({ data }: { data: Stats["daily"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.searches));
  const nice = niceMax(max);
  const label = (s: string) => new Intl.DateTimeFormat("id-ID", { day: "numeric", month: "short" }).format(new Date(s));
  const h = hover !== null ? data[hover] : null;

  return (
    <div>
      <div className="mb-2 h-5 text-xs text-muted" aria-live="polite">
        {h ? <><b className="text-ink">{fmt(h.searches)}</b> pencarian · {fmt(h.llm_calls)} panggilan AI · {label(h.day)}</>
           : "Arahkan kursor ke batang untuk detail"}
      </div>
      <div className="relative h-40">
        <div className="absolute inset-x-0 top-0 border-t border-dashed border-line" />
        <span className="absolute right-0 -top-4 font-mono text-[10px] text-muted">{fmt(nice)}</span>
        <div className="absolute inset-x-0 bottom-0 border-t border-line" />
        <div className="absolute inset-0 flex items-end gap-[2px]" role="list" aria-label="Pencarian per hari">
          {data.map((d, i) => (
            <div
              key={d.day}
              role="listitem"
              aria-label={`${label(d.day)}: ${d.searches} pencarian`}
              tabIndex={0}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              className="group flex h-full flex-1 cursor-default items-end outline-none"
            >
              <div
                className={`w-full rounded-t-[4px] transition-colors ${
                  hover === i ? "bg-accent" : "bg-brand"
                } ${d.searches === 0 ? "opacity-0" : ""}`}
                style={{ height: `${(d.searches / nice) * 100}%`, minHeight: d.searches ? 2 : 0 }}
              />
            </div>
          ))}
        </div>
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[10px] text-muted">
        <span>{data[0] && label(data[0].day)}</span>
        <span>{data.at(-1) && label(data.at(-1)!.day)}</span>
      </div>
    </div>
  );
}

function niceMax(n: number) {
  const p = 10 ** Math.floor(Math.log10(n));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((v) => v >= n) ?? n;
}
