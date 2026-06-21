import type { NextConfig } from "next";

const apiProxyTarget = process.env.MMP_API_PROXY_TARGET ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${apiProxyTarget}/:path*`,
      },
    ];
  },
};

export default nextConfig;
