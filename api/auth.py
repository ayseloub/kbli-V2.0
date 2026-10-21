"""Autentikasi: hash password (scrypt, stdlib) + JWT di cookie httpOnly.

Token disimpan di cookie `kbli_token` (httpOnly, SameSite=Lax), bukan di localStorage,
supaya tidak bisa dibaca JavaScript. Frontend memanggil API lewat proxy Next.js
(origin yang sama), jadi cookie ikut terkirim otomatis.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import jwt

from kbli.config import Settings

TOKEN_COOKIE = "kbli_token"
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, role: str, settings: Settings) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "role": role, "iat": now, "exp": now + settings.jwt_ttl_hours * 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, settings: Settings) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def validate_password(pw: str) -> str | None:
    """Kembalikan pesan error kalau password terlalu lemah."""
    if len(pw) < 8:
        return "Password minimal 8 karakter."
    if pw.isdigit() or pw.isalpha():
        return "Password harus kombinasi huruf dan angka."
    return None
