// The password sign-in end to end (spec 0010): the dashboard, the core and a stand-in for
// Supabase Auth, in a real browser. Runs by hand; CI has no browser job.
//
//   uv run python scripts/dev/fake_supabase_auth.py --expires 120
//   uv run python scripts/dev/stack.py --changes --supabase-url http://127.0.0.1:54321
//   (cd apps/web && pnpm build && CORE_URL=http://127.0.0.1:8001 SUPABASE_URL=http://127.0.0.1:54321 \
//     SUPABASE_PUBLISHABLE_KEY=sb_publishable_local pnpm start)
//   node scripts/dev/signin_e2e.cjs <an approver's user id, from the stack's "Seeded users" line>
//
// Needs Playwright with Chromium (npx playwright install chromium). The refresh check waits for
// the stand-in's access token to expire, so start it with --expires 120.
const { chromium } = require('playwright');
const { randomUUID } = require('crypto');

const [APPROVER] = process.argv.slice(2);
if (!APPROVER) { console.error('usage: node scripts/dev/signin_e2e.cjs <approver user id>'); process.exit(2); }
const WEB = process.env.WEB ?? 'http://127.0.0.1:3000', FAKE = process.env.FAKE ?? 'http://127.0.0.1:54321';
const EMAIL = 'riley@example.test', PASSWORD = `e2e-${randomUUID()}`;
const check = (ok, what) => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); if (!ok) process.exitCode = 1; };

(async () => {
  await fetch(`${FAKE}/_fake/users`, { method: 'POST', body: JSON.stringify({ id: APPROVER, email: EMAIL, password: PASSWORD }) });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  const signIn = async (email, password, on = page) => {
    await on.goto(`${WEB}/sign-in`);
    await on.fill('#email', email);
    await on.fill('#password', password);
    await on.getByRole('button', { name: 'Sign in' }).click();
    await on.waitForLoadState('networkidle');
  };
  const sessionCookies = async (on) => (await on.context().cookies()).filter((c) => c.name.startsWith('sb-') || c.name === 'dialback_signed_in_at');
  const notice = () => page.locator('p.result.error').innerText().catch(() => '');
  const who = () => page.locator('.who').innerText().catch(() => '');

  await signIn('stranger@example.test', PASSWORD);
  const unknown = await notice();
  await signIn(EMAIL, 'not-the-password');
  const wrong = await notice();
  check(unknown.includes("don't match") && unknown === wrong, 'an unknown email and a wrong password get the same answer');
  check(!(await page.context().cookies()).some((c) => c.name.startsWith('sb-')), 'a refused sign-in starts no session');

  await signIn(EMAIL, PASSWORD);
  check(/ · approver/.test(await who()), `signed in, and the core ran as that person (${(await who()).replace(/\n/g, ' | ')})`);
  check(page.url().includes('/cases'), `landed on the queue (${page.url()})`);
  const cookies = await page.context().cookies();
  const ours = cookies.filter((c) => c.name.startsWith('sb-') || c.name === 'dialback_signed_in_at');
  check(ours.length >= 2 && ours.every((c) => c.httpOnly), `the session cookies are httpOnly (${ours.map((c) => c.name).join(', ')})`);

  // A sign-in lasts 12 hours: pretend this one happened 13 hours ago.
  const queue = page.url();
  await page.context().addCookies([{ name: 'dialback_signed_in_at', value: new Date(Date.now() - 13 * 3600_000).toISOString(),
    url: WEB, httpOnly: true, sameSite: 'Lax' }]);
  await page.goto(queue);
  check(page.url().includes('/sign-in'), 'with the sign-in marker 13 hours old, the dashboard asks to sign in again');
  await signIn(EMAIL, PASSWORD);

  // The judges share accounts: signing out in one browser leaves the other signed in.
  const other = await (await browser.newContext()).newPage();
  await signIn(EMAIL, PASSWORD, other);
  await other.getByRole('button', { name: 'Sign out' }).click();
  await other.waitForLoadState('networkidle');
  await page.goto(queue);
  check(/ · approver/.test(await who()), 'signing out in another browser leaves this one signed in');

  // The cap comes from the signed token, not only our cookie: a sign-in made 13 hours ago ends.
  await fetch(`${FAKE}/_fake/backdate`, { method: 'POST', body: JSON.stringify({ email: EMAIL, hours: 13 }) });
  const stale = await (await browser.newContext()).newPage();
  await signIn(EMAIL, PASSWORD, stale);
  check(stale.url().includes('/sign-in') && (await sessionCookies(stale)).length === 0,
    `a sign-in older than 12 hours by its token is signed out, its cookies gone (${stale.url()})`);

  console.log('waiting 125 s for the 120 s access token to expire...');
  await new Promise((r) => setTimeout(r, 125_000));
  await page.reload();
  await page.waitForLoadState('networkidle');
  check(/ · approver/.test(await who()), 'still signed in after the access token expired (refreshed)');

  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.waitForLoadState('networkidle');
  check(page.url().includes('/sign-in'), 'sign-out lands on sign-in');
  const left = await sessionCookies(page);
  check(left.length === 0, `sign-out deleted the session cookies (${left.map((c) => c.name).join(', ') || 'none left'})`);
  await page.goto(`${WEB}/`);
  check(page.url().includes('/sign-in'), 'after sign-out the dashboard asks to sign in again');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
