import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Local development only: lets the dev server's client code load when the dashboard is
  // opened at 127.0.0.1 (the address scripts/dev/stack.py prints) as well as localhost.
  allowedDevOrigins: ["127.0.0.1"],
};

export default config;
