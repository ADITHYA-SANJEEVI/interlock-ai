"use client";
import OperationContext from "../product/OperationContext";
import Link from "next/link";
import dynamic from "next/dynamic";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Frame from "./Frame";
import { ApiError, Proof, api, identifier, readProof, usd } from "./contracts";
import { evidenceDatasets } from "./evidence-datasets";
import styles from "./intelligence.module.css";

const EvidenceStudio = dynamic(() => import("./EvidenceStudio"), {
  ssr: false,
  loading: () => (
    <p role="status" className={styles.loading}>
      Loading the interactive report…
    </p>
  ),
});
const certified = [
  "op_3c78b42e074541dfac3a3ed8456fb59b",
  "op_ad9410eb8f604b28bc6c31f74836e2e9",
];
type Transaction = {
  operation_id: string;
  amount_minor: number;
  currency: string;
};
const observedAt = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        timeZoneName: "short",
      })
    : "No recorded provider read";
const caseName = (proof?: Proof) =>
  !proof
    ? "Recorded operation"
    : proof.blocked
      ? "Conflicting evidence"
      : proof.capture_confirmed
        ? "Captured sandbox purchase"
        : proof.provider_order_status === "APPROVED"
          ? "Approved · capture unverified"
          : "Unverified sandbox purchase";

export default function FinancialIntelligence({
  initialOperation = "",
  licenseKey,
}: {
  initialOperation?: string;
  licenseKey?: string;
}) {
  const [operation, setOperation] = useState(initialOperation);
  const [proof, setProof] = useState<Proof>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [previews, setPreviews] = useState<Record<string, Proof>>({});
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [reader, setReader] = useState<string>();
  const request = useRef(0);
  const datasets = proof ? evidenceDatasets(proof) : undefined;
  const unavailable = Boolean(
    error ||
    busy ||
    (proof?.provider_order_id && !proof.last_canonical_read_at),
  );
  const verified = proof?.settlement === "VERIFIED_CAPTURE" && !unavailable;
  const cancelled = proof?.settlement === "CANCELLED_NEVER_SENT";
  const state =
    proof?.blocked && !cancelled
      ? "Conflict hold"
      : unavailable
        ? "Current evidence unavailable"
        : cancelled
          ? "Cancelled before handoff; no payment sent"
          : verified
            ? "Recorded capture verified"
            : proof?.capture_committed
              ? "Capture outcome unresolved"
              : "Capture not verified";

  const read = useCallback(async (op: string) => {
    if (!identifier(op)) {
      setError("Enter an exact protected operation ID.");
      return;
    }
    const sequence = ++request.current;
    setBusy(true);
    setOperation(op);
    setError("");
    try {
      const value = readProof(await api<Proof>(`paypal/evidence/${op}`));
      if (value.operation_id !== op)
        throw new Error("Operation identity mismatch.");
      if (sequence === request.current) {
        setProof(value);
        setPreviews((prior) => ({ ...prior, [op]: value }));
      }
    } catch (failure) {
      if (sequence === request.current)
        setError(
          failure instanceof Error
            ? failure.message
            : "Evidence unavailable. Existing obligations remain unresolved.",
        );
    } finally {
      if (sequence === request.current) setBusy(false);
    }
  }, []);

  const discover = useCallback(
    async (autoSelect: boolean) => {
      const selectionRevision = request.current;
      setCatalogBusy(true);
      setCatalogError("");
      try {
        const identity = await api<{ username: string }>("me");
        const value = await api<{ transactions: Transaction[] }>(
          "investigations/transactions",
        );
        setReader(identity.username);
        setTransactions(value.transactions);
        // Known demo labels/data appear only after scoped discovery and actual reads.
        const visibleCases = certified.filter((id) =>
          value.transactions.some((t) => t.operation_id === id),
        );
        const results = await Promise.allSettled(
          visibleCases.map(async (id) => {
            const p = readProof(await api<Proof>(`paypal/evidence/${id}`));
            if (p.operation_id !== id)
              throw new Error("Operation identity mismatch.");
            return p;
          }),
        );
        const loaded: Record<string, Proof> = {};
        for (const result of results)
          if (result.status === "fulfilled")
            loaded[result.value.operation_id] = result.value;
        setPreviews((prior) => ({ ...prior, ...loaded }));
        if (autoSelect && selectionRevision === request.current) {
          const selected =
            initialOperation &&
            value.transactions.some((t) => t.operation_id === initialOperation)
              ? initialOperation
              : visibleCases[0];
          if (selected && loaded[selected]) {
            setOperation(selected);
            setProof(loaded[selected]);
          } else if (selected) await read(selected);
        }
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 401) {
          setReader(undefined);
          setTransactions([]);
          setPreviews({});
          setProof(undefined);
        }
        setCatalogError(
          failure instanceof ApiError && failure.status === 401
            ? "Sign in to view your transactions."
            : "Transaction discovery is unavailable. You can load a known operation within your existing scope.",
        );
      } finally {
        setCatalogBusy(false);
      }
    },
    [initialOperation, read],
  );
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) void discover(true);
    });
    return () => {
      active = false;
    };
  }, [discover]);
  function load(event: FormEvent) {
    event.preventDefault();
    void read(operation);
  }

  return (
    <Frame>
      <OperationContext operation={proof?.operation_id} />
      <main className={styles.main}>
        <div className={styles.heading}>
          <div>
            <p className={styles.eyebrow}>READ-ONLY OPERATIONS</p>
            <h1>Financial Intelligence</h1>
          </div>
          <div className={styles.reader}>
            {reader ? (
              <>
                <i />
                {reader}
                <span>Scoped session</span>
              </>
            ) : (
              <Link href="/workspace">Sign in to workspace ↗</Link>
            )}
          </div>
        </div>
        <div className={styles.layout}>
          <aside className={styles.sidebar} aria-label="Transaction selection">
            <div className={styles.sideTitle}>
              <h2>Transactions</h2>
              <span>USD / SANDBOX</span>
            </div>
            <button
              className={styles.discovery}
              type="button"
              disabled={catalogBusy}
              onClick={() => void discover(false)}
            >
              {catalogBusy
                ? "Reading scoped transactions…"
                : "Find accessible transactions"}
              <span aria-hidden="true">↻</span>
            </button>
            {catalogError && (
              <p className={styles.catalogNote}>{catalogError}</p>
            )}
            {certified
              .flatMap((id) =>
                transactions.filter((t) => t.operation_id === id),
              )
              .map((t) => (
                <button
                  key={t.operation_id}
                  className={styles.transaction}
                  aria-pressed={proof?.operation_id === t.operation_id}
                  disabled={busy}
                  onClick={() => void read(t.operation_id)}
                >
                  <span className={styles.transactionTop}>
                    {previews[t.operation_id]?.capture_confirmed ? (
                      <i data-state="recorded" />
                    ) : (
                      <i />
                    )}
                    <span>RECORDED DEMO</span>
                    <b>{usd(t.amount_minor)}</b>
                  </span>
                  <strong>{caseName(previews[t.operation_id])}</strong>
                  <code>{t.operation_id}</code>
                  <small>
                    {previews[t.operation_id]
                      ? `${previews[t.operation_id].facts.length} facts · ${previews[t.operation_id].journals.length} journals`
                      : "Load authenticated evidence"}
                  </small>
                </button>
              ))}
            {transactions.length > 0 && (
              <label className={styles.selectorLabel}>
                Accessible transaction
                <select
                  aria-label="Accessible transaction"
                  value={
                    transactions.some((t) => t.operation_id === operation)
                      ? operation
                      : ""
                  }
                  onChange={(e) => void read(e.target.value)}
                >
                  <option value="" disabled>
                    Select a recorded transaction
                  </option>
                  {transactions.map((t) => (
                    <option key={t.operation_id} value={t.operation_id}>
                      {caseName(previews[t.operation_id])} · {t.operation_id}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <form className={styles.lookup} onSubmit={load}>
              <label htmlFor="intelligence-operation">
                Protected operation ID
              </label>
              <input
                id="intelligence-operation"
                value={operation}
                onChange={(e) => setOperation(e.target.value)}
                maxLength={100}
                autoComplete="off"
                placeholder="Load an operation within your scope"
              />
              <button disabled={busy || !operation.trim()}>
                {busy ? "Reading evidence…" : "Load evidence"}
              </button>
            </form>
            <div className={styles.scopeNote}>
              <span>READ-ONLY BY DESIGN</span>
              <p>
                No payment tools. No PayPal requests. Your authenticated scope
                applies to every record.
              </p>
              <Link
                href={
                  proof
                    ? `/operations?operation=${proof.operation_id}`
                    : "/operations"
                }
              >
                Operations inbox &amp; case files ↗
              </Link>
              <Link href="/workspace">Plan a purchase ↗</Link>
            </div>
          </aside>
          <div className={styles.analysis}>
            {error && (
              <p role="alert" className={styles.warning}>
                {error}{" "}
                {proof &&
                  "The displayed records are historical; current state could not be refreshed."}
              </p>
            )}
            {proof ? (
              <>
                <div className={styles.caseHeading}>
                  <div>
                    <p className={styles.eyebrow}>
                      AUTHENTICATED SANDBOX RECORD
                    </p>
                    <h2>{caseName(proof)}</h2>
                    <code>{proof.operation_id}</code>
                  </div>
                  <span
                    className={
                      proof.blocked && !cancelled
                        ? styles.conflict
                        : verified
                          ? styles.verified
                          : styles.pending
                    }
                  >
                    {state}
                  </span>
                </div>
                <div className={styles.freshness}>
                  <span>Recorded provider observation</span>
                  <time dateTime={proof.last_canonical_read_at ?? undefined}>
                    {observedAt(proof.last_canonical_read_at)}
                  </time>
                  <b>Historical · current PayPal state not established</b>
                </div>
                <section
                  className={styles.evidenceMatrix}
                  aria-label="Financial evidence overview"
                >
                  {[
                    [
                      "I",
                      "Intended",
                      usd(proof.amount_minor),
                      "Bound action & approval",
                    ],
                    [
                      "II",
                      "Observed",
                      unavailable
                        ? "Unavailable"
                        : proof.capture_confirmed
                          ? "Capture recorded"
                          : (proof.provider_order_status ?? "Unverified"),
                      "Authenticated PayPal observation",
                    ],
                    [
                      "III",
                      "Booked",
                      `${proof.journals.length} posting groups`,
                      `${proof.facts.length} economic facts`,
                    ],
                    [
                      "IV",
                      "Fulfilled",
                      proof.seller_fulfilled && !proof.blocked
                        ? "Seller attested"
                        : "Not established",
                      "Independent seller evidence",
                    ],
                  ].map(([n, title, value, note]) => (
                    <div key={n}>
                      <span>
                        {n} / {title}
                      </span>
                      <strong>{value}</strong>
                      <small>{note}</small>
                    </div>
                  ))}
                </section>
                <EvidenceStudio
                  key={proof.operation_id}
                  proof={proof}
                  historical={unavailable}
                  licenseKey={licenseKey}
                />
                <details className={styles.binding}>
                  <summary>Purchase identities & recorded observation</summary>
                  <dl>
                    <dt>Merchant</dt>
                    <dd>{proof.merchant_id}</dd>
                    <dt>Approval</dt>
                    <dd>{proof.approval_id}</dd>
                    <dt>Reservation record</dt>
                    <dd>{proof.reservation_id}</dd>
                    <dt>Recorded provider read</dt>
                    <dd>{proof.last_canonical_read_at ?? "Unavailable"}</dd>
                  </dl>
                </details>
              </>
            ) : (
              <div className={styles.empty}>
                <div className={styles.emptyDiagram} aria-hidden="true">
                  <i />
                  <i />
                  <i />
                  <i />
                </div>
                <p className={styles.eyebrow}>
                  AUTHENTICATED EVIDENCE REQUIRED
                </p>
                <h2>Select a transaction to investigate.</h2>
                <p>
                  Compare authorized terms, recorded PayPal observations,
                  accounting and fulfillment. The investigation prompt and
                  Studio report become available when your scoped evidence
                  loads.
                </p>
                <Link href="/workspace">Sign in to your workspace ↗</Link>
              </div>
            )}
            <details className={styles.sourceDisclosure}>
              <summary>Source records & export</summary>
              <section id="source-records" className={styles.records}>
                <div className={styles.sectionHead}>
                  <div>
                    <p className={styles.eyebrow}>RECORD EXPLORER</p>
                    <h2>Follow each financial fact.</h2>
                  </div>
                  <span>Authenticated response only</span>
                </div>
                <div className={styles.tableWrap}>
                  <table>
                    <caption>Economic facts</caption>
                    <thead>
                      <tr>
                        <th>Fact</th>
                        <th>Kind</th>
                        <th>Amount</th>
                        <th>Currency</th>
                        <th>Effect identity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datasets?.economicFacts.length ? (
                        datasets.economicFacts.map((f) => (
                          <tr key={f.id}>
                            <td>
                              <code>{f.id}</code>
                            </td>
                            <td>{f.kind}</td>
                            <td>{usd(f.amount_minor)}</td>
                            <td>{f.currency}</td>
                            <td>
                              <code>{f.effect_id}</code>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={5} className={styles.empty}>
                            No authenticated economic facts to display.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className={styles.tableWrap}>
                  <table>
                    <caption>Journal lines</caption>
                    <thead>
                      <tr>
                        <th>Posting template</th>
                        <th>Account</th>
                        <th>Side</th>
                        <th>Amount</th>
                        <th>Journal identity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datasets?.journalLines.length ? (
                        datasets.journalLines.map((l) => (
                          <tr key={l.line_id}>
                            <td>{l.template}</td>
                            <td>{l.account}</td>
                            <td>{l.side}</td>
                            <td>{usd(l.amount_minor)}</td>
                            <td>
                              <code>{l.journal_id}</code>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={5} className={styles.empty}>
                            No journal lines available. Provider status cannot
                            substitute for booking.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <p className={styles.note}>
                  Amounts across facts and posting templates are intentionally
                  kept separate. Adding authorization, capture, liability and
                  revenue would count different realities together.
                </p>
              </section>
              {proof && (
                <details className={styles.export}>
                  <summary>Inspect read-only evidence datasets</summary>
                  <p>
                    These datasets preserve source identities. They contain no
                    financial commands or credential material.
                  </p>
                  <pre>{JSON.stringify(datasets, null, 2)}</pre>
                </details>
              )}
            </details>
          </div>
        </div>
      </main>
    </Frame>
  );
}
