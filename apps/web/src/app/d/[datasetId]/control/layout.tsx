import { Shell } from "@/components/shell";

// Control (spec 0063): Overview, Trust Ledger, Evidence, Assurance and the Settings pages.
export default async function ControlLayout({ children, params }: { children: React.ReactNode; params: Promise<{ datasetId: string }> }) {
  return <Shell app="control" datasetId={(await params).datasetId}>{children}</Shell>;
}
