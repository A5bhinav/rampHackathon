/** An inner page's head (spec 0066): one title line (with any actions on the right) and one subtitle
 *  line: the tags when given, else the lead. No ring art, no eyebrow and no back link: the top bar's
 *  breadcrumbs say where you are. The rings stay on the front door and sign-in (RingBand, Rings). */
export function PageHead({ title, lead, aside, children, titleId }: {
  title: React.ReactNode;
  lead?: React.ReactNode;
  back?: { href: string; label: string };   // kept for callers; the breadcrumbs carry the way back
  aside?: React.ReactNode;
  children?: React.ReactNode;
  eyebrow?: React.ReactNode;                 // kept for callers; not shown
  titleId?: string;
}) {
  const sub = children ?? lead;
  return (
    <header className="page-title">
      <div className="page-title-row">
        <h1 id={titleId}>{title}</h1>
        {aside ? <div className="page-title-aside">{aside}</div> : null}
      </div>
      {sub ? <div className="page-sub">{sub}</div> : null}
    </header>
  );
}

export type Stat = { label: string; value: React.ReactNode; tone?: "ok" | "warn" | "bad" | "info"; sub?: React.ReactNode };

/** The KPI strip: one bordered card split into cells, a marker label, then the figure. A tone
 *  colours only a non-zero figure; a zero stays neutral. */
export function Stats({ items, label }: { items: Stat[]; label: string }) {
  return (
    <dl className="stats" aria-label={label}>
      {items.map((s) => (
        <div key={s.label} className={`stat${s.value && s.tone ? ` is-${s.tone}` : ""}`}>
          <dt>{s.label}</dt>
          <dd>{s.value}</dd>
          {s.sub ? <dd className="stat-sub">{s.sub}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

/** A card's head: the mono marker, the title, and a right-aligned meta slot. */
export function CardHead({ marker, title, id, meta, as: H = "h2" }: {
  marker?: string; title: React.ReactNode; id?: string; meta?: React.ReactNode; as?: "h2" | "h3";
}) {
  return (
    <div className="card-head">
      {marker ? <span className="section-marker">{marker}</span> : null}
      <H id={id}>{title}</H>
      {meta ? <span className="card-meta">{meta}</span> : null}
    </div>
  );
}

/** The callout: a quiet block for notes, notices, refusals and errors. */
export function Callout({ tone = "neutral", title, icon, children, role, className = "" }: {
  tone?: "neutral" | "ok" | "warn" | "bad"; title?: React.ReactNode; icon?: React.ReactNode; children?: React.ReactNode;
  role?: "status" | "alert"; className?: string;
}) {
  return (
    <div className={`callout callout-${tone}${icon ? " has-icon" : ""} ${className}`} role={role}>
      {icon ? <span className="callout-icon">{icon}</span> : null}
      <div className="callout-body">
        {title ? <p className="callout-title">{title}</p> : null}
        {children}
      </div>
    </div>
  );
}
