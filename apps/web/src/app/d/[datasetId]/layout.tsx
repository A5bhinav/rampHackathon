import Link from "next/link";
import { AlertBanners } from "@/components/alerts";
import { BrandMark, NavLinks } from "@/components/nav";
import { notFound } from "next/navigation";
import { CoreError, core } from "@/lib/core";

// The shell: who is signed in, which dataset, navigation, and the alert banners on every page.
// Pages owned by Inesh (number lineage, vendor risk list) and Sami (call view, scorecard)
// plug in under /d/[datasetId]/ and get a link here when they land.
export default async function DatasetShell({ children, params }: { children: React.ReactNode; params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  // An unknown dataset (404) or one that isn't an id (422) is a not-found page, not an error.
  const [me, datasets, alerts] = await Promise.all([core.me(), core.datasets(), core.alerts(datasetId).catch((e) => {
    if (e instanceof CoreError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  })]);
  const current = datasets.find((d) => d.id === datasetId);
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
        <nav className="nav" aria-label="Main">
          <Link className="brand" href={`/d/${datasetId}/cases`}><BrandMark />Dialback</Link>
          <NavLinks links={[{ href: `/d/${datasetId}/cases`, label: "Case queue" }, { href: `/d/${datasetId}/risk`, label: "Vendor risk" },
            { href: `/d/${datasetId}/evidence`, label: "Evidence" }]} />
          <Link href="/">How it works</Link>
        </nav>
        <div className="who">
          <span className="chip" title={current?.sim_now ? "This dataset runs on simulated time" : undefined}>
            {current ? current.label : "Unknown dataset"}
            {current ? <span className="chip-kind">{current.kind}{current.sim_now ? ", simulated time" : ""}</span> : null}
          </span>
          {datasets.length > 1 ? (
            <details className="switch">
              <summary>Switch</summary>
              <ul className="plain">
                {datasets.map((d) => <li key={d.id}><Link href={`/d/${d.id}/cases`}>{d.label}</Link></li>)}
              </ul>
            </details>
          ) : null}
          <span className="person">
            <span className="avatar" aria-hidden="true">{initials(me.display_name)}</span>
            <span className="person-text">
              <span><strong>{me.display_name}</strong> <span className="person-role">{me.roles.join(", ") || "no role"}</span></span>
              {/* The team grants approver by this id (spec 0027), so the person can read it here. */}
              <span className="mono muted" title="Your user ID: give it to the team to be made an approver">ID {me.user_id}</span>
            </span>
          </span>
          <form action="/sign-out" method="post"><button type="submit" className="quiet">Sign out</button></form>
        </div>
        </div>
      </header>
      <div className="shell">
        <AlertBanners alerts={alerts} datasetId={datasetId} />
        {children}
      </div>
    </>
  );
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}
