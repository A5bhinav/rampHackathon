"use client";
import { usePathname } from "next/navigation";
import { signInAsDemo } from "@/app/sign-in/actions";

// "Viewing as" (spec 0041): only when demo sign-in is on. Each option signs in again as that
// shared demo account through the existing server action (spec 0034) and comes back to this page.
// Nothing switches in the browser: the core sees a different person's token.
export function ViewingAs({ active }: { active: "clerk" | "approver" }) {
  const path = usePathname();
  return (
    <form action={signInAsDemo} className="viewing-as">
      <input type="hidden" name="next" value={path} />
      <span className="viewing-label" id="viewing-as-label">Viewing as</span>
      <span className="segmented" role="group" aria-labelledby="viewing-as-label">
        {(["clerk", "approver"] as const).map((role) => (
          <button key={role} type="submit" name="role" value={role} aria-pressed={active === role} className={active === role ? "is-on" : ""}>
            Demo {role}
          </button>
        ))}
      </span>
    </form>
  );
}
