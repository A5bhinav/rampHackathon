// The phone demo (spec 0065): the live call's view for each shape the API sends, its route
// handler, flow mode's columns, and where the entry points appear. `pnpm test`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import type { CaseDetail, SelectionView } from "../src/lib/api-types.ts";
import { POLL_STATES, callLength, callStateResponse, canWatchCall, flowColumns, liveCallView, phoneLines, presentLinks } from "../src/lib/live-call.ts";

const selection = { selection: JSON.parse(readFileSync(new URL("../../../packages/contracts/examples/selection.picked.json", import.meta.url), "utf8")),
  reason_text: {}, notes: {} } as unknown as SelectionView;

const SCRIPT = {
  disclosure: "Hi, this is an AI assistant on a recorded line.",
  questions: [
    { slot: "recording_consent", wording: "Is it okay to continue on this recorded line?" },
    { slot: "company_named", wording: "Could you tell me the name of the company I've reached?" },
    { slot: "speaker_name", wording: "May I have your name and role?" },
    { slot: "account_last4", wording: "What are the last four digits?" },
  ],
  note: "Dialback keeps no transcript.",
};
let seq = 0;
const tl = (type: string, to_state: string, summary = `${type} happened`) =>
  ({ seq: ++seq, type, to_state, from_state: null, version: 1, summary, hash: `h${seq}`.padEnd(64, "0"),
    occurred_at: new Date(Date.UTC(2026, 10, 9, 14, 0) + seq * 1000).toISOString(), actor: { kind: "system", id: null, display_name: "Dialback" } });
const answer = (slot: string, value: string | null, turn: number, extra: object = {}) =>
  ({ slot, value, digit_count: null, turn_id: turn, capture_method: "speech", captured_at: "2026-11-09T14:20:00Z", feeds: [], ...extra });
const call = (over: object = {}) => ({
  call_id: "call-1", attempt: 1, started_at: "2026-11-09T14:10:00Z", ended_at: null, result: null, reason: null, answered_by: null,
  direction: "outbound", dialed_number_is_pick: true, provider: "text", agent_version: "a1", last_four_result: null, answers: [], checks: null, ...over,
});
const detail = (state: string, over: Partial<CaseDetail> = {}): CaseDetail => ({
  case: { id: "c1", dataset_id: "d1", vendor: { id: "v1", legal_name: "Acme Fasteners LLC" }, state, outcome: null, version: 1,
    opened_at: "2026-11-09T14:00:00Z", updated_at: "2026-11-09T14:00:00Z", deadline_at: null, hold_status: "confirmed", bills_held: 2,
    next_payment_date: "2026-11-12", unknown_changer: false },
  entered_by: null, reviewed_by: null, change: {} as never, hold: null, decision: null, releases: [],
  timeline: [tl("change_detected", "open"), tl("hold_confirmed", "held")],
  chain: { ok: true, checked: 3, first_bad_seq: null, detail: "" } as never,
  permissions: { can_approve: false, can_reject: false, can_review: false, reasons: [] },
  calls: [], call_script: SCRIPT, ...over,
} as unknown as CaseDetail);

test("no call yet: choosing a number, then dialing the pick", () => {
  const v = liveCallView(detail("held"), selection);
  assert.equal(v.phase, "choosing");
  assert.equal(v.status, "Choosing a number");
  assert.ok(v.slots.every((s) => s.answer === null));
  const d = liveCallView(detail("calling"), selection);
  assert.equal(d.phase, "dialing");
  assert.equal(d.dialed, "(415) 555-0142");
  assert.equal(d.status, "Dialing (415) 555-0142");
});

test("on the call: the timer starts at started_at and the slots wait for the end", () => {
  const v = liveCallView(detail("calling", { calls: [call()] as never }), selection);
  assert.equal(v.phase, "on_call");
  assert.equal(v.status, "On the call");
  assert.equal(v.startedAt, "2026-11-09T14:10:00Z");
  assert.equal(v.endedAt, null);
  assert.equal(v.waiting, "Answers arrive when the call ends.");
  assert.equal(v.attempt, 1);
  assert.equal(v.disclosure, SCRIPT.disclosure);
});

test("ended with answers: slots fill in turn order, last four as a count and a word, checks appear", () => {
  const ended = call({
    ended_at: "2026-11-09T14:12:00Z", result: "answers_recorded", last_four_result: "match",
    answers: [answer("company_named", "Acme Fasteners", 3), answer("recording_consent", "yes", 2),
      answer("speaker_name", "Jordan Lee", 5), answer("speaker_role", "AR specialist", 5),
      answer("account_last4", null, 9, { digit_count: 4, capture_method: "keypad" })],
    checks: { disclosure_first: true, answers_grounded: true, agent_spoke_account_digits: false, new_number_accepted: false, policy_violations: 0, speech_guard_trips: 0 },
  });
  const v = liveCallView(detail("deciding", { calls: [ended] as never }), selection);
  assert.equal(v.phase, "answers");
  assert.deepEqual(v.slots.map((s) => s.answer), ["Yes", "Acme Fasteners", "Jordan Lee · AR specialist", "4 digits given · match"]);
  assert.deepEqual(v.slots.map((s) => s.order), [0, 1, 2, 3]);
  assert.equal(v.endedAt, "2026-11-09T14:12:00Z");
  assert.equal(v.checks.length, 6);
  assert.ok(v.checks.every((c) => c.ok));
});

test("a script that asks the role on its own keeps it out of the name's slot", () => {
  const script = { ...SCRIPT, questions: [{ slot: "speaker_name", wording: "May I have your name?" }, { slot: "speaker_role", wording: "And your role?" }] };
  const ended = call({ result: "answers_recorded", answers: [answer("speaker_name", "Jordan Lee", 5), answer("speaker_role", "AR manager", 5)] });
  const v = liveCallView(detail("deciding", { calls: [ended] as never, call_script: script as never }), selection);
  assert.deepEqual(v.slots.map((s) => s.answer), ["Jordan Lee", "AR manager"]);
});

test("no digits for the last four, even if a value carried some", () => {
  const leaky = call({ result: "answers_recorded", answers: [answer("account_last4", "4821", 9, { digit_count: 4 })] });
  const v = liveCallView(detail("deciding", { calls: [leaky] as never }), selection);
  const text = JSON.stringify(v.slots);
  assert.ok(!text.includes("4821"));
  assert.match(v.slots[3].answer ?? "", /^4 digits given/);
});

test("not connected: the core's reason, and the retry while the case waits for it", () => {
  const missed = call({ result: "no_answer", reason: "No one answered; Dialback will try again in the calling window.", ended_at: "2026-11-09T14:11:00Z" });
  const v = liveCallView(detail("retry_wait", { calls: [missed] as never }), selection);
  assert.equal(v.phase, "not_connected");
  assert.equal(v.reason, missed.reason);
  assert.equal(v.status, "Retry scheduled");
  const second = liveCallView(detail("calling", { calls: [missed, call({ call_id: "call-2", attempt: 2 })] as never }), selection);
  assert.equal(second.attempt, 2);
  assert.equal(second.phase, "on_call");
});

test("decided: the outcome and the core's effect", () => {
  const decision = { outcome: "verified", effect: "The hold stays on until an approver releases it.", rules_fired: [], explanation: [],
    engine_version: "decision-1.0.0", last_four_result: "match", decided_at: "2026-11-09T14:13:00Z" };
  const v = liveCallView(detail("awaiting_release", { decision: decision as never, calls: [call({ result: "answers_recorded" })] as never }), selection);
  assert.equal(v.phase, "decided");
  assert.equal(v.outcome, "verified");
  assert.equal(v.effect, decision.effect);
  assert.equal(v.poll, false);
});

test("no call and nothing pending: the core's last timeline sentence", () => {
  const d = detail("needs_review", { timeline: [tl("change_detected", "open"), tl("hold_confirmed", "held"),
    tl("no_provable_number", "needs_review", "The Trust Ledger found no provable number.")] as never });
  const v = liveCallView(d, null);
  assert.equal(v.phase, "no_call");
  assert.equal(v.lastStep, "The Trust Ledger found no provable number.");
  assert.equal(v.dialed, null);
});

test("it polls only while code is working", () => {
  assert.deepEqual([...POLL_STATES].sort(), ["calling", "deciding", "held", "open", "retry_wait"]);
  assert.equal(liveCallView(detail("calling"), selection).poll, true);
  assert.equal(liveCallView(detail("blocked"), null).poll, false);
});

test("the route handler: 401 signed out, 404 unknown, selection only when asked, no token in the body", async () => {
  const deps = (token: string | null, fail?: number) => ({
    token: async () => token,
    getCase: async () => { if (fail) throw Object.assign(new Error("x"), { status: fail }); return detail("calling"); },
    getSelection: async () => selection,
  });
  assert.equal((await callStateResponse(deps(null), "c1", false)).status, 401);
  assert.equal((await callStateResponse(deps("tok-secret", 404), "c1", false)).status, 404);
  const ok = await callStateResponse(deps("tok-secret"), "c1", true);
  assert.equal(ok.status, 200);
  assert.equal(ok.body.dialed, "(415) 555-0142");
  assert.ok(!JSON.stringify(ok.body).includes("tok-secret"));
  const lean = await callStateResponse(deps("tok-secret"), "c1", false);
  assert.equal(lean.body.dialed, null, "the client keeps the number it already has");
});

test("flow mode: seven columns, each with its event's time, the core's summary and the short hash", () => {
  const d = detail("calling", { timeline: [tl("change_detected", "open", "A change was detected."), tl("hold_confirmed", "held", "Held in Ramp."),
    tl("number_resolved", "calling", "The Trust Ledger found a number.")] as never });
  const cols = flowColumns(d.timeline, "calling", null);
  assert.equal(cols.length, 7);
  assert.equal(cols[1].summary, "Held in Ramp.");
  assert.equal(cols[1].hash, d.timeline[1].hash.slice(0, 12));
  assert.equal(cols[1].status, "done");
  assert.equal(cols[3].status, "current");
  assert.equal(cols[3].summary, null);
});

test("entry points: Watch the call once a call is placed (spec 0066); Present only on demo data with a case", () => {
  assert.equal(canWatchCall([tl("change_detected", "open"), tl("hold_confirmed", "held")] as never), false);
  assert.equal(canWatchCall([tl("change_detected", "open"), tl("number_resolved", "calling")] as never), false, "a number alone isn't a call");
  assert.equal(canWatchCall([tl("change_detected", "open"), tl("number_resolved", "calling"), tl("call_started", "calling")] as never), true);
  assert.equal(canWatchCall([tl("call_started", "calling"), tl("change_detected", "held")] as never), false, "a new change starts over");
  const cases = [{ id: "old", opened_at: "2026-11-01T00:00:00Z" }, { id: "new", opened_at: "2026-11-09T00:00:00Z" }];
  assert.deepEqual(presentLinks("lab", cases, "d1"), []);
  assert.deepEqual(presentLinks("demo", [], "d1"), []);
  assert.deepEqual(presentLinks("demo", cases, "d1").map((l) => [l.label, l.href]),
    [["Live call", "/d/d1/cases/new/call"], ["Flow mode", "/d/d1/cases/new/flow"]]);
});

// From the 0065 review.
test("calls from an earlier version are not this version's call", () => {
  const old = call({ call_id: "old", attempt: 2, started_at: "2026-11-09T13:00:00Z", result: "no_answer" });
  const midCall = call({ call_id: "cut", attempt: 1, started_at: "2026-11-09T13:30:00Z", result: null });
  const d = detail("held", { calls: [old, midCall] as never,
    timeline: [tl("change_detected", "open"), tl("hold_confirmed", "held")] as never });  // this version began at 14:xx
  const v = liveCallView(d, selection);
  assert.equal(v.phase, "choosing", "an old or cut-off call never shows as the current one");
  assert.equal(v.attempt, null);
  assert.equal(v.poll, true);
});

test("a retry being dialed shows dialing, not the last failure", () => {
  const missed = call({ result: "voicemail", reason: "Voicemail picked up.", ended_at: "2026-11-09T14:11:00Z" });
  const v = liveCallView(detail("calling", { calls: [missed] as never }), selection);
  assert.equal(v.phase, "dialing");
  assert.equal(v.attempt, null, "the next attempt's number isn't known yet");
});

test("not connected covers every non-answer result; blocked with no call shows the core's sentence", () => {
  for (const result of ["voicemail", "busy", "failed", "machine", "declined", "callback_requested", "timed_out"]) {
    const v = liveCallView(detail("needs_review", { calls: [call({ result, reason: "The core's reason." })] as never }), selection);
    assert.equal(v.phase, "not_connected", result);
    assert.equal(v.reason, "The core's reason.");
  }
  const blocked = liveCallView(detail("blocked", { timeline: [tl("change_detected", "open"), tl("hold_failed", "needs_review", "Ramp did not confirm the hold."),
    tl("review_blocked", "blocked", "A reviewer blocked the case.")] as never }), null);
  assert.equal(blocked.phase, "no_call");
  assert.equal(blocked.lastStep, "A reviewer blocked the case.");
});

test("the route: an expired session (core's redirect) is 401, not 502; 422 is 404", async () => {
  const redirect = Object.assign(new Error("NEXT_REDIRECT"), { digest: "NEXT_REDIRECT;replace;/sign-in?expired=1;307;" });
  const deps = (err: unknown) => ({ token: async () => "tok", getCase: async () => { throw err; }, getSelection: async () => selection });
  assert.equal((await callStateResponse(deps(redirect), "c1", false)).status, 401);
  assert.equal((await callStateResponse(deps(Object.assign(new Error("x"), { status: 422 })), "c1", false)).status, 404);
  assert.equal((await callStateResponse(deps(new Error("down")), "c1", false)).status, 502);
  const fine = { token: async () => "tok", getCase: async () => detail("calling"), getSelection: async () => selection };
  assert.equal((await callStateResponse(fine, "c1", false, "d1")).status, 200);
  assert.equal((await callStateResponse(fine, "c1", false, "another")).status, 404, "a case from another dataset");
});

test("the view carries the case version, so a client never keeps a number from an earlier change", () => {
  assert.equal(liveCallView(detail("calling"), selection).version, 1);
});

// Spec 0066, PR 2: an ended call shows its length, and the phone frame its status and whose number it is.
test("an ended call's length; nothing for a call that took no measurable time", () => {
  assert.equal(callLength("2026-11-10T09:48:18Z", "2026-11-10T09:49:36Z"), "01:18");
  assert.equal(callLength("2026-11-10T09:48:18Z", "2026-11-10T09:48:18.400Z"), null, "the text line: no 00:00");
  assert.equal(callLength("2026-11-10T09:48:18Z", null), null);
  assert.equal(callLength(null, "2026-11-10T09:48:18Z"), null);
});

test("the phone frame: the call's status, and that the number is the Trust Ledger's pick", () => {
  const v = (over: Record<string, unknown>) => ({ phase: "decided", status: "Decided", dialed: "(415) 555-0142", dialedIsPick: true,
    startedAt: "2026-11-10T09:48:18Z", endedAt: "2026-11-10T09:49:36Z", result: "answers_recorded", ...over }) as never;
  assert.deepEqual(phoneLines(v({})), { status: "Call ended · answers recorded · 01:18", number: "(415) 555-0142", pick: { tone: "ok", text: "The Trust Ledger's pick" } });
  assert.deepEqual(phoneLines(v({ endedAt: "2026-11-10T09:48:18Z" })).status, "Call ended · answers recorded");
  assert.deepEqual(phoneLines(v({ dialedIsPick: false })).pick, { tone: "bad", text: "Not the Trust Ledger's pick" });
  assert.equal(phoneLines(v({ dialedIsPick: false })).number, null, "never the pick's number over a call that didn't dial it");
  assert.equal(phoneLines(v({})).number, "(415) 555-0142");
  assert.deepEqual(phoneLines(v({ phase: "on_call", status: "On the call", endedAt: null, result: null, dialedIsPick: null })),
    { status: "On the call", number: "(415) 555-0142", pick: { tone: "ok", text: "The Trust Ledger's pick" } }, "before the report, the number shown is the pick itself");
  assert.equal(phoneLines(v({ dialed: null, phase: "choosing", status: "Choosing a number", endedAt: null, result: null })).pick, null);
});
