import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHead } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { chainHeadline, rowStatus } from "@/lib/evidence";
import { brief, when, words } from "@/lib/format";

// The evidence chain (spec 0037): every row with its hashes and what Postgres found when it
// recomputed them just now. The page decides nothing; a broken row is named by the core.
const short = (h: string) => `${h.slice(0, 10)}…`;

export default async function EvidencePage({ params }: { params: Promise<{ datasetId: string }> }) {
  const { datasetId } = await params;
  const log = await core.evidence(datasetId).catch((e) => {
    if (e instanceof CoreError && e.status === 404) notFound();
    throw e;
  });
  const { chain, last_check: check } = log;
  const bad = chain.first_bad_seq;
  return (
    <main>
      <PageHead
        title="Evidence chain"
        lead="Every state change on this dataset, in an append-only log. Each row's hash covers its own body and the row before it, so a row edited by hand no longer matches its hash, and Postgres says which row."
      />

      <section className={`card lead-card ${chain.ok ? "edge-ok" : "edge-bad chain-alarm"}`} aria-labelledby="chain-h" role={chain.ok ? undefined : "alert"}>
        <h2 id="chain-h">
          <span className={`badge ${chain.ok ? "tone-ok" : "tone-bad"}`}>{chain.ok ? "intact" : "broken"}</span>
          {chainHeadline(chain)}
        </h2>
        {chain.ok ? (
          <p className="muted">
            The newest row is #{chain.tip_seq ?? "—"}, hash <span className="mono" title={chain.tip_hash ?? ""}>{chain.tip_hash ? short(chain.tip_hash) : "—"}</span>.
            Change one character in any row and this check fails at that row.
          </p>
        ) : (
          <>
            <p>Postgres says: {chain.detail}. Rows before #{bad} still check out ({chain.checked} of them); from #{bad} on, the record can&apos;t be trusted.</p>
            <p><a className="button-link" href={`#row-${bad}`}>Jump to row #{bad}</a></p>
          </>
        )}
        <p className="muted sub">
          {check?.finished_at
            ? <>The scheduled check last ran {when(check.finished_at)} and found the chain {check.ok ? "intact" : check.ok === false ? "broken" : "in an unknown state"}.</>
            : "The scheduled check hasn't run on this dataset yet."}
          {" "}This card was recomputed when the page loaded.
        </p>
      </section>

      <figure className="card chain-how">
        <ol className="links" aria-hidden="true">
          {["#1", "#2", "#3"].map((n, i) => (
            <li key={n}><span className="link-row"><b>{n}</b><span className="mono">prev {i ? "= hash of " + `#${i}` : "= 000…0"}</span><span className="mono">hash = sha256(prev + body)</span></span></li>
          ))}
        </ol>
        <figcaption className="muted">
          Each row stores the hash of the row before it, and its own hash covers that link and its body. A row edited by hand no
          longer matches its own hash (&ldquo;body edited&rdquo;); a row deleted or reordered no longer matches its neighbour
          (&ldquo;link broken&rdquo;). Nobody can update or delete rows through the app: Postgres refuses it.
        </figcaption>
      </figure>

      <section className="card flush" aria-labelledby="rows-h">
        <h2 id="rows-h" className="card-head">
          Rows, newest first <span className="count">{log.total}</span>
          {log.total > log.events.length ? <span className="h-note">showing the newest {log.events.length}</span> : null}
        </h2>
        {log.events.length === 0 ? <p className="card-head muted">No rows yet. The first one is written when a bank change is detected.</p> : (
          <div className="table-wrap">
            <table className="chain-table">
              <thead>
                <tr><th className="num">Row</th><th>Result</th><th>Event</th><th>Vendor</th><th>When</th><th>prev_hash</th><th>content_hash</th><th>hash</th></tr>
              </thead>
              <tbody>
                {log.events.map((e) => {
                  const s = rowStatus(e, bad);
                  return (
                    <tr key={e.seq} id={`row-${e.seq}`} className={`${s.tone === "bad" ? "is-bad" : ""}${s.first ? " is-first-bad" : ""}`}>
                      <td className="num mono">#{e.seq}</td>
                      <td>
                        <span className={`badge tone-${s.tone}`}>{s.word}</span>
                        {s.first ? <div className="cell-note bad-note">The first bad row</div> : null}
                      </td>
                      <td>{words(e.type)}</td>
                      <td>{e.vendor && e.case_id ? <Link href={`/d/${datasetId}/cases/${e.case_id}`}>{e.vendor}</Link> : <span className="muted">—</span>}</td>
                      <td className="nowrap" title={when(e.occurred_at)}>{brief(e.occurred_at)}</td>
                      <td className="mono muted" title={e.prev_hash}>{short(e.prev_hash)}</td>
                      <td className="mono muted" title={e.content_hash}>{short(e.content_hash)}</td>
                      <td className="mono" title={e.hash}>{short(e.hash)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="card-head muted sub">
          Each row&apos;s result is Postgres checking that row alone, with the chain&apos;s own hash functions. The scheduled check
          walks the chain from the first row and stops at the first break, which is the row the card above names.
        </p>
      </section>
    </main>
  );
}
