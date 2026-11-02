// Sign-in rules that don't need a browser or a Supabase project (spec 0010). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { SESSION_HOURS, Throttle, authMode, cleanEmail, cleanPassword, sessionFresh, signedInAt } from "../src/lib/auth-mode.ts";

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
