import type { Metadata } from "next";
import { PublicHeader } from "@/components/public-header";
import { BANK_CHANGES_TEXT } from "@/lib/bank-changes";

export const metadata: Metadata = { title: "Dialback: how bank changes work" };

// Public, signed out, no data (spec 0089): the page the reply to a bank-change email links to.
export default function BankChangesPage() {
  return (
    <div className="public">
      <PublicHeader links={[{ href: "/", label: "Front door" }]} />
      <main>
        <section className="card">
          <div className="card-head"><h1>Changing payment details</h1></div>
          <p className="card-body">{BANK_CHANGES_TEXT}</p>
        </section>
      </main>
    </div>
  );
}
