import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertChip } from "./alerts";
import { BrandMark, Crumbs, NavLinks } from "./nav";
import { ViewingAs } from "./viewing-as";
import type { Dataset, Me } from "@/lib/api-types";
import { canSeeControl, navFor, switchTargets, type AppKey } from "@/lib/apps";
import { isAdmin, roleLabels } from "@/lib/people";
import { presentLinks } from "@/lib/live-call";
import { demoAccounts } from "@/lib/auth-mode";
import { CoreError, core } from "@/lib/core";

// The two apps' shell (spec 0063): a paper sidebar with the app switch, the dataset, that app's
// navigation and who is signed in; a slim utility bar; then the alert banners. AP workspace and
// Control share it and differ only in their navigation and the "Control" badge.
export async function Shell({ app, datasetId, children }: { app: AppKey; datasetId: string; children: React.ReactNode }) {
  // An unknown dataset (404) or one that isn't an id (422) is a not-found page, not an error.
  const [me, datasets, alerts] = await Promise.all([core.me(), core.datasets(), core.alerts(datasetId).catch((e) => {
    if (e instanceof CoreError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  })]);
  const current = datasets.find((d) => d.id === datasetId);
  // The Present menu (spec 0065): demo datasets in AP workspace only, for the newest open case.
  const present = app === "ap" && current?.kind === "demo"
    ? presentLinks(current.kind, await core.cases(datasetId).catch(() => []), datasetId) : [];  // open cases only: lighter than every case
  const control = app === "control" || canSeeControl(me.roles, current?.kind ?? "");
  const go = switchTargets(datasetId);
  const demo = demoAccounts(process.env) !== null;
  return (
    <div className={`app app-${app}`}>
      <aside className="side" aria-label="Sidebar">
        <div className="side-top">
          <Link className="brand" href={go[app]}><BrandMark />Dialback{app === "control" ? <span className="badge tone-muted app-badge">Control</span> : null}</Link>
          <details className="side-more">
            <summary>Menu</summary>
            <DatasetCard current={current} datasets={datasets} app={app} />
            <Person me={me} />
          </details>
        </div>
        {control ? (
          <nav className="app-switch" aria-label="Apps">
            <Link href={go.ap} aria-current={app === "ap" ? "true" : undefined}>AP workspace</Link>
            <Link href={go.control} aria-current={app === "control" ? "true" : undefined}>Control</Link>
          </nav>
        ) : null}
        <div className="side-desk"><DatasetCard current={current} datasets={datasets} app={app} /></div>
        <nav className="side-nav" aria-label={app === "ap" ? "AP workspace" : "Control"}>
          <NavLinks links={navFor(app, datasetId)} />
          {app === "ap" && current?.kind === "demo" ? (
            <>
              <p className="side-group">Demo</p>
              <NavLinks links={[{ href: `/d/${datasetId}/ramp`, label: "Ramp (mock)", icon: "card" }]} />
            </>
          ) : null}
          {app === "control" && isAdmin(me.roles) ? (
            <>
              <p className="side-group">Admin</p>
              <NavLinks links={[{ href: `/d/${datasetId}/control/demo`, label: "Demo controls", icon: "lock" }]} />
            </>
          ) : null}
        </nav>
        <div className="side-foot side-desk">
          {current ? <p className="side-status"><span className="dot" aria-hidden="true" />Ramp Bill Pay · {current.kind} dataset</p> : null}
          <Person me={me} />
        </div>
      </aside>
      <div className="main-col">
        <div className="utilbar">
          <Crumbs datasetId={datasetId} app={app} />
          <AlertChip alerts={alerts} datasetId={datasetId} />
          {present.length ? (
            <details className="present">
              <summary>Present</summary>
              <ul className="plain">{present.map((l) => <li key={l.href}><Link href={l.href}>{l.label}</Link></li>)}</ul>
            </details>
          ) : null}
          {demo ? <ViewingAs active={me.roles.includes("approver") ? "approver" : "clerk"} /> : null}
        </div>
        {children}
      </div>
    </div>
  );
}

function DatasetCard({ current, datasets, app }: { current: Dataset | undefined; datasets: Dataset[]; app: AppKey }) {
  return (
    <div className="dataset-card" title={current?.sim_now ? "This dataset runs on simulated time" : undefined}>
      <span className="dataset-name">{current ? current.label : "Unknown dataset"}</span>
      {current ? <span className="badge tone-muted">{current.kind}{current.sim_now ? ", simulated time" : ""}</span> : null}
      {datasets.length > 1 ? (
        <details className="switch">
          <summary>Switch</summary>
          <ul className="plain">
            {datasets.map((d) => <li key={d.id}><Link href={switchTargets(d.id)[app]}>{d.label}</Link></li>)}
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
        <span className="person-role">{roleLabels(me.roles)}</span>
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
