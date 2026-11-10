import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Local development only: lets the dev server's client code load when the dashboard is
  // opened at 127.0.0.1 (the address scripts/dev/stack.py prints) as well as localhost.
  allowedDevOrigins: ["127.0.0.1"],
  // Spec 0063: Overview, Trust Ledger and Evidence moved into the Control app. Old links (and the
  // guided demo's ?tour=N, which Next passes on) keep working.
  async redirects() {
    return [
      { source: "/d/:datasetId/overview", destination: "/d/:datasetId/control", permanent: false },
      { source: "/d/:datasetId/risk", destination: "/d/:datasetId/control/trust-ledger", permanent: false },
      { source: "/d/:datasetId/evidence", destination: "/d/:datasetId/control/evidence", permanent: false },
    ];
  },
};

export default config;
