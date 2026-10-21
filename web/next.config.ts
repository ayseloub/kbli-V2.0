import type { NextConfig } from "next";

// Aplikasi dipasang di bawah satu path (default /chat) supaya bisa berdampingan dengan
// website utama di domain yang sama:  domain/chat, domain/chat/admin, domain/chat/api/...
// Kalau dipasang di subdomain sendiri, set NEXT_PUBLIC_BASE_PATH= (kosong).
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/chat";
const API_URL = process.env.KBLI_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  basePath: basePath || undefined,
  experimental: {
    // Proxy Next memotong body request di 10 MB secara default; upload dokumen boleh sampai
    // MAX_UPLOAD_MB (25) di backend, jadi beri sedikit ruang lebih.
    middlewareClientMaxBodySize: "30mb",
  },
  async rewrites() {
    // basePath otomatis ditambahkan: /chat/api/* -> FastAPI /*
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
