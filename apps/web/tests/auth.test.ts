// Sign-in rules that don't need a browser or a Supabase project (spec 0010). `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { authMode, cleanEmail, linkParams } from "../src/lib/auth-mode.ts";

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

test("a sign-in link is accepted only as a magic link or an invite, with a plausible token hash", () => {
  const hash = "a".repeat(56);
  assert.deepEqual(linkParams({ token_hash: hash, type: "email" }), { tokenHash: hash, type: "email" });
  assert.deepEqual(linkParams({ token_hash: hash, type: "invite" }), { tokenHash: hash, type: "invite" });
  for (const type of ["recovery", "signup", "email_change", "magiclink", "", undefined]) {
    assert.equal(linkParams({ token_hash: hash, type }), null, `type ${type}`);
  }
  for (const tokenHash of ["", "short", "has spaces in it ok?", "x".repeat(600), undefined, ["a", "b"]]) {
    assert.equal(linkParams({ token_hash: tokenHash, type: "email" }), null, `hash ${String(tokenHash)}`);
  }
});

test("an email is trimmed and lowercased, and anything that isn't one is refused", () => {
  assert.equal(cleanEmail("  Riley.Approver@Example.COM "), "riley.approver@example.com");
  for (const bad of ["", "no-at-sign", "a@b", "two@@example.com", "spa ce@example.com", `${"x".repeat(250)}@example.com`, null, 42]) {
    assert.equal(cleanEmail(bad), null, String(bad));
  }
});
