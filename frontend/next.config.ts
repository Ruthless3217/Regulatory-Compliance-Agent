import type { NextConfig } from "next";

// When deployed behind the shared platform nginx the app is served under a
// sub-path (e.g. /compliance). Set NEXT_PUBLIC_BASE_PATH=/compliance at build
// time so Next.js prefixes its assets (/compliance/_next/...) and routes.
// Left empty for standalone/local dev so the app stays at the root.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const config: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  async rewrites() {
    // Server-side rewrites run inside the container, so we need the docker
    // service hostname here, not "localhost". Browser-side requests use
    // NEXT_PUBLIC_API_BASE directly and bypass this rewrite.
    const apiBase =
      process.env.INTERNAL_API_BASE ||
      process.env.NEXT_PUBLIC_API_BASE ||
      "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${apiBase}/:path*` },
    ];
  },
};

export default config;
