"use client";
import OperationContext from "../product/OperationContext";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Frame from "../paypal/Frame";
import { identifier, usd } from "../paypal/contracts";
import { CaseFile, commerce, displayTime, readCase, words } from "./contracts";
import styles from "./commerce.module.css";
const certified = [
  "op_3c78b42e074541dfac3a3ed8456fb59b",
  "op_ad9410eb8f604b28bc6c31f74836e2e9",
];
type Transaction = {
  operation_id: string;
  amount_minor: number;
  currency: string;
  merchant_id: string;
  created_at: string;
};
export default function Operations({
  initialOperation = "",
}: {
  initialOperation?: string;
}) {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [operation, setOperation] = useState(initialOperation);
  const [value, setValue] = useState<CaseFile>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const sequence = useRef(0);
  const load = useCallback(async (id: string) => {
    if (!identifier(id)) {
      setError("Enter an exact accessible operation ID.");
      return;
    }
    const revision = ++sequence.current;
    setBusy(true);
    setError("");
    setOperation(id);
    setValue(undefined);
    try {
      const next = readCase(await commerce<CaseFile>(`cases/${id}`), id);
      if (revision === sequence.current) setValue(next);
    } catch (failure) {
      if (revision === sequence.current)
        setError(
          failure instanceof Error
            ? failure.message
            : "Case unavailable. Obligations remain unchanged.",
        );
    } finally {
      if (revision === sequence.current) setBusy(false);
    }
  }, []);
  useEffect(() => {
    let active = true;
    const revision = sequence.current;
    const lifecycle = sequence;
    void commerce<{ transactions: Transaction[] }>("transactions")
      .then((result) => {
        if (!active) return;
        const list = [...result.transactions].sort((a, b) => {
          const aa = certified.indexOf(a.operation_id),
            bb = certified.indexOf(b.operation_id);
          return (aa < 0 ? 99 : aa) - (bb < 0 ? 99 : bb);
        });
        setTransactions(list);
        const selected =
          initialOperation &&
          list.some((t) => t.operation_id === initialOperation)
            ? initialOperation
            : list[0]?.operation_id;
        if (selected && sequence.current === revision) void load(selected);
      })
      .catch(() => {
        if (active)
          setCatalogError(
            "Sign in with an authorized purchase role, or check case-service availability. No public transaction dataset is provided.",
          );
      });
    return () => {
      active = false;
      lifecycle.current++;
    };
  }, [initialOperation, load]);
  function submit(e: FormEvent) {
    e.preventDefault();
    void load(operation);
  }
  return (
    <Frame>
      <OperationContext operation={value?.operation_id} />
      <main className={styles.main}>
        <div className={styles.header}>
          <div>
            <p className={styles.eyebrow}>
              READ-ONLY OPERATIONS / HISTORICAL SANDBOX EVIDENCE
            </p>
            <h1>Operations inbox</h1>
            <p>
              Review original terms, retained capacity, recorded payments and
              unresolved findings. A current plan is separate from these
              recorded purchases.
            </p>
          </div>
          <Link href="/workspace">Plan a purchase ↗</Link>
        </div>
        <div className={styles.layout}>
          <aside className={styles.inbox} aria-label="Accessible operations">
            <h2>Recorded purchases</h2>
            {catalogError && <p className={styles.muted}>{catalogError}</p>}
            {transactions.map((t) => (
              <button
                key={t.operation_id}
                aria-pressed={value?.operation_id === t.operation_id}
                disabled={busy}
                onClick={() => void load(t.operation_id)}
              >
                <strong>
                  {certified.indexOf(t.operation_id) === 0
                    ? "Completed sandbox purchase"
                    : certified.indexOf(t.operation_id) === 1
                      ? "Unresolved sandbox purchase"
                      : "Recorded operation"}
                </strong>
                <span>{usd(t.amount_minor)}</span>
                <code>{t.operation_id}</code>
                <small>Admitted {displayTime(t.created_at)}</small>
              </button>
            ))}
            <form onSubmit={submit}>
              <label>
                Exact operation ID
                <input
                  value={operation}
                  maxLength={100}
                  onChange={(e) => setOperation(e.target.value)}
                />
              </label>
              <button disabled={busy || !operation}>Load case file</button>
            </form>
          </aside>
          <article className={styles.case} aria-label="Operation case file">
            {busy && (
              <p role="status">
                Reading scoped source records. No financial action is being
                performed.
              </p>
            )}
            {error && (
              <p role="alert" className={styles.warning}>
                {error}
              </p>
            )}
            {value ? (
              <>
                <div className={styles.caseTop}>
                  <div>
                    <p className={styles.eyebrow}>
                      AUTHENTICATED CASE / {value.commercial.currency}
                    </p>
                    <h2>{words(value.status)}</h2>
                    <code>{value.operation_id}</code>
                  </div>
                  <div className={styles.exports}>
                    <a
                      className={styles.exportLink}
                      href={`/api/commerce/cases/${value.operation_id}/export.json`}
                      download
                    >
                      Export JSON ↓
                    </a>
                    <a
                      className={styles.exportLink}
                      href={`/api/commerce/cases/${value.operation_id}/export.html`}
                      download
                    >
                      Readable report ↓
                    </a>
                  </div>
                </div>
                <p className={styles.caseIntro}>
                  Sources read {displayTime(value.read_at)}. Provider
                  observation{" "}
                  {displayTime(value.provider.last_canonical_read_at)}.
                  Historical evidence; current PayPal state is not established.
                </p>
                <div
                  className={styles.nextAction}
                  aria-label="Next permissible action"
                >
                  Required operator review:{" "}
                  <strong>{words(value.next_action)}</strong>. This case file
                  grants no capture, retry or release permission.
                </div>
                <section
                  className={styles.sources}
                  aria-label="Case financial evidence"
                >
                  <div>
                    <small>I / ORIGINAL TERMS</small>
                    <strong>{usd(value.commercial.amount_minor)}</strong>
                    <span>{value.commercial.quantity} × workspace-seat</span>
                  </div>
                  <div>
                    <small>II / RECORDED PROVIDER</small>
                    <strong>
                      {value.provider.capture_confirmed
                        ? "Capture verified"
                        : "Capture unverified"}
                    </strong>
                    <span>
                      Order:{" "}
                      {value.provider.provider_order_status ?? "unavailable"}
                    </span>
                  </div>
                  <div>
                    <small>III / BOOKED</small>
                    <strong>
                      {value.economic.journals.length} journal groups
                    </strong>
                    <span>
                      {value.economic.facts.length} economic facts /{" "}
                      {value.economic.journals_balanced === true
                        ? "balanced"
                        : value.economic.journals_balanced === false
                          ? "imbalance"
                          : "balance unestablished"}
                    </span>
                  </div>
                  <div>
                    <small>IV / FULFILLED</small>
                    <strong>
                      {value.seller.fulfillment_recorded
                        ? "Recorded seller fulfillment"
                        : "Fulfillment unverified"}
                    </strong>
                    <span>No new seller contact</span>
                  </div>
                </section>
                <Link
                  className={styles.investigate}
                  href={`/intelligence?operation=${value.operation_id}`}
                >
                  Investigate this recorded case in AG Studio <span>↗</span>
                </Link>
                {value.operational_cautions?.map((note) => (
                  <p key={note} className={styles.warning}>
                    {note}
                  </p>
                ))}
                <h3>Original terms and current authority limits</h3>
                <dl className={styles.facts}>
                  <dt>Product / beneficiary</dt>
                  <dd>
                    {value.commercial.lines
                      .map((l) => `${l.product_id} for ${l.beneficiary}`)
                      .join(", ")}
                  </dd>
                  <dt>Seller / merchant</dt>
                  <dd>
                    {value.commercial.seller} / {value.commercial.merchant_id}
                  </dd>
                  <dt>Original quote expiry</dt>
                  <dd>
                    {displayTime(value.commercial.quote_expires_at)} /{" "}
                    {value.commercial.quote_expired
                      ? "expired"
                      : "recorded deadline"}
                  </dd>
                  <dt>Original authority</dt>
                  <dd>
                    {value.authority.current_original_authority
                      ? "Source currently reports fresh; capture still separately guarded"
                      : "Expired or stale; no current capture authority"}
                  </dd>
                  <dt>Approval evidence</dt>
                  <dd>
                    {value.authority.approval_details.visible
                      ? words(value.authority.approval_details.status)
                      : "Bound approval identity; details permission-limited"}
                  </dd>
                  <dt>Grant</dt>
                  <dd>
                    {words(value.authority.grant_effective_status)} / expires{" "}
                    {displayTime(value.authority.grant_expires_at)}
                  </dd>
                  <dt>Reservation</dt>
                  <dd>
                    {usd(value.authority.reservation.amount_minor)} /{" "}
                    {words(value.authority.reservation.status)}
                  </dd>
                  <dt>Dispatch exposure</dt>
                  <dd>
                    {words(value.authority.reservation.dispatch_exposure_state)}
                  </dd>
                  <dt>Lifetime budget</dt>
                  <dd>
                    Bound {usd(value.authority.lifetime_budget.bound)} /
                    consumed {usd(value.authority.lifetime_budget.consumed)} /
                    reserved {usd(value.authority.lifetime_budget.reserved)} /
                    free {usd(value.authority.lifetime_budget.free)}
                  </dd>
                </dl>
                <p className={styles.muted}>
                  Lifetime reserved capacity and dispatch exposure are separate
                  records. Neither an old APPROVED observation nor missing
                  capture evidence proves payment failure or permits releasing a
                  hold.
                </p>
                <h3>Stored reconciliation</h3>
                {value.reconciliation.available ? (
                  <>
                    <p className={styles.muted}>
                      {value.reconciliation.runs.length} visible stored run(s),{" "}
                      {value.reconciliation.findings.length} visible finding(s)
                      within bounded history. Run completion does not resolve
                      open findings.
                    </p>
                    {value.reconciliation.findings.map((f) => (
                      <div className={styles.findings} key={f.id}>
                        <strong>
                          {f.class} / {f.state}
                        </strong>
                        <p>Stored next step: {f.next_step}</p>
                        <code>{f.id}</code>
                      </div>
                    ))}
                    {value.reconciliation.runs.map((r) => (
                      <p className={styles.muted} key={r.id}>
                        Stored run {r.state} / {displayTime(r.completed_at)} /{" "}
                        <code>{r.id}</code>
                      </p>
                    ))}
                  </>
                ) : (
                  <p className={styles.warning}>
                    Stored reconciliation is unavailable to this reader. An
                    empty visible list is not reconciliation proof.
                  </p>
                )}
                {value.reconciliation.gaps.length > 0 && (
                  <p className={styles.warning}>
                    Recorded comparison gaps:{" "}
                    {value.reconciliation.gaps.map(words).join("; ")}.
                  </p>
                )}
                <h3>Source timeline</h3>
                <p className={styles.muted}>
                  Timestamps and actors come from exposed source fields. Undated
                  records appear last; no payment-event time or human action is
                  inferred.
                </p>
                <ol className={styles.timeline}>
                  {value.timeline.map((event, i) => (
                    <li key={event.kind + event.source_id + i}>
                      <time>{displayTime(event.at)}</time>
                      <strong>{words(event.kind)}</strong>
                      <p>
                        {event.source_system} / actor:{" "}
                        {event.actor ?? "not exposed"}
                      </p>
                      <code>
                        {event.source_id ?? "Source identifier unavailable"}
                      </code>
                      {event.note && <p>{event.note}</p>}
                    </li>
                  ))}
                </ol>
                <details>
                  <summary>Economic facts and journal links</summary>
                  <div className={styles.tableScroll}>
                    <table>
                      <thead>
                        <tr>
                          <th>Kind / template</th>
                          <th>Source ID</th>
                          <th>Linked fact / effect</th>
                          <th>USD</th>
                        </tr>
                      </thead>
                      <tbody>
                        {value.economic.facts.map((f) => (
                          <tr key={f.id}>
                            <td>{f.kind}</td>
                            <td>{f.id}</td>
                            <td>{f.effect_id}</td>
                            <td>{usd(f.amount_minor)}</td>
                          </tr>
                        ))}
                        {value.economic.journals.map((j) => (
                          <tr key={j.id}>
                            <td>{j.template}</td>
                            <td>{j.id}</td>
                            <td>{j.fact_id}</td>
                            <td>{usd(j.amount_minor)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
                <details>
                  <summary>
                    Source identities, integrity and unavailable evidence
                  </summary>
                  <dl className={styles.facts}>
                    {Object.entries({
                      action: value.authority.action_id,
                      evidence: value.authority.evidence_id,
                      decision: value.authority.decision_id,
                      approval: value.authority.approval_id,
                      reservation: value.authority.reservation.id,
                      order: value.provider.provider_order_id,
                      capture: value.provider.capture_id,
                      ...value.integrity,
                      package_sha256: value.package_sha256,
                    }).map(([k, v]) => (
                      <div key={k} style={{ display: "contents" }}>
                        <dt>{words(k)}</dt>
                        <dd>
                          <code>{v ?? "Unavailable"}</code>
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <ul>
                    {value.unavailable.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </details>
                <footer>
                  Sanitized source package. Existing hashes identify recorded
                  content; they do not prove an externally trusted signature. No
                  private buyer details, payment credentials or model reasoning
                  are included.
                </footer>
              </>
            ) : (
              !busy &&
              !error && (
                <p className={styles.muted}>
                  Select an accessible operation. Authentication and existing
                  tenant/role scope apply to every case and export.
                </p>
              )
            )}
          </article>
        </div>
      </main>
    </Frame>
  );
}
