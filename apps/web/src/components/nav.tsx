"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment } from "react";
import { isCurrent, pageCrumb, type AppKey } from "@/lib/apps";
import { Icon, type IconName } from "./icons";

export type NavLink = { href: string; label: string; icon?: IconName; count?: number; group?: string; exact?: boolean; not?: string[] };

// A sidebar's page links (spec 0063): the current page is marked for screen readers and drawn
// filled; a group label (Settings) starts its run of items.
export function NavLinks({ links }: { links: NavLink[] }) {
  const path = usePathname();
  return (
    <>
      {links.map((l, i) => {
        const current = isCurrent(l, path);
        return (
          <Fragment key={l.label}>
            {l.group && links[i - 1]?.group !== l.group ? <p className="side-group">{l.group}</p> : null}
            <Link className="nav-item" href={l.href} aria-current={current ? "page" : undefined}>
              {l.icon ? <Icon name={l.icon} size={16} /> : <span className="nav-blank" aria-hidden="true" />}
              <span className="nav-label">{l.label}</span>
              {l.count ? <span className="nav-count">{l.count}</span> : null}
            </Link>
          </Fragment>
        );
      })}
    </>
  );
}

// Where you are, from the path alone (the shell doesn't fetch the case): Inbox / Case / Number lineage.
export function Crumbs({ datasetId, app }: { datasetId: string; app: AppKey }) {
  const path = usePathname();
  const base = `/d/${datasetId}`;
  const ramp = new RegExp(`^${base}/ramp/([^/]+)$`).exec(path);
  if (ramp) {
    return (
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href={`${base}/ramp`}>Ramp (mock)</Link><span aria-hidden="true">/</span><span aria-current="page">Vendor</span>
      </nav>
    );
  }
  const m = new RegExp(`^${base}/cases/(?!all$)([^/]+)(/lineage)?$`).exec(path);  // /cases/all is the Cases list, not a case
  if (!m) {
    // Every other page: the app, then the page, so the top bar is always there (Control / Evidence).
    const c = pageCrumb(app, datasetId, path);
    return (
      <nav className="crumbs" aria-label="Breadcrumb">
        {c.page ? <><Link href={c.app.href}>{c.app.label}</Link><span aria-hidden="true">/</span><span aria-current="page">{c.page}</span></>
          : <span aria-current="page">{c.app.label}</span>}
      </nav>
    );
  }
  const kase = `${base}/cases/${m[1]}`;
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <Link href={`${base}/cases`}>Inbox</Link>
      <span aria-hidden="true">/</span>
      {m[2] ? <><Link href={kase}>Case</Link><span aria-hidden="true">/</span><span aria-current="page">Number lineage</span></>
        : <span aria-current="page">Case</span>}
    </nav>
  );
}

/** The current path, for a form that returns here after a server action. */
export function HerePath({ name = "next" }: { name?: string }) {
  return <input type="hidden" name={name} value={usePathname()} />;
}

// The brand mark: the callback rings, in small.
export function BrandMark() {
  return <span className="brand-mark" aria-hidden="true" />;
}
