import Link from "next/link";

/** Every page's head: an optional way back, the title, one lead line, page-level actions on the right. */
export function PageHead({ title, lead, back, aside, children }: {
  title: React.ReactNode;
  lead?: React.ReactNode;
  back?: { href: string; label: string };
  aside?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-main">
        {back ? <p className="back"><Link href={back.href}><span className="arrow" aria-hidden="true">←</span> {back.label}</Link></p> : null}
        <h1>{title}</h1>
        {lead ? <p className="lead">{lead}</p> : null}
        {children}
      </div>
      {aside ? <div className="page-head-aside">{aside}</div> : null}
    </header>
  );
}

export type Stat = { label: string; value: number; tone?: "ok" | "warn" | "bad" | "info" };

/** The count strip: one number per thing worth counting. A zero stays neutral. */
export function Stats({ items, label }: { items: Stat[]; label: string }) {
  return (
    <dl className="stats" aria-label={label}>
      {items.map((s) => (
        <div key={s.label} className={`stat${s.value && s.tone ? ` is-${s.tone}` : ""}`}>
          <dt>{s.label}</dt>
          <dd>{s.value}</dd>
        </div>
      ))}
    </dl>
  );
}
