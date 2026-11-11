import Link from "next/link";
import { notFound } from "next/navigation";
import { Icon } from "@/components/icons";
import { CardHead, PageHead } from "@/components/page";
import { CoreError, core } from "@/lib/core";
import { chainHeadline, rowStatus } from "@/lib/evidence";
import { brief, when, words } from "@/lib/format";

// The evidence chain (specs 0037, 0041): every row with its hashes and what Postgres found when
// it recomputed them just now. The page decides nothing; a broken row is named by the core.
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
      <PageHead eyebrow="Control · audit" title="Evidence" lead="Every state change on this dataset, in an append-only log." />

      {broken ? (
        <section className="chain-alarm" aria-labelledby="chain-h" role="alert">
          <Icon name="alert" size={22} />
          <div>
            <h2 id="chain-h">{chain.ok && jobFound ? `Broken at row #${bad ?? "?"}` : chainHeadline(chain)}</h2>
            <p>
              Postgres says: {chain.ok ? check?.detail : chain.detail}.
              {!chain.ok ? <> Rows before #{bad} still check out ({chain.checked} of them); from #{bad} on, the record can&apos;t be trusted.</> : null}
            </p>
            {listed ? <p><a className="button-link" href={`#row-${bad}`}>Jump to row #{bad}</a></p>
              : bad !== null ? <p className="sub">Row #{bad} is not among the rows listed below.</p> : null}
          </div>
        </section>
      ) : (
        <section aria-labelledby="chain-h">
          <h2 id="chain-h" className="sr-only">{chainHeadline(chain)}</h2>
          <dl className="stats" aria-label="Chain status">
            <div className="stat"><dt>Chain</dt><dd><span className="badge tone-ok">intact</span></dd><dd className="stat-sub">{chainHeadline(chain)}</dd></div>
            <div className="stat"><dt>Newest row</dt><dd>#{chain.tip_seq ?? "—"}</dd></div>
            <div className="stat"><dt>Tip hash</dt><dd className="mono stat-mono" title={chain.tip_hash ?? ""}>{chain.tip_hash ? short(chain.tip_hash) : "—"}</dd></div>
            <div className="stat"><dt>Scheduled check</dt>
              <dd className="stat-small">{check?.finished_at ? when(check.finished_at) : "not run yet"}</dd>
              {check?.finished_at ? <dd className="stat-sub">{check.ok ? "intact" : check.ok === false ? `broken at row #${check.first_bad_seq ?? "?"}` : "unknown state"}</dd> : null}
            </div>
          </dl>
        </section>
      )}
      <p className="muted sub chain-notes">
        {!broken ? <>Edit the recorded event in any row and this check fails at that row. </> : null}
        This status was recomputed when the page loaded.{" "}
        {check?.finished_at
          ? <>The scheduled check last ran {when(check.finished_at)} and found the chain {check.ok ? "intact" : check.ok === false ? `broken at row #${check.first_bad_seq ?? "?"}` : "in an unknown state"}.</>
          : "The scheduled check hasn't run on this dataset yet."}
      </p>

      <details className="card how">
        <summary>How the chain catches an edit</summary>
        <figure className="chain-how">
          <ol className="links" aria-hidden="true">
            {["#1", "#2", "#3"].map((n, i) => (
              <li key={n}><span className="link-row"><b>{n}</b><span className="mono">prev_hash {i ? "= hash of " + `#${i}` : "= 000…0"}</span><span className="mono">hash = sha256(prev_hash + sha256(event))</span></span></li>
            ))}
          </ol>
          <figcaption className="muted">
            Each row stores the hash of the row before it, and its own hash covers that link and the recorded event, so a row edited
            by hand stops matching its hash and Postgres names it. A row whose event was edited by hand no longer matches its content
            hash (&ldquo;body edited&rdquo;). A row deleted or moved from the middle breaks the next row&apos;s link (&ldquo;link
            broken&rdquo;); deleting or rewriting the newest rows is caught by the scheduled check, which remembers the newest row it
            last saw. Nobody can update or delete rows through the app: Postgres refuses it. Someone with the database owner&apos;s keys
            can still rewrite a row, which is why every edit is made detectable rather than impossible.
          </figcaption>
        </figure>
      </details>

      <section className="card flush" aria-labelledby="rows-h">
        <CardHead marker="Log" id="rows-h"
          title={<>Rows, newest first <span className="count">{log.total}</span></>}
          meta={log.total > log.events.length ? <span className="muted sub">showing the newest {log.events.length}</span> : null} />
        {log.events.length === 0 ? <p className="card-body muted">No rows yet. The first one is written when a bank change is detected.</p> : (
          <div className="table-wrap">
            <table className="chain-table">
              <thead>
                <tr><th className="num">Row</th><th>Result</th><th>Event</th><th>Vendor</th><th>When</th><th>prev_hash</th><th>content_hash</th><th>hash</th></tr>
              </thead>
              <tbody>
                {log.events.map((e) => {
                  const s = rowStatus(e, bad);
                  const after = bad !== null && e.seq > bad;
                  return (
                    <tr key={e.seq} id={`row-${e.seq}`} className={`${s.tone === "bad" ? "is-bad" : ""}${s.first ? " is-first-bad" : ""}${after ? " is-after" : ""}`}>
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
        <p className="card-body muted sub table-foot">
          Each row&apos;s result is Postgres checking that row&apos;s own hashes and its link to the row before, with the
          chain&apos;s own functions. The status above recomputes the whole chain from the first row and names the first break; the
          scheduled check does the same every few minutes and also compares the newest row with the one it last saw.
        </p>
      </section>
    </main>
  );
}
