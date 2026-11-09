// The case page's call view (spec 0036): display only, from what the core sends. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { answerText, checkRows, conversation, ruleChips } from "../src/lib/call.ts";

const a = (slot: string, value: string | null, turn: number, extra: Record<string, unknown> = {}) =>
  ({ slot, value, digit_count: null, turn_id: turn, capture_method: "speech", captured_at: "", feeds: [], ...extra }) as never;

test("the last four digits are only ever a count, refused or didn't know", () => {
  assert.equal(answerText(a("account_last4", null, 9, { digit_count: 4, capture_method: "keypad" })), "4 digits given");
  assert.equal(answerText(a("account_last4", null, 9, { digit_count: 1 })), "1 digit given");
  assert.equal(answerText(a("account_last4", "refused", 9)), "Refused to say");
  assert.equal(answerText(a("account_last4", "dont_know", 9)), "Didn't know");
  assert.equal(answerText(a("account_last4", "1234", 9)), "No digits recorded", "a value that isn't a count or a refusal is never shown");
  assert.equal(answerText(a("account_last4", null, 9)), "No digits recorded");
  assert.equal(answerText(a("recording_consent", "yes", 1)), "Yes");
  assert.equal(answerText(a("change_requested", "unsure", 7)), "Not sure");
  assert.equal(answerText(a("company_named", "Acme Fasteners", 3)), "Acme Fasteners");
  assert.equal(answerText(a("flag", "pressure_to_approve", 8)), "Pressed for approval");
});

const decision = {
  checklist: [
    { id: "V07_right_person_confirmed", sentence: "s7", status: "passed" },
    { id: "V08_digits_match", sentence: "s8", status: "not_met" },
  ],
  fired: [{ id: "NV01_vendor_denied_change", sentence: "n1", block: "not_verified" }],
} as never;

test("chips name only rules this decision reports on, each with the core's status word", () => {
  const chips = ruleChips(["V07_right_person_confirmed", "NV01_vendor_denied_change", "NR09_change_unsure", "V08_digits_match"], decision);
  assert.deepEqual(chips, [
    { id: "V07_right_person_confirmed", label: "V07", status: "passed", tone: "ok" },
    { id: "NV01_vendor_denied_change", label: "NV01", status: "fired", tone: "bad" },
    { id: "V08_digits_match", label: "V08", status: "not met", tone: "warn" },
  ]);
  assert.deepEqual(ruleChips(["V07_right_person_confirmed"], null), [], "no decision: no chips");
  const unchecked = { checklist: [{ id: "V07_right_person_confirmed", sentence: "s", status: "not_checked" }], fired: [] } as never;
  assert.deepEqual(ruleChips(["V07_right_person_confirmed"], unchecked), [], "a condition never run gets no chip");
});

test("the conversation follows the script; answers sit under their question, extras after", () => {
  const script = { disclosure: "Hi", note: "n", questions: [
    { slot: "recording_consent", wording: "ok?" }, { slot: "company_named", wording: "which?" }, { slot: "speaker_name", wording: "who?" },
    { slot: "account_last4", wording: "digits?" }] };
  const turns = conversation(script as never, [a("company_named", "Acme", 3), a("flag", "opt_out", 6), a("recording_consent", "yes", 1),
    a("speaker_role", "AR manager", 5), a("speaker_name", "Jordan", 4)]);
  assert.deepEqual(turns.asked.map((q) => [q.slot, q.answers.map((x) => (x as { turn_id: number }).turn_id)]),
    [["recording_consent", [1]], ["company_named", [3]], ["speaker_name", [4, 5]], ["account_last4", []]]);
  assert.deepEqual(turns.extra.map((x) => (x as { slot: string }).slot), ["flag"]);
});

test("script checks read as words, and only a clean value is ok", () => {
  const rows = checkRows({ disclosure_first: true, answers_grounded: false, agent_spoke_account_digits: false,
    new_number_accepted: true, policy_violations: 0, speech_guard_trips: 2 });
  assert.deepEqual(rows.map((r) => [r.label, r.word, r.ok]), [
    ["AI and recording disclosed first", "yes", true],
    ["Every answer is in the turn it cites", "no", false],
    ["The agent never said account digits", "never said", true],
    ["No new phone number accepted", "a new number was accepted", false],
    ["Policy violations", "none", true],
    ["Speech-guard trips", "2", false],
  ]);
  assert.deepEqual(checkRows(null), []);
});
