"use client";

import type { Candidate } from "@/lib/api";
import { Check } from "./Icons";

type Props = {
  options: Candidate[];
  highlightFirst: boolean;      // sistem yakin -> #1 diberi label "Paling cocok"
  picked?: string;
  disabled?: boolean;
  onPick: (c: Candidate) => void;
};

/** Daftar kode terdekat (top-k) yang bisa diklik user. */
export default function CodeOptions({ options, highlightFirst, picked, disabled, onPick }: Props) {
  const locked = Boolean(picked) || disabled;
  return (
    <ol className="mt-3 space-y-2" aria-label="Pilihan kode KBLI">
      {options.map((c, i) => {
        const isPicked = picked === c.kode;
        const isTop = highlightFirst && i === 0;
        return (
          <li key={c.kode}>
            <button
              type="button"
              disabled={locked}
              onClick={() => onPick(c)}
              aria-pressed={isPicked}
              className={[
                "group w-full rounded-xl border px-3.5 py-3 text-left transition",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand",
                isPicked
                  ? "border-brand bg-brand-soft"
                  : isTop
                    ? "border-go bg-accent-soft"
                    : "border-line bg-surface",
                !locked && "hover:border-brand hover:shadow-sm cursor-pointer",
                locked && !isPicked && "opacity-55",
              ].filter(Boolean).join(" ")}
            >
              <div className="flex items-start gap-3">
                <span className="mt-0.5 w-5 shrink-0 text-right font-mono text-xs text-muted">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="rounded-md bg-brand px-1.5 py-0.5 font-mono text-[13px] font-semibold tracking-wide text-brand-ink">
                      {c.kode}
                    </span>
                    <span className="font-semibold leading-snug">{c.judul}</span>
                    {isTop && !isPicked && (
                      <span className="rounded-full bg-go px-2 py-0.5 text-[11px] font-semibold text-go-ink">
                        Paling sesuai
                      </span>
                    )}
                    {isPicked && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-[11px] font-semibold text-brand-ink">
                        <Check className="h-3 w-3" /> Dipilih
                      </span>
                    )}
                  </div>
                  {c.ringkasan && (
                    <p className="mt-1 line-clamp-2 text-sm leading-snug text-muted">{c.ringkasan}</p>
                  )}
                  {c.batasan.length > 0 && (
                    <p className="mt-1.5 line-clamp-1 text-xs font-medium text-warn">
                      Ruang lingkup terbatas: {c.batasan.join("; ")}
                    </p>
                  )}
                </div>
              </div>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
