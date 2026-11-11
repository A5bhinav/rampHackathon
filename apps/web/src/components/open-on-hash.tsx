"use client";
import { useEffect } from "react";

// A link to an id inside a folded card (#decision-h, #rule-V07_...) opens every <details> around it.
// Chromium does this itself; this covers the browsers that don't (spec 0066). No effect without a hash.
export function OpenOnHash() {
  useEffect(() => {
    const open = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      let el = id ? document.getElementById(id) : null;
      if (!el) return;
      const target = el;
      for (; el; el = el.parentElement) if (el instanceof HTMLDetailsElement) el.open = true;
      target.scrollIntoView({ block: "start" });
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);
  return null;
}
