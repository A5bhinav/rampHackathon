import { CardHead, PageHead } from "@/components/page";
import { PersonControls } from "@/components/people-admin";
import { demoAccounts } from "@/lib/auth-mode";
import { core } from "@/lib/core";
import { isAdmin, logLine, roleLabels } from "@/lib/people";
import { NOT_REPORTED, peopleRows } from "@/lib/settings";

// People (spec 0063; spec 0095 for admins). An admin sees everyone and manages them through the core;
// everyone else sees who they are and the shared demo accounts. The database decides every change.
export default async function PeoplePage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const me = await core.me();
  if (isAdmin(me.roles)) return <AdminPeople me={me.user_id} path={`/d/${datasetId}/control/people`} />;
  const rows = peopleRows(me, demoAccounts(process.env) !== null);
  return (
    <main>
      <PageHead eyebrow="Control · settings" title="People" lead="You, and the shared demo accounts." />
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
        <p className="card-body muted sub table-foot">Everyone else with their roles is {NOT_REPORTED}. Admins grant roles here (spec 0095); the database checks them on every action.</p>
      </section>
    </main>
  );
}

async function AdminPeople({ me, path }: { me: string; path: string }) {
  const [people, log] = await Promise.all([core.people(), core.adminLog()]);
  return (
    <main>
      <PageHead eyebrow="Control · settings" title="People" lead="Everyone in the organisation. Every change needs a reason and is written to the organisation's evidence chain." />
      <section className="card flush" aria-labelledby="people-h">
        <CardHead marker="Roles" title="People" id="people-h" />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Roles</th><th>Ramp user</th><th>Access</th><th></th></tr></thead>
            <tbody>
              {people.map((p) => (
                <tr key={p.id}>
                  <td><strong>{p.display_name}</strong>{p.id === me ? <span className="muted"> · you</span> : null}
                    <br /><span className="muted sub">{p.email ?? "no email"}{p.demo_only ? " · demo account" : ""}</span></td>
                  <td>{roleLabels(p.roles)}</td>
                  <td className="mono">{p.ramp_user_id ? "linked" : <span className="muted">not linked</span>}</td>
                  <td>{p.access_enabled ? "On" : <strong>Off</strong>}</td>
                  <td><PersonControls person={p} self={p.id === me} path={path} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="card-body muted sub table-foot">An admin holds every role but still can't clear, release or reject a change they entered, or one made through a Ramp link they set (R5).</p>
      </section>
      <section className="card flush" aria-labelledby="admin-log-h">
        <CardHead marker="Evidence" title="Admin activity" id="admin-log-h" />
        <p className="card-body sub">
          {log.chain.ok ? `Chain intact: ${log.chain.checked} events verified.` : `Chain broken at event #${log.chain.first_bad_seq}: ${log.chain.detail}`}
        </p>
        {log.events.length ? (
          <ol className="card-body admin-log">
            {log.events.map((e) => (
              <li key={e.seq}><span className="mono muted">#{e.seq} {e.occurred_at.slice(0, 16).replace("T", " ")}</span> {logLine(e)}. <span className="muted">"{e.reason}"</span></li>
            ))}
          </ol>
        ) : <p className="card-body muted">No admin actions yet.</p>}
      </section>
    </main>
  );
}
