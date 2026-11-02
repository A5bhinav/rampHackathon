// The magic-link sign-in end to end (spec 0010): the dashboard, the core and a stand-in for
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
const [RILEY] = process.argv.slice(2);
if (!RILEY) { console.error('usage: node scripts/dev/signin_e2e.cjs <approver user id>'); process.exit(2); }
const WEB = process.env.WEB ?? 'http://127.0.0.1:3000', FAKE = process.env.FAKE ?? 'http://127.0.0.1:54321';
const check = (ok, what) => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); if (!ok) process.exitCode = 1; };
(async () => {
  await fetch(`${FAKE}/_fake/users`, { method: 'POST', body: JSON.stringify({ id: RILEY, email: 'riley@example.test' }) });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  const ask = async (email) => {
    await page.goto(`${WEB}/sign-in`);
    await page.fill('#email', email);
    await page.getByRole('button', { name: 'Email me a sign-in link' }).click();
    await page.waitForLoadState('networkidle');
    return page.getByRole('status').innerText();
  };
  const lastLink = async (email) => (await (await fetch(`${FAKE}/_fake/last-link?email=${email}`)).json()).link;

  const strangerNotice = await ask('stranger@example.test');
  const rileyNotice = await ask('Riley@Example.test');
  check(strangerNotice === rileyNotice && strangerNotice.includes('If this address belongs'), 'the same answer for an unknown and a known email');
  check(await lastLink('stranger@example.test') === null, 'no link for an email without an account');
  const link = await lastLink('riley@example.test');
  check(Boolean(link), 'a link for the approver');

  await page.goto(link);
  await page.goto(link);  // opening it twice (an email scanner, then the person) spends nothing
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForLoadState('networkidle');
  const who = await page.locator('.who').innerText().catch(() => '');
  check(/ · approver/.test(who), `signed in, and the core ran as that person (${who.replace(/\n/g, ' | ')})`);
  check(page.url().includes('/cases'), `landed on the queue (${page.url()})`);
  const cookies = await page.context().cookies();
  const sb = cookies.filter((c) => c.name.startsWith('sb-'));
  check(sb.length > 0 && sb.every((c) => c.httpOnly), `Supabase's session cookies are httpOnly (${sb.map((c) => c.name).join(', ')})`);

  const again = await browser.newPage();
  await again.goto(link);
  await again.getByRole('button', { name: 'Sign in' }).click();
  await again.waitForLoadState('networkidle');
  check(again.url().includes('/sign-in?failed=1'), 'a spent link starts no session');
  await again.goto(`${WEB}/auth/confirm?token_hash=abc&type=recovery`);
  check((await again.locator('p.result.error').innerText()).includes('not valid'), 'a recovery-type or malformed link is refused on sight');

  console.log('waiting 125 s for the 120 s access token to expire...');
  await new Promise((r) => setTimeout(r, 125_000));
  await page.reload();
  await page.waitForLoadState('networkidle');
  check(/ · approver/.test(await page.locator('.who').innerText().catch(() => '')), 'still signed in after the access token expired (refreshed)');

  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.waitForLoadState('networkidle');
  check(page.url().includes('/sign-in'), 'sign-out lands on sign-in');
  const left = (await page.context().cookies()).filter((c) => c.name.startsWith('sb-')).map((c) => c.name);
  check(left.length === 0, `sign-out deleted Supabase's cookies (${left.join(', ') || 'none left'})`);
  await page.goto(`${WEB}/`);
  check(page.url().includes('/sign-in'), 'after sign-out the dashboard asks to sign in again');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
