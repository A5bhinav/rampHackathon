import Link from "next/link";
import { Icon } from "@/components/icons";
import { BrandMark } from "@/components/nav";
import { Rings } from "@/components/ring-band";
import { DOORS, STATION_WORDS } from "@/lib/front-door";
import "./landing.css";

// The front door (spec 0063, plan v3 section 3): one screen whose job is to get someone into the
// product. Static: it calls nothing and decides nothing. The explanation lives in the product.
export default function FrontDoor() {
  return (
    <div className="door">
      <header className="door-head">
        <Link className="brand" href="/"><BrandMark />Dialback</Link>
        <nav className="door-nav" aria-label="Main">
          <Link href="/app/control">How it works</Link>
          <Link href="/sign-in">Sign in</Link>
        </nav>
      </header>
      <main className="door-main">
        <Rings className="rings door-rings" />
        <section className="door-hero" aria-labelledby="door-h">
          <p className="eyebrow">For accounts payable teams on Ramp</p>
          <h1 id="door-h">A changed bank account gets paid only after the real vendor says so.</h1>
          <p className="lead">
            Dialback holds the payment, calls the vendor on a number it can prove, lets code decide, and waits for a second person.
          </p>
          <p className="door-cta">
            {DOORS.map((d) => <Link key={d.href} className={`button-link${d.tone === "primary" ? " primary" : ""}`} href={d.href}>{d.label}</Link>)}
          </p>
        </section>
        <ol className="door-stations" aria-label="Every bank change goes through seven stations">
          {STATION_WORDS.map((s, i) => (
            <li key={s.word}>
              <span className="door-node"><Icon name={s.icon} size={16} /></span>
              <span><span className="sr-only">{i + 1}. </span>{s.word}</span>
            </li>
          ))}
        </ol>
      </main>
    </div>
  );
}
