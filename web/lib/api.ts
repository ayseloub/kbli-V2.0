export type Candidate = {
  kode: string;
  judul: string;
  ringkasan?: string | null;
  batasan: string[];
  score?: number | null;
};

export type Quota = { llm_remaining: number; llm_limit: number };

export type ChatKind =
  | "greeting" | "thanks" | "answer" | "clarify" | "refuse" | "explain" | "doc_answer" | "error";

export type Source = {
  n: number;
  document_id: string;
  title: string;
  where?: string | null;
  pages?: string | null;
  snippet: string;
};

export type ChatOut = {
  session_id: string;
  message_id?: number | null;
  kind: ChatKind;
  reply: string;
  candidates: Candidate[];
  options: Candidate[];
  sources?: Source[];
  can_explain: boolean;
  used_llm: boolean;
  quota?: Quota | null;
};

export type ChatIn = {
  session_id?: string | null;
  message?: string;
  action?: { type: "select" | "explain"; kode: string } | { type: "ask_ai" };
};

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "/chat";
export const HOME_URL = process.env.NEXT_PUBLIC_HOME_URL ?? "/";
const API = `${BASE_PATH}/api`;

async function call<T>(path: string, init: RequestInit): Promise<T> {
  let res: Response;
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: isForm ? init.headers : { "content-type": "application/json", ...(init.headers ?? {}) },
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("Server tidak bisa dihubungi. Pastikan backend sudah jalan.", 0);
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b?.detail).catch(() => null);
    const msg = typeof detail === "string" ? detail
      : res.status === 429 ? "Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi."
      : res.status >= 500 ? "Server sedang bermasalah. Coba lagi sebentar."
      : res.status === 422 ? "Periksa lagi isianmu (format email atau panjang teks)."
      : "Permintaan tidak valid.";
    throw new ApiError(msg, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  chat: (body: ChatIn) => call<ChatOut>("/chat", { method: "POST", body: JSON.stringify(body) }),
  feedback: (message_id: number, rating: 1 | -1) =>
    call<void>("/feedback", { method: "POST", body: JSON.stringify({ message_id, rating }) }),
};

// ------------------------------------------------------------------ auth
export type User = { id: string; email: string; name: string; role: "admin" | "editor" | "user" };
export type Me = { user: User | null; quota: Quota };

export const auth = {
  me: () => call<Me>("/auth/me", { method: "GET" }),
  login: (email: string, password: string, staffOnly = false) =>
    call<Me>("/auth/login", { method: "POST", body: JSON.stringify({ email, password, staff_only: staffOnly }) }),
  register: (name: string, email: string, password: string) =>
    call<Me>("/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) }),
  logout: () => call<void>("/auth/logout", { method: "POST" }),
};

// ------------------------------------------------------------------ admin
export type Stats = {
  days: number; searches: number; sessions: number; visitors: number; llm_calls: number;
  tokens_in: number; tokens_out: number; thumbs_up: number; thumbs_down: number;
  selections: number; users: number; by_kind: Record<string, number>;
  daily: { day: string; searches: number; llm_calls: number }[];
};
export type SearchRow = {
  id: number; created_at: string; query: string; confidence: string | null; reply_kind: string | null;
  rating: 1 | -1 | null; selected: string | null; top1: string | null; latency_ms: number | null;
  session_id: string; results: { kode: string; judul: string; score: number }[] | null;
};
export type SearchFilter = "all" | "refuse" | "clarify" | "negative" | "not_top1" | "no_select";
export type AdminUser = User & { is_active: boolean; created_at: string };
export type Page<T> = { total: number; items: T[] };

const qs = (p: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => [k, String(v)])).toString();

export const admin = {
  stats: (days: number) => call<Stats>(`/admin/stats?${qs({ days })}`, { method: "GET" }),
  searches: (p: { filter: SearchFilter; q?: string; limit: number; offset: number }) =>
    call<Page<SearchRow>>(`/admin/searches?${qs(p)}`, { method: "GET" }),
  topCodes: (days: number) =>
    call<{ kode: string; judul: string; n: number }[]>(`/admin/top-codes?${qs({ days })}`, { method: "GET" }),
  users: (offset = 0) => call<Page<AdminUser>>(`/admin/users?${qs({ limit: 50, offset })}`, { method: "GET" }),
  updateUser: (id: string, body: { role?: User["role"]; is_active?: boolean }) =>
    call<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
};

// --------------------------------------------------------------- dokumen
export type DocStatus = "queued" | "parsing" | "embedding" | "ready" | "failed";
export type DocItem = {
  id: string; title: string; description: string | null; original_filename: string; mime_type: string;
  page_count: number | null; status: DocStatus; status_message: string | null; is_active: boolean;
  effective_from: string | null; created_at: string; updated_at: string; uploaded_by_name: string | null;
  chunk_count: number; job_stage: string | null; job_progress: number | null; job_status: string | null;
};
export type DocChunk = {
  chunk_index: number; page_start: number | null; page_end: number | null;
  heading_path: string[] | null; content: string; token_count: number | null;
};

export const docs = {
  list: () => call<DocItem[]>("/admin/documents", { method: "GET" }),
  get: (id: string, offset = 0) =>
    call<DocItem & { chunks: DocChunk[] }>(`/admin/documents/${id}?${qs({ limit: 30, offset })}`, { method: "GET" }),
  upload: (file: File, title: string, description?: string, effective_from?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    if (description) fd.append("description", description);
    if (effective_from) fd.append("effective_from", effective_from);
    return call<DocItem>("/admin/documents", { method: "POST", body: fd });
  },
  update: (id: string, body: { title?: string; description?: string; is_active?: boolean }) =>
    call<DocItem>(`/admin/documents/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  reprocess: (id: string) => call<DocItem>(`/admin/documents/${id}/reprocess`, { method: "POST" }),
  remove: (id: string) => call<void>(`/admin/documents/${id}`, { method: "DELETE" }),
};

// -------------------------------------------------------------- data KBLI
export type KbliRow = {
  kode: string; judul: string; kata_kunci: string[]; sektor: string[]; uraian_source: string | null; uraian: string;
};
export type KbliScope = { kode: string; sektor: string; ruang_lingkup: string };
export type KbliFull = Omit<KbliRow, "uraian"> & { uraian: string | null; scopes: KbliScope[]; unchanged?: boolean };
export type KbliVersion = {
  id: string; note: string; source: string; created_at: string | null; created_by: string | null;
  stats: Record<string, number>; active: boolean;
};
export type ImportPreview = {
  token: string; filename: string; total: number; errors: string[];
  added: { kode: string; judul: string }[]; added_count: number;
  removed: { kode: string; judul: string }[]; removed_count: number;
  changed: { kode: string; judul: string; fields: string[] }[]; changed_count: number;
  unchanged_count: number; reembed_count: number;
};
export type ImportJob = {
  status: "idle" | "running" | "succeeded" | "failed"; progress?: number; message?: string;
  filename?: string; version?: string;
};

export const kbli = {
  list: (q: string, offset: number) =>
    call<Page<KbliRow>>(`/admin/kbli?${qs({ q, limit: 25, offset })}`, { method: "GET" }),
  get: (kode: string) => call<KbliFull>(`/admin/kbli/${kode}`, { method: "GET" }),
  save: (kode: string, body: { judul?: string; uraian?: string; kata_kunci?: string[] }) =>
    call<KbliFull>(`/admin/kbli/${kode}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (kode: string) => call<void>(`/admin/kbli/${kode}`, { method: "DELETE" }),
  exportUrl: `${API}/admin/kbli-export`,
  preview: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return call<ImportPreview>("/admin/kbli-import", { method: "POST", body: fd });
  },
  apply: (token: string) => call<ImportJob>(`/admin/kbli-import/${token}/apply`, { method: "POST" }),
  job: () => call<ImportJob>("/admin/kbli-job", { method: "GET" }),
  versions: () => call<KbliVersion[]>("/admin/kbli-versions", { method: "GET" }),
  activate: (id: string) => call<KbliVersion[]>(`/admin/kbli-versions/${id}/activate`, { method: "POST" }),
};
