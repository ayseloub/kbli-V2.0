"use client";

import { useCallback, useEffect, useState } from "react";
import { admin, ApiError, type AdminUser, type Page, type User } from "@/lib/api";
import { ErrorNote, fmt, fmtDate } from "./ui";

export default function Users({ meId }: { meId: string }) {
  const [page, setPage] = useState<Page<AdminUser> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try { setPage(await admin.users()); } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal memuat pengguna.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const update = async (u: AdminUser, body: { role?: User["role"]; is_active?: boolean }) => {
    try {
      const nu = await admin.updateUser(u.id, body);
      setPage((p) => p && { ...p, items: p.items.map((x) => (x.id === u.id ? nu : x)) });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Gagal menyimpan.");
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold">Pengguna</h2>
        <p className="text-xs text-muted">
          {page ? `${fmt(page.total)} akun terdaftar. ` : ""}
          Editor bisa melihat dashboard, admin bisa juga mengelola pengguna.
        </p>
      </div>
      {error && <ErrorNote message={error} onRetry={load} />}
      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-4 py-2.5 font-medium">Nama</th>
              <th className="px-4 py-2.5 font-medium">Email</th>
              <th className="px-4 py-2.5 font-medium">Daftar</th>
              <th className="px-4 py-2.5 font-medium">Peran</th>
              <th className="px-4 py-2.5 font-medium">Aktif</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {page?.items.map((u) => {
              const self = u.id === meId;
              return (
                <tr key={u.id} className={u.is_active ? "" : "opacity-55"}>
                  <td className="px-4 py-3 font-medium">{u.name}{self && <span className="ml-1.5 text-xs text-muted">(kamu)</span>}</td>
                  <td className="px-4 py-3 text-muted">{u.email}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-muted">{fmtDate(u.created_at)}</td>
                  <td className="px-4 py-3">
                    <select
                      value={u.role}
                      disabled={self}
                      aria-label={`Peran ${u.name}`}
                      onChange={(e) => update(u, { role: e.target.value as User["role"] })}
                      className="rounded-lg border border-line bg-paper px-2 py-1 text-sm disabled:opacity-60"
                    >
                      <option value="user">User</option>
                      <option value="editor">Editor</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={u.is_active}
                      disabled={self}
                      aria-label={`Aktifkan ${u.name}`}
                      onChange={(e) => update(u, { is_active: e.target.checked })}
                      className="h-4 w-4 accent-[var(--brand)]"
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!page && !error && <p className="py-10 text-center text-sm text-muted">Memuat…</p>}
      </div>
    </div>
  );
}
