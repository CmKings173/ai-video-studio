import type { NextConfig } from "next";

/**
 * AI Video Studio Frontend Next.js Configuration
 *
 * Deployment Strategy:
 * - Browser code uses relative `/api/v1/...` paths by default.
 * - `app/api/[...path]/route.ts` proxies those requests server-side to API_BACKEND_URL.
 * - This keeps Docker/on-prem browser bundles free of baked localhost/LAN backend URLs.
 */
const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
