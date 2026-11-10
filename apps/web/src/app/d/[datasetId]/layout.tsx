import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertBanners } from "@/components/alerts";
import { BrandMark, Crumbs, NavLinks, type NavLink } from "@/components/nav";
import { ViewingAs } from "@/components/viewing-as";
import type { Dataset, Me } from "@/lib/api-types";
import { demoAccounts } from "@/lib/auth-mode";
import { CoreError, core } from "@/lib/core";

// The shell (spec 0041): a paper sidebar with the dataset, navigation and who is signed in, a
// slim utility bar, then the alert banners on every page. Pages plug in under /d/[datasetId]/.
export default async function DatasetShell({ children, params }: { children: React.ReactNode; params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  // An unknown dataset (404) or one that isn't an id (422) is a not-found page, not an error.
  const [me, datasets, alerts] = await Promise.all([core.me(), core.datasets(), core.alerts(datasetId).catch((e) => {
    if (e instanceof CoreError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  })]);
  const current = datasets.find((d) => d.id === datasetId);
  const base = `/d/${datasetId}`;
  const ap: NavLink[] = [{ href: `${base}/cases`, label: "Inbox", icon: "inbox" }];
  const control: NavLink[] = [
    { href: `${base}/overview`, label: "Overview", icon: "grid" },
    { href: `${base}/risk`, label: "Trust Ledger", icon: "shield" },
    { href: `${base}/evidence`, label: "Evidence", icon: "link" },
    { href: "/corpus", label: "Assurance", icon: "check-circle" },
  ];
  const demo = demoAccounts(process.env) !== null;
  return (
    <div className="app">
      <aside className="side" aria-label="Sidebar">
        <div className="side-top">
          <Link className="brand" href={`${base}/cases`}><BrandMark />Dialback</Link>
          <details className="side-more">
            <summary>Menu</summary>
            <DatasetCard current={current} datasets={datasets} />
            <Person me={me} />
          </details>
        </div>
        <div className="side-desk"><DatasetCard current={current} datasets={datasets} /></div>
        <nav className="side-nav" aria-label="Main">
          <p className="side-group">Accounts payable</p>
          <NavLinks links={ap} />
          <p className="side-group">Control</p>
          <NavLinks links={control} />
          <span className="side-gap" />
          <NavLinks links={[{ href: "/", label: "How it works", icon: "book" }]} />
        </nav>
        <div className="side-foot side-desk">
          {current ? <p className="side-status"><span className="dot" aria-hidden="true" />Ramp Bill Pay · {current.kind} dataset</p> : null}
          <Person me={me} />
        </div>
      </aside>
      <div className="main-col">
        <div className="utilbar">
          <Crumbs datasetId={datasetId} />
          {demo ? <ViewingAs active={me.roles.includes("approver") ? "approver" : "clerk"} /> : null}
        </div>
        <AlertBanners alerts={alerts} datasetId={datasetId} />
        {children}
      </div>
    </div>
  );
}

function DatasetCard({ current, datasets }: { current: Dataset | undefined; datasets: Dataset[] }) {
  return (
    <div className="dataset-card" title={current?.sim_now ? "This dataset runs on simulated time" : undefined}>
      <span className="dataset-name">{current ? current.label : "Unknown dataset"}</span>
      {current ? <span className="badge tone-muted">{current.kind}{current.sim_now ? ", simulated time" : ""}</span> : null}
      {datasets.length > 1 ? (
        <details className="switch">
          <summary>Switch</summary>
          <ul className="plain">
            {datasets.map((d) => <li key={d.id}><Link href={`/d/${d.id}/cases`}>{d.label}</Link></li>)}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Person({ me }: { me: Me }) {
  return (
    <div className="person">
      <span className="avatar" aria-hidden="true">{initials(me.display_name)}</span>
      <span className="person-text">
        <strong>{me.display_name}</strong>
        <span className="person-role">{me.roles.join(", ") || "no role"}</span>
        {/* The team grants approver by this id (spec 0027), so the person can read it here. */}
        <span className="mono person-id" title="Your user ID: give it to the team to be made an approver">ID {me.user_id}</span>
      </span>
      <form action="/sign-out" method="post"><button type="submit" className="quiet">Sign out</button></form>
    </div>
  );
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}
