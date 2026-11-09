// Sign-in rules that don't need a browser or a Supabase project (spec 0010). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { MIN_NEW_PASSWORD, PASSWORD_RULE, SESSION_HOURS, Throttle, authMode, cleanEmail, cleanName, cleanNewPassword, cleanPassword, demoAccounts, sessionFresh, signedInAt, signUpNotice } from "../src/lib/auth-mode.ts";

const SUPABASE = { SUPABASE_URL: "https://abcdefghijklmnop.supabase.co", SUPABASE_PUBLISHABLE_KEY: "sb_publishable_x" };

test("the sign-in mode: dev only off production, Supabase when configured, else none", () => {
  assert.equal(authMode({ DIALBACK_AUTH_MODE: "dev" }), "dev");
  assert.equal(authMode({ DIALBACK_AUTH_MODE: "dev", VERCEL_ENV: "preview" }), "dev");
  assert.equal(authMode({ DIALBACK_AUTH_MODE: "dev", VERCEL_ENV: "production" }), "none", "dev sign-in never runs in production");
  assert.equal(authMode({ DIALBACK_AUTH_MODE: "dev", VERCEL_ENV: "production", ...SUPABASE }), "supabase");
  assert.equal(authMode({ ...SUPABASE }), "supabase");
  assert.equal(authMode({ SUPABASE_URL: SUPABASE.SUPABASE_URL }), "none", "both values are needed");
  assert.equal(authMode({}), "none");
});

test("a password is passed through as typed, and only a plausible one is sent", () => {
  assert.equal(cleanPassword("  correct horse battery staple "), "  correct horse battery staple ", "never trimmed or changed");
  for (const bad of ["", "x".repeat(300), null, undefined, 42, ["a"]]) {
    assert.equal(cleanPassword(bad), null, String(bad));
  }
});

test(`a sign-in lasts ${SESSION_HOURS} hours, measured from when the person signed in`, () => {
  const now = new Date("2026-11-19T18:00:00Z");
  assert.equal(SESSION_HOURS, 12);
  assert.equal(sessionFresh("2026-11-19T09:00:00.000Z", now), true);
  assert.equal(sessionFresh("2026-11-19T06:00:01.000Z", now), true);
  assert.equal(sessionFresh("2026-11-19T05:59:59.000Z", now), false, "past 12 hours: sign in again");
  assert.equal(sessionFresh("2026-11-19T18:00:30.000Z", now), true, "a little clock skew is fine");
  assert.equal(sessionFresh("2026-11-19T18:05:00.000Z", now), false, "a time in the future is not trusted");
  for (const bad of [undefined, "", "not a date", "1700000000"]) assert.equal(sessionFresh(bad, now), false, String(bad));
});

test("an email is trimmed and lowercased, and anything that isn't one is refused", () => {
  assert.equal(cleanEmail("  Riley.Approver@Example.COM "), "riley.approver@example.com");
  for (const bad of ["", "no-at-sign", "a@b", "two@@example.com", "spa ce@example.com", `${"x".repeat(250)}@example.com`, null, 42]) {
    assert.equal(cleanEmail(bad), null, String(bad));
  }
});

test("the sign-in time comes from the verified token's amr, the newest method wins", () => {
  assert.equal(signedInAt({ amr: [{ method: "password", timestamp: 1_763_560_800 }] }), "2025-11-19T14:00:00.000Z");
  assert.equal(signedInAt({ amr: [{ method: "password", timestamp: 100 }, { method: "otp", timestamp: 200 }] }), new Date(200_000).toISOString());
  for (const claims of [undefined, null, {}, { amr: "x" }, { amr: [] }, { amr: [{ method: "password" }] }, { amr: [{ timestamp: "1" }] }]) {
    assert.equal(signedInAt(claims), undefined, JSON.stringify(claims));
  }
});

test("the sign-in throttle allows a few tries per client per window", () => {
  let now = 0;
  const throttle = new Throttle(3, 60_000, () => now);
  assert.deepEqual([1, 2, 3, 4].map(() => throttle.allow("1.2.3.4")), [true, true, true, false]);
  assert.equal(throttle.allow("5.6.7.8"), true, "another client is counted apart");
  now += 60_001;
  assert.equal(throttle.allow("1.2.3.4"), true, "the window moves on");
});

// Self sign-up (spec 0027).

test("a new account's name is trimmed and 1 to 80 characters, without control characters", () => {
  assert.equal(cleanName("  Sam Clerk  "), "Sam Clerk");
  assert.equal(cleanName("Zoë O'Neil-Ng"), "Zoë O'Neil-Ng");
  for (const bad of ["", "   ", "x".repeat(81), "line\nbreak", "tab\there", null, undefined, 42]) {
    assert.equal(cleanName(bad), null, JSON.stringify(bad));
  }
});

test(`a new password is ${MIN_NEW_PASSWORD} to 72 characters with a lowercase, an uppercase, a digit and a symbol`, () => {
  // Spec 0033: the same rule Supabase enforces (password_required_characters), so the form never
  // lets through a password Supabase would refuse, and says why before it gets there.
  assert.equal(MIN_NEW_PASSWORD, 8);
  assert.equal(cleanNewPassword("Abcdef1!"), "Abcdef1!");
  assert.equal(cleanNewPassword(" Abcd1!x"), " Abcd1!x", "kept as typed, never trimmed");
  assert.equal(cleanNewPassword("Aa1" + "\\" + "xxxx"), "Aa1\\xxxx", "a backslash is one of Supabase's symbols");
  const long = "Aa1!" + "x".repeat(68);
  assert.equal(cleanNewPassword(long), long, "72 characters is the most");
  for (const bad of ["abcdef1!", "ABCDEF1!", "Abcdefg!", "Abcdefg1", "Ab1!", "Abcdef1 x", "Abcdef1€", long + "x", "", null, 42]) {
    assert.equal(cleanNewPassword(bad), null, JSON.stringify(bad));
  }
});

test("the password rule is spelled out for the form", () => {
  assert.match(PASSWORD_RULE, /8 to 72 characters/);
  for (const part of ["lowercase", "uppercase", "number", "symbol"]) assert.match(PASSWORD_RULE, new RegExp(part));
});

test("each sign-up answer maps to one notice, and only a session signs someone in", () => {
  assert.equal(signUpNotice(null, true), null, "created and signed in");
  assert.equal(signUpNotice(null, false), "confirm", "created without a session: nobody is signed in");
  assert.equal(signUpNotice({ status: 422, code: "user_already_exists" }, false), "exists");
  assert.equal(signUpNotice({ status: 422, code: "email_exists" }, false), "exists");
  assert.equal(signUpNotice({ status: 422, code: "weak_password" }, false), "weak");
  assert.equal(signUpNotice({ status: 429, code: "over_request_rate_limit" }, false), "slow");
  assert.equal(signUpNotice({ status: 0, code: undefined }, false), "unavailable", "the request never reached Supabase");
  assert.equal(signUpNotice({ status: undefined, code: undefined }, false), "unavailable");
  assert.equal(signUpNotice({ status: 503, code: "unexpected_failure" }, false), "unavailable");
  assert.equal(signUpNotice({ status: 400, code: "validation_failed" }, false), "failed");
  assert.equal(signUpNotice({ status: 422, code: "signup_disabled" }, false), "failed");
  assert.equal(signUpNotice({ status: 400, code: "anything" }, true), "failed", "an error wins over a session");
});

test("one-click demo accounts: only with the flag, Supabase, and both accounts' credentials", () => {
  const full = { SUPABASE_URL: "u", SUPABASE_PUBLISHABLE_KEY: "k", DIALBACK_DEMO_SIGN_IN: "on",
    DIALBACK_DEMO_CLERK_EMAIL: "Demo.Clerk@Example.test ", DIALBACK_DEMO_CLERK_PASSWORD: "a-long-password-1",
    DIALBACK_DEMO_APPROVER_EMAIL: "demo.approver@example.test", DIALBACK_DEMO_APPROVER_PASSWORD: "a-long-password-2" };
  const on = demoAccounts(full);
  assert.deepEqual(on && Object.keys(on), ["clerk", "approver"]);
  assert.equal(on?.clerk.email, "demo.clerk@example.test");
  for (const off of [
    { ...full, DIALBACK_DEMO_SIGN_IN: undefined }, { ...full, DIALBACK_DEMO_SIGN_IN: "yes" },
    { ...full, DIALBACK_DEMO_APPROVER_PASSWORD: "" }, { ...full, DIALBACK_DEMO_CLERK_EMAIL: "not an email" },
    { ...full, SUPABASE_URL: undefined }, { ...full, DIALBACK_AUTH_MODE: "dev" },
  ]) assert.equal(demoAccounts(off), null);
});
