// The mail page's labels (spec 0089): one plain sentence for every value the contract can send.
// The value lists are read from the generated contract, so a new enum value without a label fails here.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { flagLabel, outcomeLabel, replyLabel } from "../src/lib/mail.ts";

const generated = readFileSync(new URL("../../../packages/contracts/generated/ts/dialback.ts", import.meta.url), "utf8");

function enumValues(name: string): string[] {
  const m = new RegExp(`^\\s+${name}: ((?:"[a-z_0-9]+"(?: \\| )?)+);`, "m").exec(generated);
  assert.ok(m, `${name} not found in the generated contract`);
  return [...m[1].matchAll(/"([a-z_0-9]+)"/g)].map((x) => x[1]);
}

const NON_ASCII_DASH = /[\u2010-\u2015\u2212]/;

function check(name: string, count: number, label: (v: never) => string) {
  const values = enumValues(name);
  assert.equal(values.length, count, `${name} gained or lost a value: update the labels and this count`);
  for (const v of values) {
    const text = label(v as never);
    assert.equal(typeof text, "string", v);
    assert.ok(text.trim().length > 0, `${name}.${v} has no label`);
    assert.ok(!NON_ASCII_DASH.test(text), `${name}.${v} label has a non-ASCII dash`);
    assert.ok(/^[\x20-\x7e]+$/.test(text), `${name}.${v} label is not plain ASCII`);
  }
  assert.equal(new Set(values.map((v) => label(v as never))).size, values.length, `${name} labels must differ`);
}

test("every MailOutcome has a plain label", () => check("MailOutcome", 13, outcomeLabel));
test("every ReplyStatus has a plain label", () => check("ReplyStatus", 7, replyLabel));
test("every RedFlag has a plain label", () => check("RedFlag", 5, flagLabel));
