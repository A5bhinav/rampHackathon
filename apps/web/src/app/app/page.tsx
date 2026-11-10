import Link from "next/link";
import { redirect } from "next/navigation";
import { core } from "@/lib/core";
import { FrontDoor } from "@/components/front-door";

// The dashboard's front door (spec 0033): the newest dataset's queue. `/` is the public page.
export default async function OpenDashboard() {
  const datasets = await core.datasets();
  if (datasets.length > 0) redirect(`/d/${datasets[0].id}/cases`);
  return (
    <FrontDoor title="No datasets yet">
        <p className="muted">
          A dataset is one company&apos;s vendors, cases and evidence chain. There is none yet, so there is nothing to show.
          The team creates the demo one with <span className="mono">dialback-core demo reset</span>; after that this page opens its case queue.
        </p>
      <p className="front-links"><Link className="button-link" href="/">How Dialback works</Link></p>
    </FrontDoor>
  );
}
