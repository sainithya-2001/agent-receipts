import type { NextConfig } from "next";

// The browser calls /api/*; Next forwards it to FastAPI, so there's no CORS to configure.
const backend = process.env.BACKEND_URL ?? "http://localhost:8001";

const config: NextConfig = {
  devIndicators: false, // keeps the dev badge out of screen recordings
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default config;
