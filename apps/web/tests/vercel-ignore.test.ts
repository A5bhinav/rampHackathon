// Vercel's Ignored Build Step for the dashboard (apps/web/vercel.json "ignoreCommand").
// Each test builds a throwaway git repo shaped like this one, then runs the exact command from
// vercel.json in its apps/web folder, as Vercel does. Exit 0 skips the build; exit 1 builds.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";

const webDir = new URL("../", import.meta.url).pathname;
const config = JSON.parse(readFileSync(join(webDir, "vercel.json"), "utf8"));
const ignoreCommand: string = config.ignoreCommand;
const SKIP = 0;
const BUILD = 1;

// Git with no user or system config, so a developer's settings (signing, hooks) can't change the result.
const gitEnv: NodeJS.ProcessEnv = {
  NODE_ENV: "test",
  PATH: process.env.PATH ?? "/usr/bin:/bin",
  GIT_CONFIG_NOSYSTEM: "1",
  GIT_CONFIG_GLOBAL: "/dev/null",
  GIT_AUTHOR_NAME: "t", GIT_AUTHOR_EMAIL: "t@example.test",
  GIT_COMMITTER_NAME: "t", GIT_COMMITTER_EMAIL: "t@example.test",
};

function git(cwd: string, ...args: string[]): string {
  const r = spawnSync("git", args, { cwd, env: gitEnv, encoding: "utf8" });
  assert.equal(r.status, 0, `git ${args.join(" ")}: ${r.stderr}`);
  return r.stdout.trim();
}

function write(root: string, path: string, text: string) {
  mkdirSync(dirname(join(root, path)), { recursive: true });
  writeFileSync(join(root, path), text);
}

// A repo with the files the dashboard build reads and a few it doesn't. Returns its root.
function repo(): string {
  const root = mkdtempSync(join(tmpdir(), "vercel-ignore-"));
  git(root, "init", "-q", "-b", "main");
  for (const p of ["apps/web/src/page.tsx", "packages/contracts/openapi/dialback.yaml", "package.json",
    "pnpm-lock.yaml", "pnpm-workspace.yaml", "docs/deploy.md", "apps/core/main.py", "README.md"]) {
    write(root, p, `${p}\n`);
  }
  for (const f of ["vercel.json", "scripts/vercel-ignore-build.sh"]) {
    mkdirSync(dirname(join(root, "apps/web", f)), { recursive: true });
    copyFileSync(join(webDir, f), join(root, "apps/web", f));
  }
  commit(root, "initial");
  return root;
}

function commit(root: string, message: string, ...paths: string[]): string {
  for (const p of paths) write(root, p, `${p} changed by ${message}\n`);
  git(root, "add", "-A");
  git(root, "commit", "-q", "--allow-empty", "-m", message);
  return git(root, "rev-parse", "HEAD");
}

// Runs vercel.json's ignoreCommand from <root>/apps/web, the project's Root Directory.
function ignore(root: string, previousSha?: string): number | null {
  const env: NodeJS.ProcessEnv = { ...gitEnv, GIT_CEILING_DIRECTORIES: dirname(root) };
  if (previousSha !== undefined) env.VERCEL_GIT_PREVIOUS_SHA = previousSha;
  const r = spawnSync("sh", ["-c", ignoreCommand], { cwd: join(root, "apps/web"), env, encoding: "utf8" });
  return r.status;
}

test("vercel.json runs the ignore script", () => {
  assert.equal(ignoreCommand, "bash scripts/vercel-ignore-build.sh");
});

test("skips when only files the dashboard doesn't read changed since the last build", () => {
  const root = repo();
  const built = git(root, "rev-parse", "HEAD");
  commit(root, "docs", "docs/deploy.md");
  commit(root, "python", "apps/core/main.py", "README.md");
  assert.equal(ignore(root, built), SKIP);
});

for (const path of ["apps/web/src/page.tsx", "apps/web/src/new-file.tsx", "packages/contracts/openapi/dialback.yaml",
  "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", ".npmrc"]) {
  test(`builds when ${path} changed`, () => {
    const root = repo();
    const built = git(root, "rev-parse", "HEAD");
    commit(root, "change", path);
    assert.equal(ignore(root, built), BUILD);
  });
}

test("builds when a dashboard change is in an earlier commit than the last one (HEAD^ alone would miss it)", () => {
  const root = repo();
  const built = git(root, "rev-parse", "HEAD");
  commit(root, "web", "apps/web/src/page.tsx");
  commit(root, "docs", "docs/deploy.md");
  assert.equal(ignore(root, built), BUILD);
});

test("builds when a dashboard file was deleted", () => {
  const root = repo();
  const built = git(root, "rev-parse", "HEAD");
  git(root, "rm", "-q", "apps/web/src/page.tsx");
  commit(root, "delete");
  assert.equal(ignore(root, built), BUILD);
});

test("builds on a branch's first deployment (no previous SHA, or an empty one)", () => {
  const root = repo();
  commit(root, "docs", "docs/deploy.md");
  assert.equal(ignore(root), BUILD);
  assert.equal(ignore(root, ""), BUILD);
});

test("builds when the previous SHA isn't in the clone", () => {
  const root = repo();
  commit(root, "docs", "docs/deploy.md");
  assert.equal(ignore(root, "0123456789abcdef0123456789abcdef01234567"), BUILD);
  assert.equal(ignore(root, "not-a-sha"), BUILD);
});

// Vercel clones with --depth=10. These use --depth=2 on a four-commit history.
function shallowClone(origin: string): string {
  const shallow = mkdtempSync(join(tmpdir(), "vercel-ignore-shallow-"));
  git(shallow, "clone", "-q", "--depth=2", `file://${origin}`, ".");
  return shallow;
}

test("in a shallow clone, still skips when the previous build is inside the clone's history", () => {
  const origin = repo();
  commit(origin, "docs 0", "docs/deploy.md");
  const built = commit(origin, "docs 1", "docs/deploy.md");  // the shallow clone's oldest commit
  commit(origin, "docs 2", "docs/deploy.md");
  assert.equal(ignore(shallowClone(origin), built), SKIP);
});

test("in a shallow clone, builds when the previous build is older than the clone's history", () => {
  const origin = repo();
  const built = git(origin, "rev-parse", "HEAD");
  for (let i = 0; i < 3; i++) commit(origin, `docs ${i}`, "docs/deploy.md");
  assert.equal(ignore(shallowClone(origin), built), BUILD);
});

test("builds a redeploy of the same commit, since a person asked for it", () => {
  const root = repo();
  const head = commit(root, "docs", "docs/deploy.md");
  assert.equal(ignore(root, head), BUILD);
});

test("builds when not run inside a git checkout", () => {
  const dir = mkdtempSync(join(tmpdir(), "vercel-ignore-nogit-"));
  for (const f of ["vercel.json", "scripts/vercel-ignore-build.sh"]) {
    mkdirSync(dirname(join(dir, "apps/web", f)), { recursive: true });
    copyFileSync(join(webDir, f), join(dir, "apps/web", f));
  }
  assert.equal(ignore(dir, "0123456789abcdef0123456789abcdef01234567"), BUILD);
});
