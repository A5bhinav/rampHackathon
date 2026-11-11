// The scorecard page (spec 0101): the file `lab snapshot` wrote by running the real attack lab. Nothing
// here runs the lab or judges a result; it only puts words and cells to what the run recorded.
import data from "../data/lab-results.json" with { type: "json" };

export type LabScenario = { id: string; class: string; attack: boolean; outcome: string; passed: boolean; rule: string | null };
export type LabFuzz = { seed: number; budget: number; worlds: number; hits: Record<string, number[]> };
export type LabResults = { generated_by: string; commit: string; voice: { total: number; passed: number; scenarios: LabScenario[] }; fuzz: LabFuzz };

export const LAB = data as LabResults;

type Tone = "ok" | "warn" | "info" | "muted";
const ENDINGS: Record<string, { letter: string; word: string; tone: Tone }> = {
  verified: { letter: "V", word: "Verified", tone: "ok" },
  needs_review: { letter: "R", word: "Sent to a person", tone: "warn" },
  not_verified: { letter: "B", word: "Blocked", tone: "info" },
};

export function ending(outcome: string): { letter: string; word: string; tone: Tone } {
  return ENDINGS[outcome] ?? { letter: "?", word: outcome, tone: "muted" };
}

/** "a02-lookalike-domain" reads as "lookalike domain". */
export function scenarioName(id: string): string {
  return id.replace(/^[a-z]\d+[a-z]?-/, "").replaceAll("-", " ");
}

export function tile(s: LabScenario) {
  const e = ending(s.outcome);
  const by = s.rule ? `, by ${s.rule}` : "";
  const label = `${scenarioName(s.id)}: ${e.word}${by}${s.passed ? "" : " (failed: not what the lab required)"}`;
  return { ...e, failed: !s.passed, label };
}

export function voiceGroups(scenarios: readonly LabScenario[]) {
  return [
    { key: "attack", title: "Attacks", items: scenarios.filter((s) => s.attack) },
    { key: "legit", title: "Legitimate but odd", items: scenarios.filter((s) => !s.attack) },
  ];
}

/** Every scenario that didn't end as the lab required, in words. */
export function failures(scenarios: readonly LabScenario[]) {
  return scenarios.filter((s) => !s.passed).map((s) => {
    const word = ending(s.outcome).word;
    const sentence = s.attack ? `${scenarioName(s.id)}: this attack ended ${word}.`
      : `${scenarioName(s.id)}: this legitimate-but-odd call ended ${word}, not what the lab required.`;
    return { id: s.id, sentence };
  });
}

export type Cell = "clean" | "warn" | "bad";

// What each check found. A label not listed here is new to this page, so it reads as red, with its own name.
const FUZZ_LABELS: Record<string, { words: string; tone: Cell }> = {
  M_incontext_label_moved_pick: { words: "A label in the change email moved the number we'd call (finding A1)", tone: "bad" },
  M_shared_id_helped: { words: "A latent defect that production data can't reach today (finding A5)", tone: "warn" },
};
const labelInfo = (label: string) => FUZZ_LABELS[label] ?? { words: label, tone: "bad" as const };

/** One cell per world, in search order. A world with two labels shows the worse one. */
export function fuzzCells(fuzz: LabFuzz): Cell[] {
  const cells: Cell[] = Array.from({ length: fuzz.worlds }, () => "clean");
  for (const [label, worlds] of Object.entries(fuzz.hits)) {
    const { tone } = labelInfo(label);
    for (const w of worlds) if (cells[w] !== "bad") cells[w] = tone;
  }
  return cells;
}

export function fuzzLegend(fuzz: LabFuzz): { tone: Cell; words: string; count: number }[] {
  const flagged = new Set(Object.values(fuzz.hits).flat());
  return [
    ...Object.entries(fuzz.hits).map(([label, worlds]) => ({ ...labelInfo(label), count: worlds.length })),
    { tone: "clean" as const, words: "No check flagged it", count: fuzz.worlds - flagged.size },
  ];
}

export const FUZZ_COLUMNS = 58;
const CELL = 9, PITCH = 10;

/** The cells as one SVG path per tone, so the map is three elements and not thousands. */
export function fuzzPaths(cells: readonly Cell[], columns: number = FUZZ_COLUMNS) {
  const parts: Record<Cell, string[]> = { clean: [], warn: [], bad: [] };
  cells.forEach((cell, i) => {
    parts[cell].push(`M${(i % columns) * PITCH} ${Math.floor(i / columns) * PITCH}h${CELL}v${CELL}h-${CELL}z`);
  });
  return {
    paths: { clean: parts.clean.join(""), warn: parts.warn.join(""), bad: parts.bad.join("") },
    rows: Math.ceil(cells.length / columns), width: columns * PITCH, pitch: PITCH,
  };
}
