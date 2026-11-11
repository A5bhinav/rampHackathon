// The case page's collapsed cards (spec 0066, PR 2). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { attemptRows, changeLine, checkCount, decisionLine, holdLine, numberLine, openCard, trailLine } from "../src/lib/case-cards.ts";
import { stations } from "../src/lib/stations.ts";

const ev = (type: string, to_state: string | null = null, occurred_at = "2026-11-11T00:50:00Z") => ({ type, to_state, occurred_at }) as never;
const base = [ev("change_detected", "open"), ev("hold_confirmed", "held"), ev("number_resolved", "calling")];

test("the current station's card starts open; a settled case opens its decision", () => {
  assert.equal(openCard(stations([ev("change_detected", "open")], "open", null)), "hold");
  assert.equal(openCard(stations(base, "calling", null)), "call");
  assert.equal(openCard(stations([...base, ev("call_ended", "deciding"), ev("decision_made", "awaiting_release")], "awaiting_release", "verified")), "decision");
  assert.equal(openCard(stations([...base, ev("call_ended", "deciding"), ev("decision_made", "blocked")], "blocked", "not_verified")), "decision");
  assert.equal(openCard(stations([...base, ev("call_timeout", "needs_review")], "needs_review", null)), "call", "no person reached: the call is where it stopped");
  assert.equal(openCard(stations([ev("change_detected", "open"), ev("no_provable_number", "needs_review")], "needs_review", null)), "number");
});

const checklist = (statuses: string[]) => statuses.map((status, i) => ({ id: `V${String(i + 1).padStart(2, "0")}_x`, sentence: "s", status }));

test("the decision's headline: the outcome, then the checklist count, or the rules that decided it", () => {
  const verified = { outcome: "verified", rules_fired: ["V01_x"], checklist: checklist(Array(11).fill("passed")) } as never;
  assert.deepEqual(checkCount(verified), { passed: 11, total: 11, ran: true });
  assert.equal(decisionLine(verified, "Verified"), "Verified · 11 of 11 conditions passed");
  const nv = { outcome: "not_verified", rules_fired: ["NV02_digits_mismatch"], checklist: checklist(Array(11).fill("not_checked")) } as never;
  assert.equal(checkCount(nv).ran, false);
  assert.equal(decisionLine(nv, "Not verified"), "Not verified · NV02");
  assert.equal(decisionLine(null, ""), null);
});

const call = (attempt: number, result: string | null, answers = 0, started_at = `2026-11-11T00:5${attempt}:00Z`) =>
  ({ call_id: `c${attempt}`, attempt, result, started_at, ended_at: result ? started_at : null, answers: Array(answers).fill({}), dialed_number_is_pick: true }) as never;

test("only the attempt the decision read stays expanded; earlier ones are one line each", () => {
  const rows = attemptRows([call(1, "callback_requested"), call(2, "answers_recorded", 5)]);
  assert.deepEqual(rows.map((r) => r.expanded), [false, true]);
  assert.equal(rows[0].line, "Attempt 1 · callback requested · Nov 11, 00:51 UTC");
  assert.equal(rows[1].line, "Attempt 2 · answers recorded · the Trust Ledger's pick");
  // Earlier versions' calls are listed too and each version numbers from 1, so no "of N" (it read "1 of 3").
  assert.equal(attemptRows([call(1, "answers_recorded", 5), call(1, "answers_recorded", 5)])[1].line, "Attempt 1 · answers recorded · the Trust Ledger's pick");
  // A later attempt without answers doesn't take the place of the one the decision read.
  assert.deepEqual(attemptRows([call(1, "answers_recorded", 5), call(2, "no_answer")]).map((r) => r.expanded), [true, false]);
  // Nothing answered yet: the newest attempt is the one to show.
  assert.deepEqual(attemptRows([call(1, "no_answer"), call(2, null)]).map((r) => r.expanded), [false, true]);
  assert.match(attemptRows([call(1, null)])[0].line, /in progress/);
  assert.deepEqual(attemptRows([]), []);
});

test("the other cards' one-line summaries carry routing and last four only", () => {
  const change = { new_account: { routing_number: "026009593", last4: "0003" }, old_account: { routing_number: "121000248", last4: "0000" }, changed_by: { display_name: "Pat Clerk" } };
  assert.equal(changeLine(change as never), "026009593 ····0003, was 121000248 ····0000 · entered by Pat Clerk");
  assert.equal(changeLine({ ...change, old_account: null, changed_by: null } as never), "026009593 ····0003 · no known author");
  assert.equal(holdLine(null, 2), "Placing the hold");
  assert.equal(holdLine({ status: "confirmed", confirmed_at: "2026-11-11T00:50:00Z", released_at: null } as never, 2), "2 bills on hold · confirmed Nov 11, 00:50 UTC");
  assert.equal(holdLine({ status: "failed", confirmed_at: null, released_at: null } as never, 1), "Not confirmed in Ramp");
  assert.equal(holdLine({ status: "released", confirmed_at: "x", released_at: "2026-11-11T01:00:00Z" } as never, 1), "1 bill restored · Nov 11, 01:00 UTC");
  assert.equal(trailLine(12, { ok: true } as never), "12 events · chain intact");
  assert.equal(trailLine(12, { ok: false, first_bad_seq: 7 } as never), "12 events · chain broken at #7");
});

test("the number card's line never prints the number", () => {
  const pick = { contact_point_id: "p", e164: "+14155550142", eligible: true, rank: 1, source_types: ["w9", "invoice_pdf"] };
  const view = (selection: unknown) => ({ selection, notes: {} }) as never;
  const line = numberLine(view({ outcome: "picked", contact_point_id: "p", candidates: [pick], rule_version: "ledger-select-v1" }));
  assert.equal(line, "The Trust Ledger's pick · 2 independent sources · ledger-select-v1");
  assert.ok(!line!.includes("555"));
  assert.equal(numberLine(view({ outcome: "no_provable_number", candidates: [], rule_version: "ledger-select-v1" })), "No provable number");
  assert.equal(numberLine(view(null)), "No selection recorded");
  assert.equal(numberLine(null), null);
});
