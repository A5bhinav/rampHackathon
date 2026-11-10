import Link from "next/link";
import { PageHead } from "@/components/page";
import { NEEDS_A_PERSON, QueueTable, bySoonestPayment } from "@/components/queue";
import type { CaseSummary, EvidenceLog } from "@/lib/api-types";
import { stationsFromEvidence } from "@/lib/stations";
import { core } from "@/lib/core";
import { datasetNow } from "@/lib/format";

// Cases (spec 0063): every case on the dataset, released ones included, with state tabs that
// filter on the server. The Inbox shows what waits on a person; this is the whole list.
const TABS = [
  { key: "all", label: "All", test: (_: CaseSummary) => true },
  { key: "needs-you", label: "Needs you", test: (c: CaseSummary) => NEEDS_A_PERSON.includes(c.state) },
  { key: "in-progress", label: "In progress", test: (c: CaseSummary) => !NEEDS_A_PERSON.includes(c.state) && c.state !== "released" },
  { key: "released", label: "Released", test: (c: CaseSummary) => c.state === "released" },
] as const;

export default async function CasesPage({ params, searchParams }: {
  params: Promise<{ datasetId: string }>;
  searchParams: Promise<{ state?: string }>;
}) {
  const [{ datasetId }, { state }] = await Promise.all([params, searchParams]);
  const [all, datasets, log] = await Promise.all([core.allCases(datasetId), core.datasets(), core.evidence(datasetId).catch((): EvidenceLog | null => null)]);
  const rails = new Map(all.map((c) => [c.id, stationsFromEvidence(log?.events ?? [], c)]));
  const dataset = datasets.find((d) => d.id === datasetId);
  const now = datasetNow(dataset?.sim_now);
  const tab = TABS.find((t) => t.key === state) ?? TABS[0];
  const shown = all.filter(tab.test).sort(bySoonestPayment);
  const base = `/d/${datasetId}/cases/all`;
  return (
    <main className="inbox cases-all">
      <PageHead eyebrow={`${dataset?.label ?? "Dataset"} · Accounts payable`} title="Cases"
        lead={all.length >= 200 ? "Showing the first 200 cases on this dataset." : `${all.length} case${all.length === 1 ? "" : "s"} on this dataset.`} />
      <div className="tabs-row">
        <nav className="tabs" aria-label="Filter cases by state">
          {TABS.map((t) => (
            <Link key={t.key} href={t.key === "all" ? base : `${base}?state=${t.key}`} className={`tab${t === tab ? " is-on" : ""}`}
                  aria-current={t === tab ? "page" : undefined}>
              {t.label} <span className="tab-n">{all.filter(t.test).length}</span>
            </Link>
          ))}
        </nav>
        <span className="section-marker">Sorted by soonest payment</span>
      </div>
      {shown.length ? <QueueTable id="cases" title={tab.label === "All" ? "Every case" : tab.label} cases={shown} datasetId={datasetId} now={now} rails={rails} />
        : <div className="empty-card"><p>No cases here. A case opens when a vendor&apos;s bank details change in Ramp.</p></div>}
    </main>
  );
}
