import { Proof, usd } from "./contracts";
import Link from "next/link";
import styles from "./paypal.module.css";

export default function Realities({
  proof,
  stale,
}: {
  proof?: Proof;
  stale: boolean;
}) {
  const cancelled = proof?.settlement === "CANCELLED_NEVER_SENT";
  const conflict = proof?.blocked && !cancelled;
  const verified = proof?.settlement === "VERIFIED_CAPTURE" && !stale;
  const state = conflict
    ? "Conflict hold"
    : stale
      ? "Refresh unavailable"
      : cancelled
        ? "Cancelled before handoff"
        : verified
          ? "Capture verified"
          : proof?.capture_committed
            ? "Capture outcome unknown"
            : "Awaiting canonical proof";
  return (
    <section
      id="realities"
      className={styles.evidenceSection}
      aria-labelledby="realities-title"
    >
      <div className={styles.sectionHeading}>
        <div>
          <p className={styles.eyebrow}>FINANCIAL EVIDENCE</p>
          <h2 id="realities-title">Four Realities</h2>
          <p>Compare authorization, payment, accounting and fulfillment.</p>
        </div>
        <span
          className={`${styles.badge} ${conflict ? styles.danger : verified ? styles.success : styles.pending}`}
        >
          {state}
        </span>
      </div>
      {stale && (
        <p className={styles.warning} role="status">
          Current canonical evidence is unavailable. Any records below are
          historical evidence; current state is unavailable. Existing
          obligations remain unresolved.
        </p>
      )}
      <div className={styles.realities}>
        <article>
          <span className={styles.realityNumber}>I</span>
          <h3>Intended</h3>
          <p>What was authorized?</p>
          <strong>
            {proof ? usd(proof.amount_minor) : "No admitted operation"}
          </strong>
          <details>
            <summary>Authority identities</summary>
            <dl>
              <dt>Action</dt>
              <dd>{proof?.action_id ?? "Unavailable"}</dd>
              <dt>Approval</dt>
              <dd>{proof?.approval_id ?? "Unavailable"}</dd>
              <dt>Reservation</dt>
              <dd>{proof?.reservation_id ?? "Unavailable"}</dd>
            </dl>
          </details>
          <small>
            {cancelled
              ? "Local cancellation released this reservation. The original approval remains consumed; no payment was sent."
              : "Reservation records allocation, not settlement. Unknown outcomes retain obligations."}
          </small>
        </article>
        <article>
          <span className={styles.realityNumber}>II</span>
          <h3>Observed</h3>
          <p>What did PayPal confirm?</p>
          <strong>
            {proof
              ? cancelled
                ? "No provider handoff"
                : conflict
                  ? "Conflict hold"
                  : proof.capture_confirmed
                    ? stale
                      ? "Previously verified capture"
                      : "Canonical capture verified"
                    : proof.capture_committed
                      ? "Unknown capture outcome"
                      : (proof.provider_order_status ?? "No canonical read")
              : "No provider evidence"}
          </strong>
          <details>
            <summary>Provider observation & identities</summary>
            <dl>
              <dt>Order</dt>
              <dd>{proof?.provider_order_id ?? "Not observed"}</dd>
              <dt>Capture</dt>
              <dd>{proof?.capture_id ?? "Not verified"}</dd>
              <dt>Canonical receipt</dt>
              <dd>{proof?.receipt_id ?? "Unavailable"}</dd>
              <dt>Read time</dt>
              <dd>{proof?.last_canonical_read_at ?? "Unavailable"}</dd>
            </dl>
          </details>
          <small>
            Browser redirects and model outputs are never payment evidence.
          </small>
        </article>
        <article>
          <span className={styles.realityNumber}>III</span>
          <h3>Booked</h3>
          <p>What did the ledger record?</p>
          <strong>
            {proof
              ? `${proof.facts.length} facts · ${proof.journals.length} posting groups`
              : "No authenticated ledger"}
          </strong>
          {proof?.journals.length ? (
            <ul>
              {proof.journals.map((j) => (
                <li key={j.id}>
                  <b>{j.template.replaceAll("_", " ")}</b>
                  <span>{usd(j.amount_minor)}</span>
                  <details>
                    <summary>Inspect journal lines</summary>
                    {j.lines.map((l, i) => (
                      <div key={i}>
                        {l.side} · {l.account} · {usd(l.amount_minor)}
                      </div>
                    ))}
                    <code>{j.id}</code>
                  </details>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.emptyText}>
              No posting evidence available. A provider status cannot substitute
              for a journal.
            </p>
          )}
        </article>
        <article>
          <span className={styles.realityNumber}>IV</span>
          <h3>Fulfilled</h3>
          <p>What did the seller attest?</p>
          <strong>
            {proof?.seller_fulfilled && !conflict
              ? "Authenticated fulfillment record"
              : "Fulfillment not established"}
          </strong>
          <p className={styles.emptyText}>
            {conflict
              ? "Conflicting evidence blocks fulfillment eligibility."
              : "Seller fulfillment is independent of capture and booking. A paid badge cannot establish an active seat."}
          </p>
          <small>
            {proof?.seller_fulfilled
              ? "Recorded attestation; fresh seller-state comparison requires reconciliation."
              : "No verified fulfillment record to display."}
          </small>
        </article>
      </div>
      {proof && (
        <p>
          <Link
            href={`/intelligence?operation=${encodeURIComponent(proof.operation_id)}`}
          >
            Open Financial Intelligence ↗
          </Link>
        </p>
      )}
      {proof && (
        <details className={styles.proofDetail}>
          <summary>
            Inspect transaction identities & authenticated facts
          </summary>
          <dl>
            <dt>Classification</dt>
            <dd>{proof.classification}</dd>
            <dt>Operation</dt>
            <dd>{proof.operation_id}</dd>
            <dt>Merchant</dt>
            <dd>{proof.merchant_id}</dd>
            <dt>Currency</dt>
            <dd>{proof.currency}</dd>
            {proof.cancellation && (
              <>
                <dt>Local cancellation</dt>
                <dd>{proof.cancellation.cancellation_id}</dd>
                <dt>Linked replacement</dt>
                <dd>
                  {proof.cancellation.replacement_operation_id ??
                    "None admitted"}
                </dd>
              </>
            )}
            <dt>Evidence / decision</dt>
            <dd>
              {proof.evidence_id} / {proof.decision_id}
            </dd>
            <dt>CREATE / CAPTURE committed</dt>
            <dd>
              {String(proof.create_committed)} /{" "}
              {String(proof.capture_committed)}
            </dd>
          </dl>
          {proof.facts.map((f) => (
            <p key={f.id}>
              <b>{f.kind}</b> · {usd(f.amount_minor)} <code>{f.id}</code>
            </p>
          ))}
        </details>
      )}
    </section>
  );
}
