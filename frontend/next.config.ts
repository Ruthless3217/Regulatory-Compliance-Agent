import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
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
