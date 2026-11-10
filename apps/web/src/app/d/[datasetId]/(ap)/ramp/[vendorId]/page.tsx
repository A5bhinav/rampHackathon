import Link from "next/link";
import { notFound } from "next/navigation";
import { AutoRefresh } from "@/components/auto-refresh";
import { StateBadge } from "@/components/badges";
import { BankChangeForm } from "@/components/bank-change-form";
import { CaseRail } from "@/components/flow";
import { CoreError, core } from "@/lib/core";
import { canWatchCall } from "@/lib/live-call";
import { formatAccount, pickedUp, watchFor } from "@/lib/ramp-portal";
import { REFRESH_STATES, stations } from "@/lib/stations";

// One vendor's payment details in Ramp (mock) (spec 0077). After a save, the page watches: it
// refreshes itself until Dialback has detected the change and opened (or reopened) the case, then
// follows that case's seven stations as code works through them.
export default async function RampVendor({ params, searchParams }: {
  params: Promise<{ datasetId: string; vendorId: string }>;
  searchParams: Promise<{ sent?: string }>;
}) {
  const [{ datasetId, vendorId }, { sent }] = await Promise.all([params, searchParams]);
  const data = await core.vendorAccounts(datasetId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });
  const vendor = data.vendors.find((v) => v.vendor_id === vendorId);
  if (!vendor) notFound();
  const detail = vendor.open_case_id ? await core.case(vendor.open_case_id).catch(() => null) : null;
  const sentAt = sent ?? null;
  const watch = watchFor(sentAt, detail ? { picked: sentAt ? pickedUp(detail.timeline, sentAt) : false } : null);
  const steps = detail ? stations(detail.timeline, detail.case.state, detail.decision?.outcome ?? null) : null;
  const caseHref = detail ? `/d/${datasetId}/cases/${detail.case.id}` : null;
  const working = watch === "detecting" || (watch === "opened" && detail !== null && REFRESH_STATES.has(detail.case.state));
  return (
    <main className="ramp">
      <header className="ramp-head">
        <p className="back"><Link href={`/d/${datasetId}/ramp`}><span className="arrow" aria-hidden="true">←</span> Ramp (mock) vendors</Link></p>
        <p className="section-marker">Ramp (mock) · vendor payment details</p>
        <h1>{vendor.legal_name}</h1>
        <p className="muted">Current account: <span className="mono">{formatAccount(vendor.account)}</span></p>
      </header>

      {watch !== "idle" ? (
        <section className="card ramp-watch" aria-live="polite" aria-labelledby="watch-h">
          <div className="card-head">
            <span className="section-marker">Dialback</span>
            <h2 id="watch-h">{watch === "opened" ? "Dialback caught the change" : "Saved in Ramp (mock). Waiting for Dialback to detect it…"}</h2>
            {detail && watch === "opened" ? <span className="card-meta"><StateBadge state={detail.case.state} /></span> : null}
          </div>
          <div className="card-body">
            {watch === "opened" && steps && caseHref ? (
              <>
                <CaseRail steps={steps} />
                <p className="present-links">
                  <Link className="button-link primary" href={caseHref}>Open the case</Link>
                  {canWatchCall(detail!.timeline) ? <Link className="button-link" href={`${caseHref}/call`}>Watch the call</Link> : null}
                  <Link className="button-link" href={`${caseHref}/flow`}>Flow mode</Link>
                </p>
              </>
            ) : <p className="muted sub">Ramp sends Dialback a signed webhook; this page refreshes until the case opens.</p>}
          </div>
        </section>
      ) : null}

      <section className="card" aria-labelledby="edit-h">
        <div className="card-head"><span className="section-marker">Ramp (mock)</span><h2 id="edit-h">Edit payment details</h2></div>
        <div className="card-body">
          {data.can_change ? <BankChangeForm datasetId={datasetId} vendorId={vendorId} />
            : <div className="callout"><div className="callout-body"><p>{data.reason}</p></div></div>}
        </div>
      </section>
      <AutoRefresh active={working} />
    </main>
  );
}
