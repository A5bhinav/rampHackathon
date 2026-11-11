"use client";

// A case page that couldn't be read (spec 0064): a refresh can meet a core that's restarting or a
// network blip. Say so plainly and offer to try again; nothing about the case is guessed.
export default function CaseError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main>
      <div className="card empty-state" role="alert">
        <p className="empty-title">This case couldn&apos;t be loaded just now</p>
        <p className="muted sub">Nothing was changed.</p>
        <p><button type="button" className="primary" onClick={() => reset()}>Try again</button></p>
      </div>
    </main>
  );
}
