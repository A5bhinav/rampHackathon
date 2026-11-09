import Link from "next/link";

/** Every page's header, so titles, back links and side details line up the same everywhere:
 *  the title block on the left, page-level facts on the right (stacked below on a phone). */
export function PageHead({ back, title, lead, aside, children }: {
  back?: { href: string; label: string };
  title: React.ReactNode;
  lead?: React.ReactNode;
  aside?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-main">
        {back ? <p className="back"><Link href={back.href}><span className="arrow" aria-hidden="true">←</span>{back.label}</Link></p> : null}
        <h1>{title}</h1>
        {lead ? <p className="lead">{lead}</p> : null}
        {children}
      </div>
      {aside ? <div className="page-head-aside">{aside}</div> : null}
    </header>
  );
}

/** A row of counts: each a number and what it counts, with an optional tone. */
export function Stats({ items, label }: { items: { n: number; label: string; tone?: string }[]; label: string }) {
  return (
    <dl className="stats" aria-label={label}>
      {items.map((s) => (
        <div key={s.label} className={s.n > 0 && s.tone ? `stat ${s.tone}` : "stat"}>
          <dt>{s.label}</dt>
          <dd>{s.n}</dd>
        </div>
      ))}
    </dl>
  );
}
