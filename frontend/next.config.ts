import type { NextConfig } from "next";

// Браузер ходит только на свой origin; /api/* проксируется на backend.
// Так cookie сессии остаётся first-party, а CORS не нужен.
const backend = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8300";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    // CSP: Next.js вставляет inline-скрипты начальной загрузки, поэтому script-src содержит 'unsafe-inline'
    // (ограничение: строгий CSP с nonce — отдельная задача). Остальное закрыто: свои ресурсы, без фреймов чужих сайтов.
    const csp = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      "connect-src 'self'",
      "frame-src 'self'",
      "object-src 'self'",
      "frame-ancestors 'self'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; ");
    const common = [
      { key: "Content-Security-Policy", value: csp },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "SAMEORIGIN" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
    ];
    // HSTS — только за TLS (иначе браузер запомнит запрет http на стенде без сертификата).
    if (process.env.ENABLE_HSTS === "1") common.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
    return [{ source: "/:path*", headers: common }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
