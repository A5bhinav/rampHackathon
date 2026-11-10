import Link from "next/link";
import { BrandMark } from "./nav";

/** The public pages' header (spec 0041): brand, a few links, and the way into the dashboard. */
export function PublicHeader({ links }: { links: { href: string; label: string }[] }) {
  return (
    <header className="pub-head">
      <nav className="pub-nav" aria-label="Main">
        <Link className="brand" href="/"><BrandMark />Dialback</Link>
        {links.map((l) => (l.href.startsWith("#") ? <a key={l.href} href={l.href}>{l.label}</a> : <Link key={l.href} href={l.href}>{l.label}</Link>))}
      </nav>
      <Link className="button-link primary" href="/app">Open the dashboard</Link>
    </header>
  );
}
