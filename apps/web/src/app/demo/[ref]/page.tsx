import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import { FrontDoor } from "@/components/front-door";
import { core } from "@/lib/core";
import { sessionToken } from "@/lib/session";
import { caseFor, demoStory, newestDemo } from "@/lib/story";

// /demo/<ref> (spec 0033): the seeded vendor's newest case on the newest demo dataset. The core
// call signs the visitor in first if needed. With no such case, the queue says why.
export default async function DemoCase({ params }: { params: Promise<{ ref: string }> }) {
  const story = demoStory((await params).ref);
  if (!story) notFound();
  if (!(await sessionToken())) redirect(`/sign-in?next=/demo/${story.ref}`);  // come back here after signing in
  const demo = newestDemo(await core.datasets());
  if (!demo) {
    return (
      <FrontDoor title="No demo data yet">
          <p className="muted">
            {story.legal_name}&apos;s case lives on the demo dataset, and there isn&apos;t one right now. The team creates it
            with <span className="mono">dialback-core demo reset</span>.
          </p>
        <p className="front-links"><Link className="button-link primary" href="/app">Open the dashboard</Link> <Link className="button-link" href="/">How Dialback works</Link></p>
      </FrontDoor>
    );
  }
  const found = caseFor(await core.allCases(demo.id), story.legal_name);
  if (!found) redirect(`/d/${demo.id}/cases?missing=${encodeURIComponent(story.ref)}`);
  redirect(`/d/${demo.id}/cases/${found.id}`);
}
