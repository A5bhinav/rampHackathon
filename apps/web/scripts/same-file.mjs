// Fails when two files differ. `check:api` uses it to refuse a build whose committed API types
// (packages/contracts/generated/ts/dialback.ts) are stale. Plain Node, because Vercel's build
// image has no `diff`.
import { readFileSync } from "node:fs";

const [fresh, committed] = process.argv.slice(2);
if (!fresh || !committed) {
  console.error("usage: node scripts/same-file.mjs <freshly generated> <committed>");
  process.exit(2);
}
if (readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8")) {
  console.error(`${committed} is stale: run \`pnpm gen:api\` in apps/web and commit the result`);
  process.exit(1);
}
