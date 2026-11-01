import Link from "next/link";
import { AlertBanners } from "@/components/alerts";
import { core } from "@/lib/core";

// The shell: who is signed in, which dataset, navigation, and the alert banners on every page.
// Pages owned by Inesh (number lineage, vendor risk list) and Sami (call view, scorecard)
// plug in under /d/[datasetId]/ and get a link here when they land.
export default async function DatasetShell({ children, params }: { children: React.ReactNode; params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const [me, datasets, alerts] = await Promise.all([core.me(), core.datasets(), core.alerts(datasetId)]);
  const current = datasets.find((d) => d.id === datasetId);
  return (
    <div className="shell">
      <header className="topbar">
        <nav className="nav" aria-label="Main">
          <Link className="brand" href={`/d/${datasetId}/cases`}>Dialback</Link>
          <Link href={`/d/${datasetId}/cases`}>Case queue</Link>
        </nav>
        <div className="who">
          <span>
            {current ? `${current.label} (${current.kind})` : "Unknown dataset"}
            {current?.sim_now ? ` · simulated time` : ""}
          </span>
          {datasets.length > 1 ? (
            <details>
              <summary>Switch</summary>
              <ul className="plain">
                {datasets.map((d) => <li key={d.id}><Link href={`/d/${d.id}/cases`}>{d.label}</Link></li>)}
              </ul>
            </details>
          ) : null}
          <span>{me.display_name} · {me.roles.join(", ") || "no role"}</span>
          <form action="/sign-out" method="post"><button type="submit">Sign out</button></form>
        </div>
      </header>
      <AlertBanners alerts={alerts} />
      {children}
    </div>
  );
}
