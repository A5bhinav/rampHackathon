// The dashboard's pure helpers (spec 0009). Node runs these directly: `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ago, dueLabel, relative } from "../src/lib/format.ts";
import { groupRules } from "../src/lib/rules.ts";

const NOW = new Date("2026-11-03T23:30:00Z");

test("dueLabel counts UTC days from the dataset's clock", () => {
  assert.deepEqual(dueLabel("2026-11-03", NOW), { text: "due today", soon: true });
  assert.deepEqual(dueLabel("2026-11-04", NOW), { text: "due in 1 day", soon: true });
  assert.deepEqual(dueLabel("2026-11-06", NOW), { text: "due in 3 days", soon: true });
  assert.deepEqual(dueLabel("2026-11-07", NOW), { text: "due in 4 days", soon: false });
  assert.deepEqual(dueLabel("2026-11-02", NOW), { text: "1 day overdue", soon: true });
  assert.deepEqual(dueLabel("2026-10-31", NOW), { text: "3 days overdue", soon: true });
  assert.equal(dueLabel(null, NOW), null);
});

test("relative uses the largest whole unit, and ago never looks forward", () => {
  assert.equal(relative("2026-11-05T23:30:00Z", NOW), "in 2 days");
  assert.equal(relative("2026-11-03T18:00:00Z", NOW), "5 hours ago");
  assert.equal(relative("2026-11-03T23:30:10Z", NOW), "in 1 minute");
  assert.equal(ago("2026-11-03T18:00:00Z", NOW), "5 hours ago");
  assert.equal(ago("2027-08-30T00:00:00Z", NOW), "", "a lab clock set before the case opened shows nothing");
});

test("rules are grouped by their permanent ids", () => {
  const ids = ["NV01_vendor_denied_change", "NR12_asked_what_is_checked", "NR99_conditions_not_met", "V08_digits_match_unmet",
    "U01_speaker_in_thread", "V01_number_eligible", "S01_known_contact", "X42_retired"];
  const groups = groupRules(ids, ids.map((id) => `sentence for ${id}`))!;
  assert.deepEqual(groups.map((g) => [g.key, g.tone, g.rules.map((r) => r.id)]), [
    ["not_verified", "bad", ["NV01_vendor_denied_change"]],
    ["review", "warn", ["NR12_asked_what_is_checked"]],
    ["unmet", "warn", ["NR99_conditions_not_met", "V08_digits_match_unmet"]],
    ["unchecked", "warn", ["U01_speaker_in_thread"]],
    ["met", "ok", ["V01_number_eligible", "S01_known_contact"]],
    ["other", "muted", ["X42_retired"]],
  ]);
  assert.equal(groups[2].rules[1].sentence, "sentence for V08_digits_match_unmet", "sentences are passed through unchanged");
});

test("a sentence missing for a rule falls back to the plain list", () => {
  assert.equal(groupRules(["V01_number_eligible", "V02_outbound_dial"], ["one sentence"]), null);
});
