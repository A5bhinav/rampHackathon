import { notFound, redirect } from "next/navigation";
import { core } from "@/lib/core";
import { sessionToken } from "@/lib/session";
import { demoStory, newestDemo } from "@/lib/story";
import { flowHref, tourCase, tourHref, tourStep, type TourIds } from "@/lib/tour";

// /tour/N (specs 0039, 0066): opens beat N of the guided demo on the newest demo dataset, following
// Acme's newest case: the one the presenter's change opened. /tour/flow is the last beat's button
// (flow mode for that case); /tour/8, from the old eight-step tour, opens the last beat.
export default async function TourStepPage({ params }: { params: Promise<{ step: string }> }) {
  const raw = (await params).step;
  if (raw === "8") redirect("/tour/7");
  const step = raw === "flow" ? null : tourStep(raw);
  if (raw !== "flow" && !step) notFound();
  if (step?.target === "lab") redirect(tourHref(step, { dataset: "", vendor: null, kase: null }));  // public, no sign-in
  if (!(await sessionToken())) redirect(`/sign-in?next=/tour/${raw}`);
  const demo = newestDemo(await core.datasets());
  if (!demo) redirect("/demo/acme");  // says there is no demo data yet
  const ids = await tourIds(demo.id);
  redirect(step ? tourHref(step, ids) : flowHref(ids));
}

async function tourIds(dataset: string): Promise<TourIds> {
  const acme = demoStory("acme")!.legal_name;
  const [accounts, cases] = await Promise.all([
    core.vendorAccounts(dataset).catch(() => null),  // no Ramp mock vendors: the change beat opens the vendor list
    core.allCases(dataset),
  ]);
  const vendor = accounts?.vendors.find((v) => v.legal_name === acme)?.vendor_id
    ?? cases.find((c) => c.vendor.legal_name === acme)?.vendor.id ?? null;
  return { dataset, vendor, kase: tourCase(cases, vendor) };
}

