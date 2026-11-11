"use client";
import { answerQuestion, questions, type QuestionId } from "./investigation";
import {
  money,
  observed,
  type PublicCase,
  type PublicEvidence,
} from "./public-evidence";
import styles from "./workbench.module.css";

export default function InvestigationWorkbench({
  record,
  guided,
  onStart,
  evidence,
  question,
  onQuestion,
  onNavigate,
}: {
  record: PublicCase;
  guided: boolean;
  onStart(): void;
  evidence: PublicEvidence;
  question: QuestionId;
  onQuestion(id: QuestionId): void;
  onNavigate(view: string, fact?: string, tool?: string): void;
}) {
  const answer = answerQuestion(record, question);
  const hasTrace = evidence.investigation.operation_id === record.operation_id;
  return (
    <section className={styles.workbench} aria-label="Guided investigation">
      <div className={styles.signature}>
        <div>
          <p>FOLLOW THE INCOMPLETE CASE</p>
          <h2>Why wasn't this purchase completed?</h2>
          <p>
            Follow buyer consent through the evidence gaps and the obligation
            that remains.
          </p>
        </div>
        <button onClick={onStart}>
          {guided
            ? "Restart guided investigation"
            : "Start guided investigation"}
        </button>
      </div>
      {guided && (
        <nav className={styles.journey} aria-label="Guided evidence trail">
          {(
            [
              ["consent", "Buyer consent"],
              ["capture", "Capture evidence"],
              ["accounting", "Accounting"],
              ["fulfillment", "Fulfillment"],
              ["reservation", "Retained capacity"],
              ["unresolved", "Open obligation"],
            ] as const
          ).map(([id, label], i) => (
            <button
              key={id}
              aria-current={question === id ? "step" : undefined}
              onClick={() => onQuestion(id)}
            >
              <small>0{i + 1}</small>
              {label}
            </button>
          ))}
          <button
            onClick={() =>
              onNavigate("Agent traces", undefined, "reconcile_records")
            }
          >
            <small>07</small>Recorded tools
          </button>
          <button onClick={() => onNavigate("Reports")}>
            <small>08</small>Related reports
          </button>
        </nav>
      )}
      <div className={styles.heading}>
        <div>
          <p>INVESTIGATION WORKBENCH</p>
          <h2>Ask a question. Inspect its sources.</h2>
        </div>
        <span>
          Deterministic source interpretation
          <br />
          No new AI or provider call
        </span>
      </div>
      <div className={styles.questions} aria-label="Financial questions">
        {questions.map(([id, label]) => (
          <button
            key={id}
            aria-pressed={id === question}
            onClick={() => onQuestion(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <article
        className={styles.answer}
        data-answer-status={answer.status}
        aria-labelledby="investigation-answer"
        aria-live="polite"
        aria-atomic="true"
      >
        <p className={styles.classification}>
          HISTORICAL EVIDENCE /{" "}
          {answer.status === "attention"
            ? "REVIEW REQUIRED"
            : answer.status === "limited"
              ? "AUTHORITY / VISIBILITY LIMIT"
              : "RECORDED OUTCOME"}
        </p>
        <h3 id="investigation-answer">{answer.title}</h3>
        <p>{answer.detail}</p>
      </article>
      <div className={styles.actions}>
        <button onClick={() => onNavigate(answer.target, answer.factId)}>
          Inspect related {answer.target.toLowerCase()}
        </button>
        <button
          disabled={!hasTrace}
          aria-describedby="trace-availability"
          onClick={() => onNavigate("Agent traces", undefined, answer.tool)}
        >
          Inspect recorded investigator tools
        </button>
        <small id="trace-availability">
          {hasTrace
            ? "The seven-tool recording concerns this incomplete case."
            : "The recorded investigator concerns the incomplete case; no trace is attributed to this completed purchase."}
        </small>
      </div>
      <details className={styles.citations}>
        <summary>
          Inspect supporting sources ({answer.sources.length}) and observation
          times
        </summary>
        <p>
          Paths refer to this case in the verified versioned bundle. Package
          read time is not an inferred event time. Hashes identify content, not
          external certification.
        </p>
        {answer.sources.map((source) => (
          <div key={source.path} className={styles.citation}>
            <strong>{source.label}</strong>
            <small>
              {source.clock}: {observed(source.at)}
            </small>
            <code>{source.path}</code>
            <pre>
              {source.value === undefined
                ? "Unavailable in this scoped source"
                : JSON.stringify(source.value, null, 2)}
            </pre>
          </div>
        ))}
      </details>
      <details className={styles.comparison}>
        <summary>Compare the two recorded outcomes</summary>
        <div className={styles.table}>
          <table>
            <caption>
              Separate genuine operations / USD 1.00 each / no combined spending
              total
            </caption>
            <thead>
              <tr>
                <th>Independent record</th>
                {evidence.cases.map((c) => (
                  <th key={c.operation_id}>
                    {c.provider.capture_confirmed
                      ? "Completed purchase"
                      : "Capture unverified"}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[
                [
                  "PayPal observation",
                  ...evidence.cases.map(
                    (c) =>
                      `${c.provider.provider_order_status} / ${observed(c.provider.last_canonical_read_at)}`,
                  ),
                ],
                [
                  "Verified capture",
                  ...evidence.cases.map(
                    (c) => c.provider.capture_id ?? "Not established",
                  ),
                ],
                [
                  "Accounting",
                  ...evidence.cases.map(
                    (c) => `${c.economic.journals.length} journal groups`,
                  ),
                ],
                [
                  "Fulfillment",
                  ...evidence.cases.map((c) =>
                    c.seller.fulfillment_recorded
                      ? "Recorded"
                      : "Not established",
                  ),
                ],
                [
                  "Retained capacity",
                  ...evidence.cases.map((c) =>
                    money(c.authority.lifetime_budget.reserved),
                  ),
                ],
                [
                  "Separate exposure",
                  ...evidence.cases.map(
                    (c) => c.authority.reservation.dispatch_exposure_state,
                  ),
                ],
              ].map(([label, ...values]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  {values.map((v, i) => (
                    <td key={i}>{v}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          The later recorded purchase-planning run did not create either
          payment. Current provider state is not established.
        </p>
      </details>
    </section>
  );
}
