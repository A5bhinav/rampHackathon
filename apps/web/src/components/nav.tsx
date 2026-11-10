"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, type IconName } from "./icons";

export type NavLink = { href: string; label: string; icon?: IconName; count?: number };

// The shell's page links: the current page is marked for screen readers and drawn filled.
export function NavLinks({ links }: { links: NavLink[] }) {
  const path = usePathname();
  return (
    <>
      {links.map((l) => {
        const current = path === l.href || path.startsWith(`${l.href}/`);
        return (
          <Link key={l.label} className="nav-item" href={l.href} aria-current={current ? "page" : undefined}>
            {l.icon ? <Icon name={l.icon} size={16} /> : <span className="nav-blank" aria-hidden="true" />}
            <span className="nav-label">{l.label}</span>
            {l.count ? <span className="nav-count">{l.count}</span> : null}
          </Link>
        );
      })}
    </>
  );
}

// Where you are, from the path alone (the shell doesn't fetch the case): Inbox / Case / Number lineage.
export function Crumbs({ datasetId }: { datasetId: string }) {
  const path = usePathname();
  const base = `/d/${datasetId}`;
  const m = new RegExp(`^${base}/cases/([^/]+)(/lineage)?$`).exec(path);
  if (!m) return <span />;
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
