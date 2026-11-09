// A small line-icon set for the money path (spec 0032): 24px grid, 1.75 stroke, currentColor.
// Decorative only: every icon sits next to words that say the same thing.
type Name = "hold" | "call" | "decision" | "person" | "release" | "card" | "bank" | "arrow" | "detect" | "ledger" | "chain" | "mail";

const PATHS: Record<Name, React.ReactNode> = {
  hold: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></>,
  call: <path d="M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" />,
  decision: <><path d="M12 3l7 3v5c0 4.6-3 8.3-7 10-4-1.7-7-5.4-7-10V6z" /><path d="M9 12l2 2 4-4" /></>,
  person: <><circle cx="12" cy="8" r="4" /><path d="M4.5 21c.6-3.8 3.7-6 7.5-6s6.9 2.2 7.5 6" /></>,
  release: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 7.7-1.5" /></>,
  card: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 10h18M7 15h4" /></>,
  bank: <><path d="M3 10l9-6 9 6" /><path d="M5 10v8M10 10v8M14 10v8M19 10v8M3 21h18" /></>,
  arrow: <path d="M12 5v14M6 13l6 6 6-6" />,
  detect: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.3-4.3M11 8v3l2 1.5" /></>,
  ledger: <><path d="M6 3h10l3 3v15H6z" /><path d="M9 9h7M9 13h7M9 17h4" /></>,
  chain: <><rect x="3" y="9" width="8" height="6" rx="3" /><rect x="13" y="9" width="8" height="6" rx="3" /><path d="M10 12h4" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
};

export function Icon({ name, size = 18 }: { name: Name; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}
