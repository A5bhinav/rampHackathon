import { CardHead, Stats } from "./page";
import { FUZZ_COLUMNS, LAB, declaredCount, failures, fuzzCells, fuzzLegend, fuzzPaths, tile, voiceGroups } from "@/lib/lab";

// The attack lab's results (spec 0101), shared by the public /scorecard page and Control's Assurance
// page. Static: every result was written by `lab snapshot` running the real lab; this only lays it out.

export function LabResults() {
  const { voice, fuzz, commit } = LAB;
  const cells = fuzzCells(fuzz);
  const flagged = cells.filter((c) => c !== "clean").length;
  return (
    <>
      <Stats label="Lab results" items={[
        { label: "Scripted calls that passed", value: `${voice.passed} of ${voice.total}`, tone: voice.passed === voice.total ? "ok" : "warn",
          sub: `${declaredCount(voice.scenarios)} rest on a fact the script supplies` },
        { label: "Generated worlds tried", value: fuzz.worlds.toLocaleString("en-US") },
        { label: "Worlds a check flagged", value: flagged.toLocaleString("en-US"), tone: flagged ? "warn" : "ok" },
        { label: "Ran on commit", value: <span className="lab-commit">{commit}</span> },
      ]} />
      <VoiceGrid />
      <FuzzMap />
    </>
  );
}

function VoiceGrid() {
  const { scenarios } = LAB.voice;
  const failed = failures(scenarios);
  return (
    <section className="card" aria-labelledby="lab-voice-h">
      <CardHead marker="Calls" title="Every scripted call, and how it ended" id="lab-voice-h" />
      <div className="card-body">
        {voiceGroups(scenarios).map((g) => (
          <div className="lab-row" key={g.key}>
            <h3>{g.title} <span className="count">{g.items.length}</span></h3>
            <ul className="lab-tiles">
              {g.items.map((s) => {
                const t = tile(s);
                return <li key={s.id} className={`lab-tile tone-${t.tone}${t.failed ? " is-failed" : ""}${t.declared ? " is-declared" : ""}`} title={t.label} aria-label={t.label}>{t.letter}{t.declared ? "*" : ""}</li>;
              })}
            </ul>
          </div>
        ))}
        <p className="lab-key muted sub">
          <span className="lab-tile tone-ok" aria-hidden="true">V</span> Verified
          <span className="lab-tile tone-warn" aria-hidden="true">R</span> Sent to a person
          <span className="lab-tile tone-info" aria-hidden="true">B</span> Blocked
          <span className="lab-tile is-failed" aria-hidden="true" /> Didn&apos;t end as the lab required
          <span className="lab-tile is-declared" aria-hidden="true">*</span> Rests on a fact the script supplies, because production doesn&apos;t compute it yet
        </p>
        {failed.length ? (
          <div className="lab-failures">
            <h3>What the lab caught</h3>
            <ul className="plain">{failed.map((f) => <li key={f.id}>{f.sentence}</li>)}</ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function FuzzMap() {
  const { fuzz } = LAB;
  const cells = fuzzCells(fuzz);
  const { paths, rows, width, pitch } = fuzzPaths(cells, FUZZ_COLUMNS);
  const legend = fuzzLegend(fuzz);
  const summary = `${fuzz.worlds} generated worlds: ${legend.map((l) => `${l.count} ${l.words}`).join("; ")}.`;
  return (
    <section className="card" aria-labelledby="lab-fuzz-h">
      <CardHead marker="Fuzz" title="Generated worlds against the number-picking rule" id="lab-fuzz-h" />
      <div className="card-body">
        <p className="muted">Each square is one randomly drawn, attacker-shaped set of inputs to the rule, in the order it was drawn (seed {fuzz.seed}). Many
          carry planted numbers and some use non-default settings, so the flagged share is not a real-world rate.</p>
        <svg className="lab-map" viewBox={`0 0 ${width} ${rows * pitch}`} role="img" aria-label={summary}>
          <path className="cell-clean" d={paths.clean} />
          <path className="cell-warn" d={paths.warn} />
          <path className="cell-bad" d={paths.bad} />
        </svg>
        <ul className="lab-legend plain">
          {legend.map((l) => (
            <li key={l.words}><span className={`lab-swatch cell-${l.tone}`} aria-hidden="true" /><strong>{l.count.toLocaleString("en-US")}</strong> {l.words}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
