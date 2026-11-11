import { ACCESS_OFF } from "@/lib/people";

// A person whose access an admin turned off (spec 0095, D7, A20). The core refuses every request they
// make; this page says so and offers to sign out.
export default function AccessOffPage() {
  return (
    <main className="narrow">
      <h1>Access turned off</h1>
      <p>{ACCESS_OFF}</p>
      <form action="/sign-out" method="post"><button type="submit">Sign out</button></form>
    </main>
  );
}
