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
  // Broken if the recompute just now found a break, or the scheduled check did (it also catches
  // deleted or rewritten newest rows, which a recompute alone can't). Both verdicts are the core's.
  const jobFound = check?.ok === false;
  const broken = !chain.ok || jobFound;
  const bad = chain.first_bad_seq ?? (jobFound ? check?.first_bad_seq ?? null : null);
  const listed = bad !== null && log.events.some((e) => e.seq === bad);
  return (
    <main>
      <PageHead
        title="Evidence chain"
        lead="Every state change on this dataset, in an append-only log. Each row stores the hash of the row before it, and its own hash covers that link and the recorded event, so a row edited by hand stops matching its hash and Postgres names it."
      />

      <section className={`card lead-card ${broken ? "edge-bad chain-alarm" : "edge-ok"}`} aria-labelledby="chain-h" role={broken ? "alert" : undefined}>
        <h2 id="chain-h">
          <span className={`badge ${broken ? "tone-bad" : "tone-ok"}`}>{broken ? "broken" : "intact"}</span>
          {chain.ok && jobFound ? `Broken at row #${bad ?? "?"}` : chainHeadline(chain)}
        </h2>
        {!broken ? (
          <p className="muted">
            The newest row is #{chain.tip_seq ?? "—"}, hash <span className="mono" title={chain.tip_hash ?? ""}>{chain.tip_hash ? short(chain.tip_hash) : "—"}</span>.
            Edit the recorded event in any row and this check fails at that row.
          </p>
        ) : (
          <>
            <p>
              Postgres says: {chain.ok ? check?.detail : chain.detail}.
              {!chain.ok ? <> Rows before #{bad} still check out ({chain.checked} of them); from #{bad} on, the record can&apos;t be trusted.</> : null}
            </p>
            {listed ? <p><a className="button-link" href={`#row-${bad}`}>Jump to row #{bad}</a></p>
              : bad !== null ? <p className="muted sub">Row #{bad} is not among the rows listed below.</p> : null}
          </>
        )}
        <p className="muted sub">
          This card was recomputed when the page loaded.{" "}
          {check?.finished_at
            ? <>The scheduled check last ran {when(check.finished_at)} and found the chain {check.ok ? "intact" : check.ok === false ? `broken at row #${check.first_bad_seq ?? "?"}` : "in an unknown state"}.</>
            : "The scheduled check hasn't run on this dataset yet."}
        </p>
      </section>

      <figure className="card chain-how">
        <ol className="links" aria-hidden="true">
          {["#1", "#2", "#3"].map((n, i) => (
            <li key={n}><span className="link-row"><b>{n}</b><span className="mono">prev_hash {i ? "= hash of " + `#${i}` : "= 000…0"}</span><span className="mono">hash = sha256(prev_hash + sha256(event))</span></span></li>
          ))}
        </ol>
        <figcaption className="muted">
          Each row stores the hash of the row before it, and its own hash covers that link and the recorded event. A row whose
          event was edited by hand no longer matches its content hash (&ldquo;body edited&rdquo;). A row deleted or moved from
          the middle breaks the next row&apos;s link (&ldquo;link broken&rdquo;); deleting or rewriting the newest rows is caught by
          the scheduled check, which remembers the newest row it last saw. Nobody can update or delete rows through the app:
          Postgres refuses it. Someone with the database owner&apos;s keys can still rewrite a row, which is why every edit is
          made detectable rather than impossible.
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
          Each row&apos;s result is Postgres checking that row&apos;s own hashes and its link to the row before, with the
          chain&apos;s own functions. The card above recomputes the whole chain from the first row and names the first break; the
          scheduled check does the same every few minutes and also compares the newest row with the one it last saw.
        </p>
      </section>
    </main>
  );
}
