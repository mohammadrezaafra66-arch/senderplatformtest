import type { NextConfig } from "next";

const apiProxyTarget = process.env.MMP_API_PROXY_TARGET ?? "http://mmp-backend:8900";

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
