import { notFound } from "next/navigation";
import { PageHead, Stats } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { phone } from "@/lib/lineage";
import { counts, digitMarks, shares, statusLabel, statusTone, type RiskStatus } from "@/lib/risk";

const STRIPE = { needs_person: "is-bad", near_miss: "is-bad", unproven: "is-warn", missing: "", provable: "is-ok" } as const;

// The vendor risk list (spec 0026, owner Inesh), the demo's opening screen: is each vendor's number
// on file one the Trust Ledger can prove? Every explanation is the ledger's status_text.
export default async function RiskPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const vendors = await core.vendorRisk(datasetId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });

  return (
    <main>
      <PageHead eyebrow="Control · posture" title="Trust Ledger"
        lead="Is each vendor's number on file one the Trust Ledger can prove? Each row says why, in the ledger's own words." />
      {vendors.length > 0 ? <CountStrip vendors={vendors} /> : null}
      {vendors.length > 0 ? <RiskMix vendors={vendors} /> : null}
      {vendors.length === 0 ? (
        <section className="card empty-card"><p>The Trust Ledger has no documents for this dataset&apos;s vendors yet.</p></section>
      ) : (
        <section className="card flush" aria-label="Vendors">
          <div className="table-wrap">
            <table className="risk">
              <thead>
                <tr><th className="w-vendor">Vendor</th><th>Status</th><th>On file</th><th>Provable</th></tr>
              </thead>
              <tbody>
                {vendors.map((v) => (
                  <tr key={v.vendor_id} className={STRIPE[v.status]}>
                    <td><strong className="vendor-name">{v.legal_name}</strong><div className="muted sub cell-note">{v.status_text}</div></td>
                    <td><span className={`badge ${statusTone(v.status)}`}>{statusLabel(v.status)}</span></td>
                    <td className="mono nowrap">{v.number_on_file ? <OnFile number={v.number_on_file} provable={v.status === "near_miss" ? v.provable_number : null} /> : "—"}</td>
                    <td className="mono nowrap">{v.provable_number ? phone(v.provable_number) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const STAT_TONE = { needs_person: "bad", near_miss: "bad", unproven: "warn", missing: undefined, provable: "ok" } as const;

/** The count strip, worst first: needs a person, near miss, unproven, missing, provable. */
function CountStrip({ vendors }: { vendors: { status: RiskStatus }[] }) {
  return (
    <Stats label="Vendors by status" items={counts(vendors).map(([status, n]) => ({ label: cap(statusLabel(status)), value: n, tone: STAT_TONE[status] }))} />
  );
}

const KEY = { needs_person: "bad", near_miss: "bad", unproven: "warn", missing: "muted", provable: "ok" } as const;

/** Vendors by status as one segmented bar (spec 0032): shares inside the blocks that fit them,
 *  the legend naming each with its count, worst first. */
function RiskMix({ vendors }: { vendors: { status: RiskStatus }[] }) {
  const rows = counts(vendors);
  const pct = shares(rows.map(([, n]) => n));
  const label = rows.map(([status, n], i) => `${statusLabel(status)} ${n} (${pct[i]}%)`).join(", ");
  return (
    <section className="card mix" aria-labelledby="mix-h">
      <div className="chart-head">
        <span className="section-marker">Mix</span>
        <h2 id="mix-h">Vendors by status <span className="count">{vendors.length}</span></h2>
      </div>
      <div className="segbar" role="img" aria-label={`Vendors by status: ${label}`}>
        {rows.map(([status, n], i) => n ? (
          <span key={status} className={`seg seg-${KEY[status]}`} style={{ flex: `${n} 1 0px` }} title={`${cap(statusLabel(status))}: ${n} (${pct[i]}%)`}>
            {pct[i] >= 10 ? `${pct[i]}%` : ""}
          </span>
        ) : null)}
      </div>
      <ul className="legend mix-legend">
        {rows.map(([status, n]) => (
          <li key={status}><span className={`key seg-${KEY[status]}`} aria-hidden="true" />{cap(statusLabel(status))} <strong>{n}</strong></li>
        ))}
      </ul>
    </section>
  );
}

/** The number on file; for a near miss, the digits that differ from the provable one are marked. */
function OnFile({ number, provable }: { number: string; provable: string | null }) {
  const marks = digitMarks(number, provable);
  if (!marks.some((m) => m.differs)) return <>{phone(number)}</>;
  return (
    <span title="Marked digits differ from the provable number">
      {marks.map((m, i) => m.differs ? <mark key={i} className="digit-off">{m.ch}</mark> : m.ch)}
    </span>
  );
}
