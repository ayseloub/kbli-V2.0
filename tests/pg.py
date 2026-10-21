import os


def pg_test_url():
    """Hanya jalan di database yang namanya mengandung 'test' — test ini MENGHAPUS data."""
    url = os.environ.get("TEST_DATABASE_URL")
    return url if url and "test" in url.rsplit("/", 1)[-1] else None


