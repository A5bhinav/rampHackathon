import { notFound } from "next/navigation";
import { CardHead, PageHead } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { when } from "@/lib/format";
import { integrationRows } from "@/lib/settings";

// Integrations (spec 0063): what the API reports about the dataset's connections today: Ramp (the
// dataset's kind), the evidence chain and its scheduled check, and open alerts. Webhooks and the
// voice service say "not reported yet" until the health read lands.
export default async function IntegrationsPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const [datasets, log, alerts] = await Promise.all([
    core.datasets(),
    core.evidence(datasetId).catch((e) => {
      if (e instanceof CoreError && e.status === 404) notFound();
      throw e;
    }),
    core.alerts(datasetId),
  ]);
  const dataset = datasets.find((d) => d.id === datasetId);
  const rows = integrationRows({ kind: dataset?.kind ?? "unknown", chain: log.chain, lastCheck: log.last_check, alerts });
  return (
    <main>
      <PageHead eyebrow="Control · settings" title="Integrations" lead="The dataset's connections, as the core reports them." />
      <section className="card flush" aria-labelledby="int-h">
        <CardHead marker="Health" title="Connections" id="int-h"
          meta={log.last_check?.finished_at ? <span className="muted sub">chain checked {when(log.last_check.finished_at)}</span> : null} />
        <ul className="int-list">
          {rows.map((r) => (
            <li key={r.name}><span className="int-name">{r.name}</span><span className={`badge tone-${r.tone}`}>{r.value}</span></li>
          ))}
        </ul>
      </section>
    </main>
  );
}
