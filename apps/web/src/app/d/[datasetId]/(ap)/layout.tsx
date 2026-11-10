import { Shell } from "@/components/shell";

// AP workspace (spec 0063): Inbox, Cases, Vendors, and every case page under them.
export default async function ApLayout({ children, params }: { children: React.ReactNode; params: Promise<{ datasetId: string }> }) {
  return <Shell app="ap" datasetId={(await params).datasetId}>{children}</Shell>;
}
