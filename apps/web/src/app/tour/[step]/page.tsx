import { notFound, redirect } from "next/navigation";
import { core } from "@/lib/core";
import { sessionToken } from "@/lib/session";
import { caseFor, demoStory, newestDemo } from "@/lib/story";
import { tourHref, tourStep } from "@/lib/tour";

// /tour/N (spec 0039): opens step N of the guided demo on the newest demo dataset.
export default async function TourStepPage({ params }: { params: Promise<{ step: string }> }) {
  const step = tourStep((await params).step);
  if (!step) notFound();
  if (step.target === "results") redirect(tourHref(step, { dataset: "", acme: null }));  // public, no sign-in
  if (!(await sessionToken())) redirect(`/sign-in?next=/tour/${step.n}`);
  const demo = newestDemo(await core.datasets());
  if (!demo) redirect("/demo/acme");  // says there is no demo data yet
  const acme = caseFor(await core.allCases(demo.id), demoStory("acme")!.legal_name);
  redirect(tourHref(step, { dataset: demo.id, acme: acme?.id ?? null }));
}
