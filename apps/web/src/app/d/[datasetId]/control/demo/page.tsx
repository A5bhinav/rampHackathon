import { redirect } from "next/navigation";
import { DemoControls } from "@/components/demo-controls";
import { CardHead, PageHead } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { isAdmin } from "@/lib/people";
import type { DemoState } from "@/lib/api-types";

// Demo controls (spec 0095, D9): the shell's `dialback-core demo` commands, for admins only. Everything
// shown is the core's own status of the current demo.
export default async function DemoPage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const me = await core.me();
  if (!isAdmin(me.roles)) redirect(`/d/${datasetId}/control`);
  const path = `/d/${datasetId}/control/demo`;
  const [people, state] = await Promise.all([core.people(), core.demoState().catch((e) => (e instanceof CoreError ? e.body.message : Promise.reject(e)))]);
  const clerks = people.filter((p) => p.access_enabled && p.id !== me.user_id && (p.roles.includes("clerk") || p.roles.includes("admin")))
    .map((p) => ({ id: p.id, name: p.display_name }));
  return (
    <main>
      <PageHead eyebrow="Control · admin" title="Demo controls" lead="Reset, chaos and tamper, as the shell's demo commands do. Each is written to the organisation's evidence chain before it runs." />
      <section className="card" aria-labelledby="demo-status-h">
        <CardHead marker="Status" title="Current demo" id="demo-status-h" />
        {typeof state === "string" ? <p className="card-body muted">{state}</p> : <Status state={state} />}
      </section>
      <section className="card" aria-labelledby="demo-controls-h">
        <CardHead marker="Controls" title="Commands" id="demo-controls-h" />
        <DemoControls path={path} clerks={clerks} chaos={typeof state !== "string" && state.chaos} />
      </section>
    </main>
  );
}

function Status({ state }: { state: DemoState }) {
  return (
    <div className="card-body">
      <p><strong>{state.dataset.label}</strong> · {state.cases.length} case{state.cases.length === 1 ? "" : "s"} · chaos {state.chaos ? "on" : "off"}</p>
      <p className={state.chain.ok ? "" : "bad"}>{state.chain.ok ? `Chain intact (${state.chain.checked} events).` : `Chain broken at event #${state.chain.first_bad_seq}: ${state.chain.detail}`}</p>
      {state.alerts.length ? <ul>{state.alerts.map((a, i) => <li key={i}>{a.severity}: {a.message}</li>)}</ul> : null}
      {state.warnings.length ? <ul className="muted">{state.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
    </div>
  );
}
