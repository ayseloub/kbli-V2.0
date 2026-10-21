"""Rate limit & kuota LLM berbasis Redis (fixed window).

  rl:{identitas}:{menit}      -> semua pesan, anti spam
  llm:{identitas}:{tanggal}   -> jatah jawaban LLM per user per hari
  llm:global:{tanggal}        -> budget LLM seluruh aplikasi per hari

Kuota LLM hanya dipotong saat LLM benar-benar dipanggil.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from kbli.config import Settings


@dataclass
class Identity:
    key: str            # "u:<user_id>" atau "a:<anon_id>:<ip>"
    logged_in: bool
    user_id: str | None = None
    role: str | None = None


class Limiter:
    def __init__(self, redis, settings: Settings):
        self.r = redis
        self.s = settings

    @staticmethod
    def _day() -> str:
        return time.strftime("%Y%m%d", time.gmtime(time.time() + 7 * 3600))  # reset 00.00 WIB

    def check_rate(self, ident: Identity) -> bool:
        key = f"rl:{ident.key}:{int(time.time() // 60)}"
        n = self.r.incr(key)
        if n == 1:
            self.r.expire(key, 90)
        return n <= self.s.rate_limit_per_minute

    def llm_limit(self, ident: Identity) -> int:
        return self.s.llm_quota_user_per_day if ident.logged_in else self.s.llm_quota_anon_per_day

    def llm_remaining(self, ident: Identity) -> int:
        used = int(self.r.get(f"llm:{ident.key}:{self._day()}") or 0)
        return max(0, self.llm_limit(ident) - used)

    def try_consume_llm(self, ident: Identity) -> bool:
        """Potong 1 kuota user + global secara atomik. False kalau salah satunya habis."""
        day = self._day()
        ukey, gkey = f"llm:{ident.key}:{day}", f"llm:global:{day}"
        pipe = self.r.pipeline()
        pipe.incr(ukey)
        pipe.incr(gkey)
        pipe.expire(ukey, 2 * 86400)
        pipe.expire(gkey, 2 * 86400)
        u, g, *_ = pipe.execute()
        if u > self.llm_limit(ident) or g > self.s.llm_global_per_day:
            self.r.decr(ukey)
            self.r.decr(gkey)
            return False
        return True

    def refund_llm(self, ident: Identity) -> None:
        """Kembalikan kuota kalau panggilan LLM gagal (timeout/error provider)."""
        day = self._day()
        self.r.decr(f"llm:{ident.key}:{day}")
        self.r.decr(f"llm:global:{day}")
