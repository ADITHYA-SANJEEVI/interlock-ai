"use client";
import { answerQuestion, type QuestionId } from "./investigation";
import { observed, type PublicCase } from "./public-evidence";
import styles from "./realities.module.css";

export default function FourRealities({
  record,
  onInspect,
}: {
  record: PublicCase;
  onInspect(id: QuestionId): void;
}) {
  const captured =
    record.provider.capture_confirmed && Boolean(record.provider.capture_id);
  const dimensions = [
    {
      id: "authority" as const,
      name: "Intended",
      state: "Admitted / now expired",
      kind: "limited",
      detail: "Exact action, signed terms and approval binding",
      at: record.read_at,
      clock: "Source read",
    },
    {
      id: "capture" as const,
      name: "Observed",
      state: captured ? "Capture verified" : "Capture unverified",
      kind: captured ? "established" : "unverified",
      detail: captured
        ? "Independent historical PayPal confirmation"
        : "Buyer APPROVED does not establish capture",
      at: record.provider.last_canonical_read_at,
      clock: "PayPal observation",
    },
    {
      id: "accounting" as const,
      name: "Booked",
      state:
        record.economic.journals.length && record.economic.journals_balanced
          ? "Balanced journals recorded"
          : "Booking not established",
      kind: record.economic.journals.length ? "established" : "absent",
      detail: `${record.economic.facts.length} facts / ${record.economic.journals.length} linked journal groups`,
      at: record.read_at,
      clock: "Source read",
    },
    {
      id: "fulfillment" as const,
      name: "Fulfilled",
      state: record.seller.fulfillment_recorded
        ? "Seller fulfillment recorded"
        : "Fulfillment not established",
      kind: record.seller.fulfillment_recorded ? "established" : "absent",
      detail: record.seller.fulfillment_recorded
        ? "Controlled seller evidence, independent of payment"
        : "No fulfillment evidence",
      at: record.read_at,
      clock: "Source read",
    },
  ];
  return (
    <section className={styles.realities} aria-label="Four source realities">
      <div className={styles.title}>
        <div>
          <p>FOUR REALITIES / ONE RECORDED OPERATION</p>
          <h2>Each system establishes a different fact.</h2>
        </div>
        <small>Separate evidence. No global success verdict.</small>
      </div>
      <div className={styles.dimensions}>
        {dimensions.map((d, i) => (
          <button
            key={d.id}
            data-state={d.kind}
            onClick={() => onInspect(d.id)}
            aria-label={`Inspect ${d.name.toLowerCase()} source`}
          >
            <span className={styles.number}>
              0{i + 1} / {d.name}
            </span>
            <strong>{d.state}</strong>
            <span>{d.detail}</span>
            <small>
              {d.clock}: {observed(d.at)}
            </small>
            <b>
              Inspect source <span aria-hidden="true">↗</span>
            </b>
          </button>
        ))}
      </div>
      <p className={styles.caution}>
        USD 1.00 remains held against a separate unresolved dispatch exposure in
        both cases. A completed capture does not resolve that record.
      </p>
    </section>
  );
}

export function RealitySource({
  record,
  question,
  onReturn,
}: {
  record: PublicCase;
  question: QuestionId;
  onReturn(): void;
}) {
  const answer = answerQuestion(record, question);
  return (
    <section className={styles.source} aria-label="Selected reality source">
      <p>HISTORICAL SOURCE / DETERMINISTIC INTERPRETATION</p>
      <p className={styles.caution}>
        {record.provider.capture_confirmed
          ? "Completed recorded purchase"
          : "Recorded buyer consent / capture unverified"}{" "}
        · USD 1.00 · Separate dispatch exposure remains unresolved; capacity is
        retained.
      </p>
      <h2>{answer.title}</h2>
      <p>{answer.detail}</p>
      <button onClick={onReturn}>Return to investigation</button>
      {answer.sources.map((s) => (
        <div key={s.path}>
          <h3>{s.label}</h3>
          <p>
            {s.clock}: {observed(s.at)}
          </p>
          <code>{s.path}</code>
          <details>
            <summary>Original source fields and identifiers</summary>
            <pre>
              {JSON.stringify(s.value, null, 2) ??
                "Unavailable in scoped source"}
            </pre>
          </details>
        </div>
      ))}
      <p>
        Package read time is not an inferred event time. Original source hashes
        and provenance remain available in the case exports.
      </p>
    </section>
  );
}
