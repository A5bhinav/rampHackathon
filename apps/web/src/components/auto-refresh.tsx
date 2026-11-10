"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Keeps a server-rendered page fresh while code is still working on the case (spec 0064): every 2
// seconds it asks the server to render again. It stops when the state settles (the server renders
// it with active=false) and while the tab is hidden. The browser never calls the core.
export function AutoRefresh({ active, every = 2000 }: { active: boolean; every?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => { if (document.visibilityState === "visible") router.refresh(); }, every);
    return () => window.clearInterval(id);
  }, [active, every, router]);
  return null;
}
