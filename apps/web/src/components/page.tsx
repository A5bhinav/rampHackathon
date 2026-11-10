import { RingBand, type RingVariant } from "./ring-band";

/** Every page's head (spec 0041): the ring band with an optional way back, an eyebrow, the title,
 *  one lead line, tags under it, and an aside on the right that clears the rings. */
export function PageHead({ title, lead, back, aside, children, eyebrow, variant, titleId }: {
  title: React.ReactNode;
  lead?: React.ReactNode;
  back?: { href: string; label: string };
  aside?: React.ReactNode;
  children?: React.ReactNode;
  eyebrow?: React.ReactNode;
  variant?: RingVariant;
  titleId?: string;
}) {
  return (
    <RingBand title={title} lead={lead} back={back} aside={aside} eyebrow={eyebrow} variant={variant} titleId={titleId}>
      {children}
    </RingBand>
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
