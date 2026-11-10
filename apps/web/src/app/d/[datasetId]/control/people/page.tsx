import { CardHead, PageHead } from "@/components/page";
import { demoAccounts } from "@/lib/auth-mode";
import { core } from "@/lib/core";
import { NOT_REPORTED, peopleRows } from "@/lib/settings";

// People (spec 0063): who is signed in and their roles, and the shared demo accounts behind
// "Viewing as". Everyone else with their roles waits for a people endpoint (spec 0075).
export default async function PeoplePage() {
  const me = await core.me();
  const rows = peopleRows(me, demoAccounts(process.env) !== null);
  return (
    <main>
      <PageHead eyebrow="Control · settings" title="People" lead="Who can see and act on this dataset." />
      <section className="card flush" aria-labelledby="people-h">
        <CardHead marker="Roles" title="People" id="people-h" />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Roles</th><th></th></tr></thead>
            <tbody>
              {rows.map((p) => <tr key={p.name}><td><strong>{p.name}</strong></td><td>{p.roles}</td><td className="muted">{p.note}</td></tr>)}
            </tbody>
          </table>
        </div>
        <p className="card-body muted sub table-foot">Everyone else with their roles is {NOT_REPORTED}. Roles are granted by the team (spec 0027); the database checks them on every action.</p>
      </section>
    </main>
  );
}
