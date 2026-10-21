"""Leksikon istilah awam -> istilah resmi KBLI, untuk ekspansi query BM25.

Hanya dipakai di sisi QUERY (one-way expansion): istilah resmi ditambahkan,
kata asli user tetap dipertahankan. Isi daftar ini dari log pertanyaan user
yang gagal (retrieval_logs) — itu sumber terbaik.
"""
import re

QUERY_SYNONYMS: dict[str, str] = {
    "bengkel": "reparasi perawatan",
    "servis": "reparasi perawatan",
    "service": "reparasi perawatan",
    "pabrik": "industri",
    "bikin": "pembuatan industri",
    "produksi": "industri pembuatan",
    "jualan": "perdagangan eceran",
    "jual": "perdagangan eceran",
    "toko": "perdagangan eceran",
    "grosir": "perdagangan besar",
    "distributor": "perdagangan besar",
    "online": "internet elektronik daring",
    "olshop": "perdagangan eceran internet",
    "warung": "rumah makan",
    "kafe": "rumah minum kafe",
    "cafe": "rumah minum kafe",
    "ternak": "peternakan budidaya",
    "nyetak": "percetakan",
    "konveksi": "pakaian jadi konveksi",
    "jahit": "penjahitan",
    "aplikasi": "piranti lunak pemrograman",
    "website": "portal web pemrograman",
    "medsos": "media sosial internet",
    "kos": "akomodasi pondok penginapan",
    "kost": "akomodasi pondok penginapan",
    "kosan": "akomodasi pondok penginapan",
    "rental": "penyewaan sewa",
    "laundry": "penatu pencucian dry cleaning",
    "londri": "penatu pencucian dry cleaning",
    "kiloan": "",
    "salon": "pangkas rambut kecantikan",
    "barbershop": "pangkas rambut",
    "travel": "biro perjalanan wisata",
    "ekspedisi": "kurir pengiriman barang",
    "ojek": "angkutan orang",
    "motor": "sepeda motor",
    "mlm": "multi level marketing penjualan langsung",
    "reseller": "perdagangan eceran agen",
    "dropship": "perdagangan eceran internet",
    "dropshipper": "perdagangan eceran internet",
    "keliling": "menjajakan berkeliling",
    "door": "rumah ke rumah menjajakan",
    "foto": "fotografi pemotretan",
    "fotografer": "fotografi pemotretan",
    "rancangan": "desain perancangan",
    "rancang": "desain perancangan",
    "software": "piranti lunak perangkat lunak",
    "podcast": "rekaman suara audio",
}

_RE = re.compile(r"\b(" + "|".join(map(re.escape, QUERY_SYNONYMS)) + r")\b", re.IGNORECASE)


def expand_query(text: str) -> str:
    extra = [QUERY_SYNONYMS[m.lower()] for m in _RE.findall(text) if QUERY_SYNONYMS[m.lower()]]
    return f"{text} {' '.join(extra)}" if extra else text
