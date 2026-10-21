import { Fragment } from "react";

/** Render teks balasan: paragraf dipisah baris kosong, **tebal**, dan baris baru. */
export default function RichText({ text }: { text: string }) {
  const paragraphs = text.split(/\n{2,}/).filter(Boolean);
  return (
    <div className="space-y-2.5 leading-relaxed">
      {paragraphs.map((p, i) => (
        <p key={i}>
          {p.split("\n").map((line, j, arr) => (
            <Fragment key={j}>
              {line.split(/(\*\*[^*]+\*\*|\[\d{1,2}\])/g).map((part, k) =>
                part.startsWith("**") && part.endsWith("**") ? (
                  <strong key={k} className="font-semibold">{part.slice(2, -2)}</strong>
                ) : /^\[\d{1,2}\]$/.test(part) ? (
                  <a
                    key={k}
                    href={`#src-${part.slice(1, -1)}`}
                    className="mx-0.5 inline-grid h-[18px] min-w-[18px] place-items-center rounded bg-brand px-1 align-text-top font-mono text-[10px] font-semibold text-brand-ink no-underline"
                  >
                    {part.slice(1, -1)}
                  </a>
                ) : (
                  <Fragment key={k}>{part}</Fragment>
                ),
              )}
              {j < arr.length - 1 && <br />}
            </Fragment>
          ))}
        </p>
      ))}
    </div>
  );
}
