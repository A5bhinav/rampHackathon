import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { SINCE_COOKIE, SUPABASE_COOKIE_OPTIONS, authMode, sessionFresh, signedInAt } from "@/lib/auth-mode";

// Keeps the Supabase session fresh on every request (spec 0010; Supabase's server-side auth
// guide), and ends it once the sign-in is older than 12 hours. It refreshes an expired access
// token with the refresh token and writes the new cookies to both the request the page sees and
// the response. Who someone is gets decided by the core, which verifies every token.
export async function proxy(request: NextRequest) {
  if (authMode(process.env) !== "supabase") return NextResponse.next({ request });
  let response = NextResponse.next({ request });
  const supabase = createServerClient(process.env.SUPABASE_URL!, process.env.SUPABASE_PUBLISHABLE_KEY!, {
    cookieOptions: SUPABASE_COOKIE_OPTIONS,
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet, headers) => {
        toSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        toSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        // Cache headers that keep a CDN from caching a response that sets session cookies.
        Object.entries(headers ?? {}).forEach(([name, value]) => response.headers.set(name, value));
      },
    },
  });
  // Nothing may run between creating the client and this call, or sessions can drop at random.
  const { data } = await supabase.auth.getClaims();
  const claims = data?.claims;
  if (!claims) return response;

  // The sign-in time comes from the verified token; our own cookie is the fallback for a token
  // without one. Past 12 hours, the session ends here, not just on the page.
  const since = signedInAt(claims) ?? request.cookies.get(SINCE_COOKIE)?.value;
  if (sessionFresh(since, new Date())) return response;
  await supabase.auth.signOut({ scope: "local" }).catch(() => undefined);
  // No redirect from here: the page finds no session and sends the person to sign in itself. (A
  // server action's redirect is followed by an internal fetch that can't drop cookies, so a
  // redirect here would loop.) The cookies go from the request the page sees and the browser.
  const ended = [...request.cookies.getAll()].map((c) => c.name).filter((n) => /^sb-.+-auth-token/.test(n) || n === SINCE_COOKIE);
  ended.forEach((name) => request.cookies.delete(name));
  const out = NextResponse.next({ request });
  ended.forEach((name) => out.cookies.delete(name));
  out.headers.set("Cache-Control", "private, no-store");
  return out;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
