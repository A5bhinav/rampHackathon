"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { refreshDelay } from "@/lib/stations";

// Keeps a server-rendered page fresh while code is still working on the case (spec 0064): it asks
// the server to render again every 2 seconds for the first minute, then every 10. It stops when the
// state settles (the server renders it with active=false), skips while the tab is hidden, and never
// starts a refresh while the last one is still rendering. The browser never calls the core.
export function AutoRefresh({ active }: { active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const start = Date.now();
    let timer = 0;
    const tick = () => {
      if (document.visibilityState === "visible") router.refresh();
      timer = window.setTimeout(tick, refreshDelay(Date.now() - start));
    };
    timer = window.setTimeout(tick, refreshDelay(0));
    return () => window.clearTimeout(timer);
  }, [active, router]);
  return null;
}
