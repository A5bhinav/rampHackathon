// The evidence chain page (spec 0037): words for what the core's checks found. `pnpm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { chainHeadline, rowStatus } from "../src/lib/evidence.ts";

const row = (seq: number, content_ok = true, link_ok = true) => ({ seq, content_ok, link_ok }) as never;

test("each row's result in words, and the first bad row is marked", () => {
  assert.deepEqual(rowStatus(row(3), null), { word: "intact", tone: "ok", first: false });
  assert.deepEqual(rowStatus(row(3, false), 3), { word: "body edited", tone: "bad", first: true });
  assert.deepEqual(rowStatus(row(4, true, false), 3), { word: "link broken", tone: "bad", first: false });
  assert.deepEqual(rowStatus(row(5, false, false), null), { word: "body edited, link broken", tone: "bad", first: false });
  assert.deepEqual(rowStatus(row(9), 3), { word: "intact", tone: "ok", first: false });
});

test("the headline says intact with a count, or names the broken row", () => {
  assert.equal(chainHeadline({ ok: true, checked: 25, first_bad_seq: null } as never), "Intact: Postgres recomputed all 25 rows just now");
  assert.equal(chainHeadline({ ok: true, checked: 0, first_bad_seq: null } as never), "Empty: no rows yet");
  assert.equal(chainHeadline({ ok: false, checked: 6, first_bad_seq: 7 } as never), "Broken at row #7");
});
