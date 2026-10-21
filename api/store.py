"""Penyimpanan user, sesi chat, pesan, log retrieval, feedback, dan query admin.

PgStore     : produksi (tabel di db/schema.sql)
MemoryStore : development/test tanpa database (query admin dibuat sederhana)
"""
from __future__ import annotations

import itertools
import json
import uuid
from datetime import datetime, timezone
from typing import Protocol

SEARCH_FILTERS = ("all", "refuse", "clarify", "negative", "not_top1", "no_select")


class Store(Protocol):
    # users
    def create_user(self, email: str, name: str, password_hash: str, role: str = "user") -> dict: ...
    def get_user(self, user_id: str) -> dict | None: ...
    def get_user_by_email(self, email: str) -> dict | None: ...
    # chat
    def ensure_session(self, session_id: str | None, anon_id: str, user_id: str | None = None) -> str: ...
    def add_message(self, session_id: str, role: str, content: str | None, **meta) -> int: ...
    def log_retrieval(self, message_id: int, source: str, query: str, config: dict,
                      results: list[dict], latency_ms: int) -> None: ...
    def last_user_message(self, session_id: str) -> str | None: ...
    def add_feedback(self, message_id: int, rating: int | None, selected_kode: str | None,
                     comment: str | None) -> None: ...
    # admin
    def admin_stats(self, days: int) -> dict: ...
    def admin_searches(self, filter: str, limit: int, offset: int, q: str | None) -> dict: ...
    def admin_top_codes(self, days: int, limit: int) -> list[dict]: ...
    def admin_users(self, limit: int, offset: int) -> dict: ...
    def admin_set_user(self, user_id: str, role: str | None, is_active: bool | None) -> dict | None: ...


def _user_public(u: dict) -> dict:
    return {k: u[k] for k in ("id", "email", "name", "role", "is_active", "created_at") if k in u}


# ======================================================================= memory
class MemoryStore:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.retrievals: list[dict] = []
        self.feedback: list[dict] = []
        self._ids = itertools.count(1)

    # users
    def create_user(self, email, name, password_hash, role="user"):
        email = email.lower()
        if self.get_user_by_email(email):
            raise ValueError("email sudah terdaftar")
        u = {"id": str(uuid.uuid4()), "email": email, "name": name, "password_hash": password_hash,
             "role": role, "is_active": True, "created_at": datetime.now(timezone.utc)}
        self.users[u["id"]] = u
        return u

    def get_user(self, user_id):
        return self.users.get(user_id)

    def get_user_by_email(self, email):
        return next((u for u in self.users.values() if u["email"] == email.lower()), None)

    # chat
    def ensure_session(self, session_id, anon_id, user_id=None):
        s = self.sessions.get(session_id or "")
        if s and ((user_id and s["user_id"] == user_id) or (not user_id and s["anon_id"] == anon_id)):
            return session_id
        sid = str(uuid.uuid4())
        self.sessions[sid] = {"anon_id": anon_id, "user_id": user_id}
        return sid

    def add_message(self, session_id, role, content, **meta):
        mid = next(self._ids)
        self.messages.append({"id": mid, "session_id": session_id, "role": role, "content": content, **meta})
        return mid

    def log_retrieval(self, message_id, source, query, config, results, latency_ms):
        self.retrievals.append(dict(message_id=message_id, source=source, query=query,
                                    config=config, results=results, latency_ms=latency_ms))

    def last_user_message(self, session_id):
        for m in reversed(self.messages):
            if m["session_id"] == session_id and m["role"] == "user" and m["content"]:
                return m["content"]
        return None

    def add_feedback(self, message_id, rating, selected_kode, comment):
        self.feedback.append(dict(message_id=message_id, rating=rating,
                                  selected_kode=selected_kode, comment=comment))

    # admin (versi sederhana untuk dev tanpa DB)
    def admin_stats(self, days):
        return {"days": days, "searches": len(self.retrievals), "sessions": len(self.sessions),
                "visitors": len({s["anon_id"] or s["user_id"] for s in self.sessions.values()}),
                "by_kind": {}, "llm_calls": 0, "tokens_in": 0, "tokens_out": 0,
                "thumbs_up": sum(f["rating"] == 1 for f in self.feedback),
                "thumbs_down": sum(f["rating"] == -1 for f in self.feedback),
                "selections": sum(bool(f["selected_kode"]) for f in self.feedback),
                "users": len(self.users), "daily": []}

    def admin_searches(self, filter, limit, offset, q):
        rows = [{"id": i + 1, "query": r["query"], "confidence": r["config"].get("confidence"),
                 "results": r["results"][:5]} for i, r in enumerate(self.retrievals)]
        return {"total": len(rows), "items": list(reversed(rows))[offset:offset + limit]}

    def admin_top_codes(self, days, limit):
        return []

    def admin_users(self, limit, offset):
        us = sorted(self.users.values(), key=lambda u: u["created_at"], reverse=True)
        return {"total": len(us), "items": [_user_public(u) for u in us[offset:offset + limit]]}

    def admin_set_user(self, user_id, role, is_active):
        u = self.users.get(user_id)
        if not u:
            return None
        if role is not None:
            u["role"] = role
        if is_active is not None:
            u["is_active"] = is_active
        return _user_public(u)


# ===================================================================== postgres
# Satu "pencarian" = satu baris retrieval_logs (message_id = pesan user).
# Balasan bot = pesan asisten pertama sesudahnya; pilihan user = aksi 'select' pertama
# sesudahnya sebelum user mengetik pesan baru.
_SEARCH_CTE = """
WITH s AS (
  SELECT r.id, r.created_at, r.query_text AS query, r.config->>'confidence' AS confidence,
         r.results, r.latency_ms, m.session_id,
         (r.results->0->>'kode') AS top1,
         a.tool_payload->>'kind' AS reply_kind,
         f.rating,
         sel.kode AS selected
  FROM retrieval_logs r
  JOIN chat_messages m ON m.id = r.message_id
  LEFT JOIN LATERAL (
    SELECT id, tool_payload FROM chat_messages
    WHERE session_id = m.session_id AND id > m.id AND role = 'assistant'
    ORDER BY id LIMIT 1) a ON TRUE
  LEFT JOIN message_feedback f ON f.message_id = a.id
  LEFT JOIN LATERAL (
    SELECT x.tool_payload->>'kode' AS kode FROM chat_messages x
    WHERE x.session_id = m.session_id AND x.id > m.id AND x.tool_name = 'select'
      AND NOT EXISTS (SELECT 1 FROM chat_messages u
                      WHERE u.session_id = m.session_id AND u.role = 'user'
                        AND u.content IS NOT NULL AND u.id > m.id AND u.id < x.id)
    ORDER BY x.id LIMIT 1) sel ON TRUE
  WHERE r.source = 'kbli'
)
"""

_FILTER_SQL = {
    "all": "TRUE",
    "refuse": "reply_kind = 'refuse'",
    "clarify": "reply_kind = 'clarify'",
    "negative": "rating = -1",
    "not_top1": "selected IS NOT NULL AND selected <> top1",
    "no_select": "selected IS NULL AND reply_kind IN ('answer', 'clarify')",
}


class PgStore:
    def __init__(self, database_url: str):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(database_url, min_size=1, max_size=10, open=True)
        self._dict_row = dict_row

    def close(self):
        self.pool.close()

    def _one(self, sql, params=()):
        with self.pool.connection() as c:
            return c.cursor(row_factory=self._dict_row).execute(sql, params).fetchone()

    def _all(self, sql, params=()):
        with self.pool.connection() as c:
            return c.cursor(row_factory=self._dict_row).execute(sql, params).fetchall()

    # users
    def create_user(self, email, name, password_hash, role="user"):
        from psycopg.errors import UniqueViolation
        try:
            u = self._one("""INSERT INTO users (email, name, password_hash, role) VALUES (%s, %s, %s, %s)
                             RETURNING *""", (email.lower(), name, password_hash, role))
        except UniqueViolation as e:
            raise ValueError("email sudah terdaftar") from e
        return {**u, "id": str(u["id"])}

    def get_user(self, user_id):
        try:
            uuid.UUID(str(user_id))
        except ValueError:
            return None
        u = self._one("SELECT * FROM users WHERE id = %s", (user_id,))
        return {**u, "id": str(u["id"])} if u else None

    def get_user_by_email(self, email):
        u = self._one("SELECT * FROM users WHERE email = %s", (email.lower(),))
        return {**u, "id": str(u["id"])} if u else None

    # chat
    def ensure_session(self, session_id, anon_id, user_id=None):
        with self.pool.connection() as c:
            if session_id:
                try:
                    uuid.UUID(session_id)
                except ValueError:
                    session_id = None
            if session_id:
                owner = ("user_id = %s", user_id) if user_id else ("anon_id = %s AND user_id IS NULL", anon_id)
                row = c.execute(f"SELECT id FROM chat_sessions WHERE id = %s AND {owner[0]}",
                                (session_id, owner[1])).fetchone()
                if row:
                    c.execute("UPDATE chat_sessions SET updated_at = now() WHERE id = %s", (session_id,))
                    return str(row[0])
            row = c.execute("INSERT INTO chat_sessions (anon_id, user_id) VALUES (%s, %s) RETURNING id",
                            (anon_id, user_id)).fetchone()
            return str(row[0])

    def add_message(self, session_id, role, content, **meta):
        with self.pool.connection() as c:
            row = c.execute(
                """INSERT INTO chat_messages (session_id, role, content, tool_name, tool_payload,
                                              llm_provider, llm_model, tokens_in, tokens_out, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (session_id, role, content, meta.get("tool_name"),
                 json.dumps(meta["tool_payload"]) if meta.get("tool_payload") is not None else None,
                 meta.get("llm_provider"), meta.get("llm_model"), meta.get("tokens_in"),
                 meta.get("tokens_out"), meta.get("latency_ms"))).fetchone()
            return int(row[0])

    def log_retrieval(self, message_id, source, query, config, results, latency_ms):
        with self.pool.connection() as c:
            c.execute("""INSERT INTO retrieval_logs (message_id, source, query_text, config, results, latency_ms)
                         VALUES (%s, %s, %s, %s, %s, %s)""",
                      (message_id, source, query, json.dumps(config), json.dumps(results), latency_ms))

    def last_user_message(self, session_id):
        with self.pool.connection() as c:
            row = c.execute("""SELECT content FROM chat_messages
                               WHERE session_id = %s AND role = 'user' AND content IS NOT NULL
                               ORDER BY id DESC LIMIT 1""", (session_id,)).fetchone()
            return row[0] if row else None

    def add_feedback(self, message_id, rating, selected_kode, comment):
        with self.pool.connection() as c:
            c.execute("""INSERT INTO message_feedback (message_id, rating, selected_kode, comment)
                         VALUES (%s, %s, %s, %s)
                         ON CONFLICT (message_id) DO UPDATE
                         SET rating = COALESCE(EXCLUDED.rating, message_feedback.rating),
                             selected_kode = COALESCE(EXCLUDED.selected_kode, message_feedback.selected_kode),
                             comment = COALESCE(EXCLUDED.comment, message_feedback.comment)""",
                      (message_id, rating, selected_kode, comment))

    # admin
    def admin_stats(self, days):
        since = "now() - make_interval(days => %(d)s)"
        p = {"d": days}
        totals = self._one(f"""
            SELECT
              (SELECT count(*) FROM retrieval_logs WHERE source = 'kbli' AND created_at >= {since}) AS searches,
              (SELECT count(*) FROM chat_sessions WHERE created_at >= {since}) AS sessions,
              (SELECT count(DISTINCT coalesce(user_id::text, anon_id)) FROM chat_sessions
                 WHERE created_at >= {since}) AS visitors,
              (SELECT count(*) FROM chat_messages WHERE llm_model IS NOT NULL AND created_at >= {since}) AS llm_calls,
              (SELECT coalesce(sum(tokens_in), 0) FROM chat_messages WHERE created_at >= {since}) AS tokens_in,
              (SELECT coalesce(sum(tokens_out), 0) FROM chat_messages WHERE created_at >= {since}) AS tokens_out,
              (SELECT count(*) FROM message_feedback WHERE rating = 1 AND created_at >= {since}) AS thumbs_up,
              (SELECT count(*) FROM message_feedback WHERE rating = -1 AND created_at >= {since}) AS thumbs_down,
              (SELECT count(*) FROM message_feedback WHERE selected_kode IS NOT NULL
                 AND created_at >= {since}) AS selections,
              (SELECT count(*) FROM users) AS users""", p)
        kinds = self._all(f"""SELECT tool_payload->>'kind' AS kind, count(*) AS n FROM chat_messages
                              WHERE role = 'assistant' AND tool_payload ? 'kind' AND created_at >= {since}
                              GROUP BY 1""", p)
        daily = self._all(f"""
            WITH d AS (SELECT generate_series((now() AT TIME ZONE 'Asia/Jakarta')::date - (%(d)s - 1),
                                              (now() AT TIME ZONE 'Asia/Jakarta')::date, '1 day')::date AS day)
            SELECT d.day::text AS day,
              (SELECT count(*) FROM retrieval_logs r WHERE r.source = 'kbli'
                 AND (r.created_at AT TIME ZONE 'Asia/Jakarta')::date = d.day) AS searches,
              (SELECT count(*) FROM chat_messages m WHERE m.llm_model IS NOT NULL
                 AND (m.created_at AT TIME ZONE 'Asia/Jakarta')::date = d.day) AS llm_calls
            FROM d ORDER BY d.day""", p)
        return {"days": days, **totals, "by_kind": {k["kind"]: k["n"] for k in kinds}, "daily": daily}

    def admin_searches(self, filter, limit, offset, q):
        where = _FILTER_SQL.get(filter, "TRUE")
        params: dict = {"limit": limit, "offset": offset}
        if q:
            where += " AND query ILIKE %(q)s"
            params["q"] = f"%{q}%"
        total = self._one(f"{_SEARCH_CTE} SELECT count(*) AS n FROM s WHERE {where}", params)["n"]
        rows = self._all(f"""{_SEARCH_CTE}
            SELECT id, created_at, query, confidence, reply_kind, rating, selected, top1, latency_ms,
                   session_id::text AS session_id,
                   (SELECT jsonb_agg(jsonb_build_object('kode', e->>'kode', 'judul', e->>'judul',
                                                        'score', (e->>'score')::float))
                    FROM jsonb_array_elements(results) WITH ORDINALITY AS t(e, i) WHERE i <= 5) AS results
            FROM s WHERE {where} ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s""", params)
        return {"total": total, "items": rows}

    def admin_top_codes(self, days, limit):
        return self._all("""
            SELECT f.selected_kode AS kode, k.judul, count(*) AS n
            FROM message_feedback f JOIN kbli_codes k ON k.kode = f.selected_kode
            WHERE f.created_at >= now() - make_interval(days => %s)
            GROUP BY 1, 2 ORDER BY n DESC, kode LIMIT %s""", (days, limit))

    def admin_users(self, limit, offset):
        total = self._one("SELECT count(*) AS n FROM users")["n"]
        rows = self._all("""SELECT id::text AS id, email, name, role::text AS role, is_active, created_at
                            FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s""", (limit, offset))
        return {"total": total, "items": rows}

    def admin_set_user(self, user_id, role, is_active):
        u = self._one("""UPDATE users SET role = coalesce(%s::user_role, role),
                                          is_active = coalesce(%s, is_active), updated_at = now()
                         WHERE id = %s
                         RETURNING id::text AS id, email, name, role::text AS role, is_active, created_at""",
                      (role, is_active, user_id))
        return u
