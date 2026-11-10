import { redirect } from "next/navigation";
import { core } from "@/lib/core";
import { sessionToken } from "@/lib/session";

// The front door's Control button (spec 0063): the newest dataset's Control overview, signing in
// first and coming back here. With no dataset, /app says why.
export default async function OpenControl() {
  if (!(await sessionToken())) redirect("/sign-in?next=/app/control");
  const datasets = await core.datasets();
  redirect(datasets.length ? `/d/${datasets[0].id}/control` : "/app");
}
