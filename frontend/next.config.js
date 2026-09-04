/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000") + "/:path*" }];
  },
};
module.exports = nextConfig;
