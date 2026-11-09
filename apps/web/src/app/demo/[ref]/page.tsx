import { notFound, redirect } from "next/navigation";
import { core } from "@/lib/core";
import { caseFor, demoStory, newestDemo } from "@/lib/story";

// /demo/<ref> (spec 0033): the seeded vendor's newest case on the newest demo dataset. The core
// call signs the visitor in first if needed. With no such case, the queue says why.
export default async function DemoCase({ params }: { params: Promise<{ ref: string }> }) {
  const story = demoStory((await params).ref);
  if (!story) notFound();
  const demo = newestDemo(await core.datasets());
  if (!demo) redirect("/app");
  const found = caseFor(await core.allCases(demo.id), story.legal_name);
  if (!found) redirect(`/d/${demo.id}/cases?missing=${encodeURIComponent(story.ref)}`);
  redirect(`/d/${demo.id}/cases/${found.id}`);
}
