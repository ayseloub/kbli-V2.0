"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "./Icons";

const KEY = "kbli_theme";

function systemDark() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Tombol terang/gelap. Pilihan disimpan per browser; tanpa pilihan ikut tema sistem. */
export default function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState<boolean | null>(null);

  useEffect(() => {
    const attr = document.documentElement.dataset.theme;
    setDark(attr ? attr === "dark" : systemDark());
  }, []);

  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try { localStorage.setItem(KEY, next ? "dark" : "light"); } catch { /* mode privat */ }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Ganti ke mode terang" : "Ganti ke mode gelap"}
      title={dark ? "Mode terang" : "Mode gelap"}
      className={`rounded-lg p-2 hover:bg-white/10 ${className}`}
    >
      {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
    </button>
  );
}

/** Dijalankan di <head> sebelum halaman tampil, supaya tidak "berkedip" ke tema yang salah. */
export const themeInitScript = `try{var t=localStorage.getItem("${KEY}");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;
