// Groups a decision's rules for people to read (spec 0006). The core sends `rules_fired` and one
// templated sentence per rule in the same order (`explanation`); the rule ids are the engine's
// permanent ids, so the group follows from the id alone. The sentences are never rewritten here.

export type RuleGroup = { key: string; title: string; tone: "ok" | "warn" | "bad" | "muted"; rules: { id: string; sentence: string }[] };

// The titles say what the rules are, not where a fact came from: a condition can be a Trust
// Ledger fact (V01) or a check run after the call (V08, V09), not only something said on it.
const GROUPS: { key: string; title: string; tone: RuleGroup["tone"]; test: (id: string) => boolean }[] = [
  { key: "not_verified", title: "Not verified because", tone: "bad", test: (id) => id.startsWith("NV") },
  { key: "review", title: "Sent to a person because", tone: "warn", test: (id) => /^NR\d\d_/.test(id) && !id.startsWith("NR99") },
  { key: "unmet", title: "Conditions not met", tone: "warn", test: (id) => id.startsWith("NR99") || id.endsWith("_unmet") },
  // The skeleton engine adds U01-U03 to every verified decision: facts no slice supplies yet.
  { key: "unchecked", title: "Not checked by this engine version", tone: "warn", test: (id) => /^U\d\d_/.test(id) },
  { key: "met", title: "Conditions met", tone: "ok", test: (id) => /^(V|S)\d\d_/.test(id) },
];

export function groupRules(ids: readonly string[], sentences: readonly string[]): RuleGroup[] | null {
  if (ids.length !== sentences.length) return null;  // a retired id without a sentence: show the plain list
  const groups: RuleGroup[] = [];
  ids.forEach((id, i) => {
    const g = GROUPS.find((x) => x.test(id)) ?? { key: "other", title: "Recorded by an earlier engine", tone: "muted" as const };
    let group = groups.find((x) => x.key === g.key);
    if (!group) groups.push((group = { key: g.key, title: g.title, tone: g.tone, rules: [] }));
    group.rules.push({ id, sentence: sentences[i] });
  });
  return groups;
}
