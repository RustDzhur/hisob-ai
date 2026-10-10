import type { NextConfig } from "next";

// Браузер ходит только на свой origin; /api/* проксируется на backend.
// Так cookie сессии остаётся first-party, а CORS не нужен.
const backend = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8300";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
