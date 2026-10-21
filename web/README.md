# Frontend KBLI (Next.js)

Tampilan chat untuk API KBLI v2. Semua request `/api/*` diteruskan ke FastAPI
(lihat `next.config.ts`), jadi tidak perlu urusan CORS.

## Jalankan (dev)

```bash
# terminal 1 — backend (dari folder kbli-v2)
docker compose up -d db redis
source .venv/bin/activate && uvicorn api.main:app --reload

# terminal 2 — frontend
cd web
cp .env.example .env.local     # sekali saja
npm install                    # sekali saja
npm run dev                    # buka http://localhost:3000
```

## Struktur

- `components/Chat.tsx` — state percakapan, kirim pesan, pilih kode, jelaskan AI, 👍/👎
- `components/CodeOptions.tsx` — kartu 5 kode terdekat (#1 diberi label "Paling cocok" kalau sistem yakin)
- `components/RichText.tsx` — render **tebal** dan paragraf dari balasan template
- `lib/api.ts` — tipe respons API + fetch
