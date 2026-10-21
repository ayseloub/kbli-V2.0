"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, auth } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { Bot } from "../Icons";
import ThemeToggle from "../ThemeToggle";
import Documents from "./Documents";
import KbliData from "./KbliData";
import Overview from "./Overview";
import SearchLog from "./SearchLog";
import Users from "./Users";

type Tab = "ringkasan" | "pencarian" | "kbli" | "dokumen" | "pengguna";

export default function AdminApp() {
  const { me, loading, refresh } = useMe();
  const [tab, setTab] = useState<Tab>("ringkasan");

  if (loading) return <Center>Memuat…</Center>;
  const user = me?.user;
  if (!user || user.role === "user") return <AdminLogin onDone={refresh} wrongAccount={user?.email} />;

  const tabs: { id: Tab; label: string; adminOnly?: boolean }[] = [
    { id: "ringkasan", label: "Ringkasan" },
    { id: "pencarian", label: "Log pencarian" },
    { id: "kbli", label: "Data KBLI" },
    { id: "dokumen", label: "Dokumen" },
    { id: "pengguna", label: "Pengguna", adminOnly: true },
  ];

  const logout = async () => {
    await auth.logout().catch(() => {});
    refresh();
  };

  return (
    <div className="min-h-dvh bg-paper">
      <header className="sticky top-0 z-10 border-b border-header-line bg-header text-white shadow-sm">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 pt-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white text-[#303b91]">
            <Bot className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-[15px] font-semibold leading-tight">Asisten Virtual BKPM · Admin</h1>
            <p className="truncate text-xs text-white/80">{user.name} · {user.role === "admin" ? "Admin" : "Editor"}</p>
          </div>
          <ThemeToggle />
          <Link href="/" className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-white/90 hover:bg-white/10">
            Buka chat
          </Link>
          <button type="button" onClick={logout} className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-white/90 hover:bg-white/10">
            Keluar
          </button>
        </div>
        <nav className="mx-auto mt-2 flex max-w-6xl gap-1 overflow-x-auto px-4" aria-label="Menu admin">
          {tabs.filter((t) => !t.adminOnly || user.role === "admin").map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              aria-current={tab === t.id ? "page" : undefined}
              className={`whitespace-nowrap border-b-[3px] px-3 pb-2.5 pt-1 text-sm font-medium transition ${
                tab === t.id ? "border-go text-white" : "border-transparent text-white/70 hover:text-white"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        {tab === "ringkasan" && <Overview />}
        {tab === "pencarian" && <SearchLog />}
        {tab === "kbli" && <KbliData isAdmin={user.role === "admin"} />}
        {tab === "dokumen" && <Documents canDelete={user.role === "admin"} />}
        {tab === "pengguna" && <Users meId={user.id} />}
      </main>
    </div>
  );
}

function AdminLogin({ onDone, wrongAccount }: { onDone: () => void; wrongAccount?: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(
    wrongAccount ? `Akun ${wrongAccount} bukan akun admin.` : null,
  );
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.login(email.trim(), password, true);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Gagal masuk.");
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh flex-col bg-paper">
      <div className="h-2 bg-header" />
      <div className="grid flex-1 place-items-center px-4 py-10">
        <div className="w-full max-w-sm rounded-2xl border border-line bg-surface p-7 shadow-soft">
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-brand text-brand-ink">
              <Bot className="h-6 w-6" />
            </div>
            <div>
              <h1 className="font-semibold leading-tight">Masuk Admin</h1>
              <p className="text-xs text-muted">Asisten Virtual BKPM</p>
            </div>
          </div>
          <form onSubmit={submit} className="space-y-3.5">
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Email</span>
              <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-xl border border-line bg-bubble px-3.5 py-2.5 outline-none focus:border-brand focus:bg-surface" />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Password</span>
              <input type="password" required autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-xl border border-line bg-bubble px-3.5 py-2.5 outline-none focus:border-brand focus:bg-surface" />
            </label>
            {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
            <button type="submit" disabled={busy}
              className="w-full rounded-xl bg-go py-2.5 font-semibold text-go-ink hover:brightness-95 disabled:opacity-50">
              {busy ? "Memproses…" : "Masuk"}
            </button>
          </form>
          <p className="mt-5 text-center text-xs text-muted">
            Akun admin dibuat oleh pengelola sistem. Lupa password? Hubungi admin lain.
          </p>
        </div>
      </div>
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="grid min-h-dvh place-items-center px-4 text-center text-muted"><div>{children}</div></div>;
}
