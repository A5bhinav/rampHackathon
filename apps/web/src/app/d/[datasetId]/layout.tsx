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
          <Link href={`/d/${datasetId}/risk`}>Vendor risk</Link>
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
          {/* The team grants approver by this id (spec 0027), so the person can read it here. */}
          <span className="muted mono" title="Your user ID: give it to the team to be made an approver">ID {me.user_id}</span>
          <form action="/sign-out" method="post"><button type="submit">Sign out</button></form>
        </div>
      </header>
      <AlertBanners alerts={alerts} datasetId={datasetId} />
      {children}
    </div>
  );
}
