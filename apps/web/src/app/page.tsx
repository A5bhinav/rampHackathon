import Link from "next/link";
import { Icon } from "@/components/icons";
import { BrandMark } from "@/components/nav";
import { PublicHeader } from "@/components/public-header";
import { RingMotif, Rings } from "@/components/ring-band";
import { HeroShot, StationShot } from "@/components/showcase";
import { ACROSS, DEMO_STORIES, STORY_STATIONS, type StoryKey } from "@/lib/story";
import "./landing.css";

// The public "How it works" page (spec 0033). Static: it calls nothing and decides nothing. The
// dashboard itself is at /app, behind sign-in.
const ICON: Record<StoryKey, Parameters<typeof Icon>[0]["name"]> = {
  change: "bank", detect: "detect", hold: "hold", number: "ledger", call: "call", decide: "decision", release: "person",
};

const SCAM = [
  { title: "A vendor's email is taken over", text: "The fraudster reads the real thread with your AP team: the invoices, the names, the payment dates." },
  { title: "\"We've changed banks\"", text: "A request arrives from the vendor's own address, with new account details and a phone number to confirm them." },
  { title: "The callback gets skipped", text: "AP is told to phone the vendor at a trusted number first. Under a payment deadline, they call the number in the email, or don't call at all." },
  { title: "The money is gone", text: "The next payment goes to the fraudster's account. By the time the real vendor asks where it is, it has moved on." },
];

const COMPARE: { row: string; old: string; now: string }[] = [
  { row: "When a bank change appears", old: "Payments keep flowing unless someone remembers to stop them.",
    now: "Payments to that vendor are held in Ramp as soon as the change is detected, and the hold is read back (R1)." },
  { row: "Which number gets called", old: "Often the one in the email, which is the fraudster's.",
    now: "Only a number seen in two independent sources before the request; never one first seen in its thread (R2)." },
  { row: "Who makes the call", old: "A busy clerk, if there is time before the payment run.",
    now: "An AI agent that says it is an AI on a recorded line, and never says the new details (R4, R8)." },
  { row: "Who decides", old: "The clerk's judgment of a convincing voice.",
    now: "A fixed rule table over the recorded answers; the model can't set the outcome (R3)." },
  { row: "Who releases the money", old: "Often the same person who entered the change.",
    now: "A different person, with a written reason; Postgres refuses the person who entered it (R5)." },
  { row: "What's on record", old: "A note in the AP system, if anything.",
    now: "Every step in a hash-chained log; an edited row is caught and named (R6)." },
  { row: "When something goes wrong", old: "It depends who notices.",
    now: "The hold stays on and a person reviews: errors, timeouts and unclear answers all fail closed (R7)." },
];

export default function HowItWorks() {
  return (
    <div className="public story">
      <PublicHeader links={[{ href: "#stations", label: "The pipeline" }, { href: "#demo", label: "Demo data" }, { href: "/corpus", label: "Frozen test cases" }]} />
      <section className="hero" aria-labelledby="hero-h">
        <Rings className="rings hero-rings" />
        <div className="hero-text">
          <p className="eyebrow">For accounts payable teams on Ramp</p>
          <h1 id="hero-h">A changed bank account gets paid only after the real vendor says so.</h1>
          <p className="lead">
            When a vendor&apos;s bank details change in Ramp, Dialback holds their payments, calls the vendor on a phone number it can
            prove is theirs, lets code decide what the answers mean, and waits for a second person to release.
          </p>
          <p className="story-cta">
            <Link className="button-link primary" href="/tour/1">Take the guided demo</Link>
            <a className="button-link" href="#stations">See the pipeline</a>
          </p>
        </div>
      </section>
      <main className="story-main">
        <section className="story-section hero-shot-section" aria-label="The dashboard, drawn">
          <HeroShot />
        </section>

        <section className="story-section" aria-labelledby="problem-h">
          <h2 id="problem-h">The problem: vendor email fraud</h2>
          <p className="muted story-intro">
            The fraud doesn&apos;t break into anything at your company. It takes over one of your vendors, and asks politely.
          </p>
          <ol className="scam">
            {SCAM.map((s, i) => (
              <li key={s.title} className="scam-step">
                <span className="scam-n" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
          <p className="muted sub">
            The usual advice is the callback in step 3: phone the vendor on a number you already trust. Dialback makes that call
            for you, every time, on a number it can prove.
          </p>
        </section>

        <section className="story-section" aria-labelledby="compare-h">
          <h2 id="compare-h">The old way and Dialback, side by side</h2>
          <div className="table-wrap card flush">
            <table className="compare">
              <thead><tr><th scope="col"><span className="sr-only">Step</span></th><th scope="col">The old way</th><th scope="col">With Dialback</th></tr></thead>
              <tbody>
                {COMPARE.map((r) => (
                  <tr key={r.row}><th scope="row">{r.row}</th><td className="muted">{r.old}</td><td><RuleText text={r.now} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="story-section" id="stations" aria-labelledby="stations-h">
          <h2 id="stations-h">Seven stations, and what each one stops</h2>
          <p className="muted story-intro">Every bank change goes through the same stations, in this order. Inside the dashboard, each case's rail shows where it stands: hold, call, decision, sign-off and release.</p>
          <p className="muted sub shot-caption">The pictures are drawings of the dashboard, filled with the demo company&apos;s fictional data and the product&apos;s own wording. They are not a live case.</p>
          <ol className="pipeline">
            {STORY_STATIONS.map((s, i) => (
              <li key={s.key} className="pipe-stop">
                <span className="pipe-node"><Icon name={ICON[s.key]} size={20} /></span>
                <div className="pipe-pair">
                  <div className="pipe-text">
                    <h3><span className="pipe-n">{i + 1}</span> {s.label}{s.rules ? <span className="badge tone-muted">{s.rules}</span> : null}</h3>
                    <p>{s.does}</p>
                    <p className="pipe-stops"><strong>Stops:</strong> {s.stops}</p>
                  </div>
                  <StationShot station={s.key} />
                </div>
              </li>
            ))}
          </ol>
          <h3 className="story-sub">Under every station</h3>
          <ul className="across">
            {ACROSS.map((a) => (
              <li key={a.title} className="panel">
                <h3>{a.title} <span className="badge tone-muted">{a.rules}</span></h3>
                <p className="muted">{a.text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="story-section" aria-labelledby="real-h">
          <h2 id="real-h">What is real today</h2>
          <div className="real">
            <div className="panel">
              <h3>Built and running</h3>
              <ul>
                <li>The whole pipeline above, against a Ramp-shaped mock of Ramp&apos;s API, with fictional vendors and accounts.</li>
                <li>The Trust Ledger&apos;s rule and the disclosed voice agent are built. A demo run can still use stand-ins for them (the rehearsal stack picks a fixed number and plays a scripted vendor), depending on how it is deployed; the case page shows what each call recorded.</li>
                <li>The hold, the state machine, the four-eyes release and the evidence chain, enforced in Postgres.</li>
                <li>The number-picking rule, checked against 20 frozen cases in the repo: 14 planted attacks, 1 stated known limit and 5 legitimate controls. <Link href="/corpus">See each case and what the rule did</Link>.</li>
              </ul>
            </div>
            <div className="panel">
              <h3>Not built yet</h3>
              <ul>
                <li>The attack lab that would replay hundreds of scams and score the whole system. It is a plan in the repo, so there is <Link href="/scorecard">no scorecard</Link> to show.</li>
                <li>Ramp&apos;s own sandbox: everything runs against the mock.</li>
              </ul>
            </div>
          </div>
        </section>

        <section className="story-section" id="demo" aria-labelledby="demo-h">
          <h2 id="demo-h">See it on demo data</h2>
          <p className="muted story-intro">
            The demo company has three vendors whose bank details change. Each is set up to show a different ending. On the deployed
            demo, the case page shows what actually happened on its own run, which can differ from the story below.
          </p>
          <ul className="demo-stories">
            {DEMO_STORIES.map((s) => (
              <li key={s.ref} className="card">
                <div className="card-body">
                  <p className="tags"><span className="badge tone-muted">{s.title}</span></p>
                  <h3>{s.legal_name}</h3>
                  <p className="muted">{s.story}</p>
                  <Link className="button-link" href={`/demo/${s.ref}`}>Open this case</Link>
                </div>
              </li>
            ))}
          </ul>
          <p className="muted sub">
            Opening a case asks you to sign in; the sign-in page offers shared demo accounts, a clerk and an approver, that work only on
            demo data. Everything in the demo is fictional: the company, the vendors, the accounts, the numbers.
          </p>
          <p><Link className="button-link primary" href="/app">Open the dashboard</Link></p>
        </section>
      </main>
      <footer className="pub-foot">
        <RingMotif />
        <span className="brand"><BrandMark />Dialback</span>
        <nav aria-label="Footer">
          <a href="#stations">The pipeline</a>
          <Link href="/corpus">Frozen test cases</Link>
          <Link href="/scorecard">Scorecard</Link>
          <Link href="/app">Open the dashboard</Link>
        </nav>
      </footer>
    </div>
  );
}

/** A sentence with its rule ids, "(R1)", set in mono. */
function RuleText({ text }: { text: string }) {
  return <>{text.split(/(R\d)/).map((part, i) => (/^R\d$/.test(part) ? <span key={i} className="mono rule-ref">{part}</span> : part))}</>;
}
