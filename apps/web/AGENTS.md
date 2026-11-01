<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# apps/web: scope for agents

- Pages call only the core, server-side, through `src/lib/core.ts`, with the signed-in user's token. The browser never calls the core and never sees the token.
- Types come from `packages/contracts/openapi/dialback.yaml` via `pnpm gen:api` into `packages/contracts/generated/ts/`. Never edit the generated file; `pnpm check:api` fails if it is stale.
- The dashboard never decides who may act. `permissions` from the API only hides buttons; the database refuses the changer, the reviewer and a stale version anyway.
- Every sentence that explains a decision comes from the core's templates. Show text people typed (reasons) as plain text; never render it as HTML.
- Pages by owner: shell, case queue, case detail and release console (Abhi); number lineage and vendor risk list (Inesh); call view and scorecard (Sami). Add yours under `src/app/d/[datasetId]/` and a link in the shell's nav.
- Commands: `pnpm typecheck`, `pnpm build`, `pnpm check:api`.
