import { notFound } from "next/navigation";
import { LiveCall } from "@/components/live-call";
import type { SelectionView } from "@/lib/api-types";
import { CoreError, core } from "@/lib/core";
import { liveCallView } from "@/lib/live-call";

// Live call (spec 0065): the phone demo's split view, full width for a screen share. The first view
// is rendered here; the client component then asks .../call/state while code is working.
export default async function LiveCallPage({ params }: { params: Promise<{ datasetId: string; caseId: string }> }) {
  const { datasetId, caseId } = await params;
  const [detail, selection] = await Promise.all([
    core.case(caseId).catch((e) => {
      if (e instanceof CoreError && (e.status === 404 || e.status === 422)) notFound();
      throw e;
    }),
    core.selection(caseId).catch((): SelectionView | null => null),
  ]);
  const base = `/d/${datasetId}/cases/${caseId}`;
  return <LiveCall initial={liveCallView(detail, selection)} stateUrl={`${base}/call/state`} caseHref={base} flowHref={`${base}/flow`} />;
}
