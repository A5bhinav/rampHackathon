import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { SUPABASE_COOKIE_OPTIONS, authMode } from "@/lib/auth-mode";

// Keeps the Supabase session fresh on every request (spec 0010; Supabase's server-side auth
// guide). It refreshes an expired access token with the refresh token and writes the new
// cookies to both the request the page sees and the response. It never decides access: pages
// that get no valid token from here are sent to sign in by the core's 401.
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
  await supabase.auth.getClaims();
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
