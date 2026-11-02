import { redirect } from "next/navigation";
import { core } from "@/lib/core";

export default async function Home() {
  const datasets = await core.datasets();
  if (datasets.length > 0) redirect(`/d/${datasets[0].id}/cases`);
  return (
    <main className="shell">
      <div className="card" style={{ marginTop: 48 }}>
        <h1>No datasets yet</h1>
        <p className="muted">Create one with <span className="mono">dialback-core new-dataset --kind demo --label Demo</span>.</p>
      </div>
    </main>
  );
}
