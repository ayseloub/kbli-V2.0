from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://kbli:kbli@localhost:55432/kbli"
    store_backend: str = "postgres"        # 'postgres' | 'memory' (dev tanpa DB)
    redis_url: str = "redis://localhost:6379/0"

    embed_model: str = "denaya/indoSBERT-large"
    embed_dim: int = 256
    kbli_index_dir: str = "data/processed"      # data awal (hasil pipeline)
    kbli_versions_dir: str = "data/kbli_versions"  # versi hasil edit/impor dari dashboard
    kbli_keep_versions: int = 30                   # versi lama yang disimpan (±9 MB per versi)
    # Hasil eval/ablation (64 query skripsi, IndoSBERT-large): max/linear/0.6 -> P@1 0.922
    dense_strategy: str = "max"            # chunk | doc | max | mean
    fusion: str = "linear"                 # rrf | linear
    fusion_w_bm25: float = 0.6             # bobot BM25 di fusion (0..1)
    intent_rerank: bool = True             # turunkan kode yang jenisnya bertentangan dg niat query
    intent_penalty: float = 0.70

    # Knowledge base dokumen
    docs_enabled: bool = True
    doc_embed_model: str = "BAAI/bge-m3"   # multibahasa, cocok untuk dokumen panjang
    doc_embed_dim: int = 1024              # harus sama dengan VECTOR(n) document_chunks
    doc_top_k: int = 4                     # potongan yang ditampilkan / dikirim ke LLM
    doc_min_score: float = 0.45            # kemiripan cosine minimal agar dianggap relevan
    doc_worker_enabled: bool = True        # worker ingestion jalan di dalam proses API
    upload_dir: str = "data/uploads"
    max_upload_mb: int = 25

    # Auth
    jwt_secret: str = "dev-secret-ganti-di-produksi-minimal-32-karakter"   # WAJIB diganti di .env produksi
    jwt_ttl_hours: int = 24 * 7
    cookie_secure: bool = False            # True kalau sudah HTTPS
    allow_registration: bool = False       # publik tidak perlu akun; admin dibuat lewat CLI/dashboard
    trust_proxy_headers: bool = True       # ambil IP asli dari X-Forwarded-For (di belakang Nginx)
    trusted_proxy_hops: int = 1            # jumlah proxy tepercaya yang menambah X-Forwarded-For
                                           # (Nginx saja = 1; Cloudflare/LB -> Nginx = 2)

    llm_enabled: bool = True
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    llm_max_tokens: int = 600              # model reasoning butuh ruang untuk "berpikir" + jawaban
    llm_reasoning_effort: str = "low"      # low | medium | high; kosongkan untuk model non-reasoning
    llm_timeout_s: float = 20.0

    rate_limit_per_minute: int = 20
    llm_quota_anon_per_day: int = 10
    llm_quota_user_per_day: int = 20
    llm_global_per_day: int = 800
    max_input_chars: int = 1000
    chat_top_k: int = 5                    # jumlah pilihan kode yang selalu ditampilkan ke user
    max_concurrent_inference: int = 4

    # Ambang routing (disetel ulang dari data eval / log)
    out_of_scope_max_score: float = 0.51   # skor dense top-1 di bawah ini -> luar topik
    min_query_coverage: float = 0.40      # porsi kata query yang dikenal kosakata KBLI
    bm25_confident_rel_margin: float = 0.30
    confident_min_score: float = 0.675     # skor dense top-1 minimal untuk "yakin"
    confident_min_margin: float = 0.08     # selisih skor dense top-1 vs top-2
    confident_min_fused_margin: float = 0.10  # selisih skor fusion (linear, 0..1) top-1 vs top-2


@lru_cache
def get_settings() -> Settings:
    return Settings()
