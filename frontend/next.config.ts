import type { NextConfig } from "next";

// The browser calls /api/*; Next forwards it to FastAPI, so there's no CORS to configure.
// On Render, BACKEND_URL is the API's private-network host:port (render.yaml fromService), so
// add the scheme when it's missing. Rewrites are fixed at build time: rebuild after changing it.
const raw = process.env.BACKEND_URL || "http://localhost:8001";
const backend = /^https?:\/\//.test(raw) ? raw : `http://${raw}`;

const config: NextConfig = {
  devIndicators: false, // keeps the dev badge out of screen recordings
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default config;
