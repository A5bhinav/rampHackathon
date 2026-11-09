"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

// The shell's page links: the current page is marked for screen readers and drawn with an underline.
export function NavLinks({ links }: { links: { href: string; label: string }[] }) {
  const path = usePathname();
  return (
    <>
      {links.map((l) => {
        const current = path === l.href || path.startsWith(`${l.href}/`);
        return <Link key={l.href} href={l.href} aria-current={current ? "page" : undefined}>{l.label}</Link>;
      })}
    </>
  );
}

// The brand mark: a handset with a return arrow, the call back.
export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" />
        <path d="M15 3h6v6" />
        <path d="M21 3l-6 6" />
      </svg>
    </span>
  );
}
