import Link from "next/link";

// Signature A, "callback rings" (spec 0041): the paper band at the top of every page, with five
// concentric circles off its right edge. The rings are the product's only decoration; they are
// hidden from screen readers and never take a click.
export type RingVariant = "default" | "compact" | "dark";

export function Rings({ className = "rings" }: { className?: string }) {
  return (
    <span className={className} aria-hidden="true">
      <span className="ring r-outline" />
      <span className="ring r-deep" />
      <span className="ring r-mid" />
      <span className="ring r-light" />
      <span className="ring r-center" />
    </span>
  );
}

export function RingBand({ title, eyebrow, lead, back, aside, children, variant = "default", titleId }: {
  title: React.ReactNode;
  eyebrow?: React.ReactNode;
  lead?: React.ReactNode;
  back?: { href: string; label: string };
  aside?: React.ReactNode;
  children?: React.ReactNode;
  variant?: RingVariant;
  titleId?: string;
}) {
  return (
    <header className={`ring-band ring-${variant}`}>
      <Rings />
      <div className="ring-band-inner">
        <div className="ring-band-main">
          {back ? <p className="back"><Link href={back.href}><span className="arrow" aria-hidden="true">←</span> {back.label}</Link></p> : null}
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h1 id={titleId}>{title}</h1>
          {lead ? <p className="lead">{lead}</p> : null}
          {children}
        </div>
        {aside ? <div className="ring-band-aside">{aside}</div> : null}
      </div>
    </header>
  );
}

/** The small three-ring motif for empty states and the public footer. */
export function RingMotif() {
  return (
    <span className="ring-motif" aria-hidden="true"><span /><span /><span /></span>
  );
}
