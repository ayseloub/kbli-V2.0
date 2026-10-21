"""Buat (atau naikkan) akun admin dari terminal.

  python -m api.create_admin --email kamu@email.com --name "Aza"

Password ditanyakan secara tersembunyi. Kalau email sudah terdaftar,
akun itu dinaikkan jadi admin (password tidak diubah).
"""
from __future__ import annotations

import argparse
import getpass
import sys

from kbli.config import get_settings

from .auth import hash_password, validate_password
from .store import PgStore


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="Admin")
    ap.add_argument("--role", default="admin", choices=["admin", "editor"])
    a = ap.parse_args(argv)

    store = PgStore(get_settings().database_url)
    try:
        user = store.get_user_by_email(a.email)
        if user:
            store.admin_set_user(user["id"], a.role, True)
            print(f"OK: {a.email} sekarang {a.role}.")
            return
        pw = getpass.getpass("Password baru: ")
        if err := validate_password(pw):
            sys.exit(err)
        if pw != getpass.getpass("Ulangi password: "):
            sys.exit("Password tidak sama.")
        store.create_user(a.email, a.name, hash_password(pw), role=a.role)
        print(f"OK: akun {a.role} {a.email} dibuat.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
