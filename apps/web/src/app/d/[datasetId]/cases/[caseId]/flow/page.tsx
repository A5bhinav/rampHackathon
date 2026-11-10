import Link from "next/link";
import { notFound } from "next/navigation";
import { AutoRefresh } from "@/components/auto-refresh";
import { ChainBadge } from "@/components/badges";
import { BrandMark } from "@/components/nav";
import { Rings } from "@/components/ring-band";
import { CoreError, core } from "@/lib/core";
import { brief, when } from "@/lib/format";
import { flowColumns } from "@/lib/live-call";
import { REFRESH_STATES } from "@/lib/stations";

// Flow mode (spec 0065), for the projector: seven columns that light up as each evidence event
// lands, each with its time, the core's own sentence and the short hash. Display only.
export default async function FlowMode({ params }: { params: Promise<{ datasetId: string; caseId: string }> }) {
  const { datasetId, caseId } = await params;
  const detail = await core.case(caseId).catch((e) => {
    if (e instanceof CoreError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  });
  const c = detail.case;
  if (c.dataset_id !== datasetId) notFound();  // outside the AP shell, so check the dataset here
  const cols = flowColumns(detail.timeline, c.state, detail.decision?.outcome ?? null);
  const base = `/d/${datasetId}/cases/${caseId}`;
  return (
    <main className="flow-mode">
      <Rings className="rings flow-rings" />
      <header className="flow-top">
        <Link className="brand" href={base}><BrandMark />Dialback</Link>
        <Link className="badge-link" href={`/d/${datasetId}/control/evidence`}><ChainBadge chain={detail.chain} /></Link>
      </header>
      <section className="flow-head" aria-labelledby="flow-h">
        <p className="flow-sub">{c.vendor.legal_name} · {c.bills_held} {c.bills_held === 1 ? "bill" : "bills"} held</p>
        <h1 id="flow-h">AI collects. Code decides. A person releases.</h1>
      </section>
      <ol className="flow-cols" aria-label="The seven stations">
        {cols.map((s, i) => (
          <li key={s.key} className={`flow-col is-${s.status} st-${s.tone}`} aria-current={s.status === "current" ? "step" : undefined}>
            <span className="flow-n">{String(i + 1).padStart(2, "0")}</span>
            <span className="flow-label">{s.label}<span className="sr-only">: {s.status === "todo" ? "not reached" : s.status === "branched" ? "stopped" : s.status === "current" ? "now" : "done"}</span></span>
            {s.marker ? <span className="flow-marker">{s.marker}</span> : null}
            <span className="flow-summary">{s.summary ?? (s.status === "current" ? "Now" : "")}</span>
            <span className="flow-meta">
              {s.at ? <span title={when(s.at)}>{brief(s.at)}</span> : null}
              {s.hash ? <span className="mono" title="Evidence hash">{s.hash}</span> : null}
            </span>
          </li>
        ))}
      </ol>
      <p className="flow-foot"><Link href={base}>Back to the case</Link> · <Link href={`${base}/call`}>Live call</Link></p>
      <AutoRefresh active={REFRESH_STATES.has(c.state)} />
    </main>
  );
}
