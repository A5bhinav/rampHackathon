import Link from "next/link";
import { AlertBanners } from "@/components/alerts";
import { BrandMark, NavLinks } from "@/components/nav";
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
          <Link className="brand" href={`/d/${datasetId}/cases`}><BrandMark />Dialback</Link>
          <NavLinks links={[{ href: `/d/${datasetId}/cases`, label: "Case queue" }, { href: `/d/${datasetId}/risk`, label: "Vendor risk" }]} />
        </nav>
        <div className="who">
          <span className="chip" title={current?.sim_now ? "This dataset runs on simulated time" : undefined}>
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
          <span className="person">
            <span className="avatar" aria-hidden="true">{initials(me.display_name)}</span>
            <span><strong>{me.display_name}</strong> · {me.roles.join(", ") || "no role"}</span>
          </span>
          <form action="/sign-out" method="post"><button type="submit">Sign out</button></form>
        </div>
      </header>
      <AlertBanners alerts={alerts} datasetId={datasetId} />
      {children}
    </div>
  );
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}
