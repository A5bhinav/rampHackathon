import Link from "next/link";
import { FrontDoor } from "@/components/front-door";

export default function NotFound() {
  return (
    <FrontDoor title="Not found">
      <p className="muted">There is nothing at this address on this dataset, or you can&apos;t see it.</p>
      <p className="front-links"><Link className="button-link primary" href="/app">Open the dashboard</Link></p>
    </FrontDoor>
  );
}
