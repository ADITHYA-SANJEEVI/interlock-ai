"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Snapshot, usd } from "../paypal/contracts";
import {
  commerce,
  displayTime,
  PlanningResult,
  readPlanning,
  words,
} from "./contracts";
import styles from "./commerce.module.css";

export default function PurchasePlanner({
  scope,
  onHandoff,
}: {
  scope?: Snapshot;
  onHandoff(plan: PlanningResult): void;
}) {
  const [objective, setObjective] = useState("");
  const [beneficiary, setBeneficiary] = useState("alice");
  const [maximum, setMaximum] = useState("1.00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<PlanningResult>();
  const [copied, setCopied] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const running = useRef(false);
  useEffect(() => () => controller.current?.abort(), []);
  const permitted = scope?.roles.includes("PURCHASE_REQUESTER");
  function edited() {
    setPlan(undefined);
    setCopied(false);
    setError("");
  }
  return (
    <section className={styles.planner} aria-label="Purchase planning agent">
      <p className={styles.eyebrow}>
        CURRENT PLANNING / NO FINANCIAL AUTHORITY
      </p>
      <h3>Plan a controlled purchase</h3>
      <p>
        Let the agent inspect seller terms and spending rules before you prepare
        a protected request.
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (running.current || !permitted || !scope) return;
          if (!/^\d{1,7}(\.\d{1,2})?$/.test(maximum)) {
            setError("Enter an exact USD maximum with at most two decimals.");
            return;
          }
          const [whole, fraction = ""] = maximum.split(".");
          const limit = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
          if (limit < 1 || limit > 1_000_000_000) {
            setError("USD maximum is outside the supported bounds.");
            return;
          }
          const input = {
            objective: objective.trim(),
            beneficiary: beneficiary.trim(),
            spending_limit_minor: limit,
          };
          running.current = true;
          setBusy(true);
          setError("");
          setPlan(undefined);
          setCopied(false);
          controller.current = new AbortController();
          try {
            const result = await commerce<PlanningResult>(
              "plans",
              input,
              AbortSignal.any([
                controller.current.signal,
                AbortSignal.timeout(105000),
              ]),
            );
            setPlan(
              readPlanning(result, input, scope.principal, scope.tenant_id),
            );
          } catch (failure) {
            if (!controller.current.signal.aborted)
              setError(
                failure instanceof Error
                  ? failure.message
                  : "Planning unavailable. No financial action was sent.",
              );
          } finally {
            running.current = false;
            setBusy(false);
          }
        }}
      >
        <label htmlFor="planning-objective">Purchasing objective</label>
        <textarea
          id="planning-objective"
          value={objective}
          maxLength={1000}
          minLength={8}
          required
          rows={2}
          placeholder="Obtain one workspace seat for alice. Inspect the seller terms, budget and required approvals."
          disabled={!permitted || busy}
          onChange={(e) => {
            setObjective(e.target.value);
            edited();
          }}
        />
        <div className={styles.fields}>
          <label>
            Requested beneficiary
            <input
              value={beneficiary}
              pattern="[a-z][a-z0-9_-]{2,40}"
              required
              disabled={!permitted || busy}
              onChange={(e) => {
                setBeneficiary(e.target.value);
                edited();
              }}
            />
          </label>
          <label>
            Maximum USD
            <input
              value={maximum}
              inputMode="decimal"
              required
              disabled={!permitted || busy}
              onChange={(e) => {
                setMaximum(e.target.value);
                edited();
              }}
            />
          </label>
        </div>
        <button
          type="submit"
          disabled={!permitted || busy || objective.trim().length < 8}
        >
          {busy
            ? "Reading commercial and authority sources…"
            : "Plan purchase with tools"}
        </button>
      </form>
      {!permitted && (
        <p className={styles.muted}>
          Sign in as a purchase requester to plan. Planning does not require a
          usable grant; proceeding does.
        </p>
      )}
      {busy && (
        <p role="status">
          The model is choosing scoped tools. No quote, action, reservation or
          payment is being created.
        </p>
      )}
      {error && (
        <p role="alert" className={styles.warning}>
          {error}
        </p>
      )}
      {plan && (
        <div
          className={styles.planResult}
          aria-label="Source-backed purchase plan"
        >
          <div className={styles.resultHeading}>
            <strong>
              {plan.policy_preview.status === "BLOCKED"
                ? "Plan prepared · execution blocked"
                : "Plan prepared · human gates remain"}
            </strong>
            <span>
              {plan.tool_execution_count} tools /{" "}
              {(plan.latency_ms / 1000).toFixed(1)}s
            </span>
          </div>
          <ol
            className={styles.toolPath}
            aria-label="Completed model-selected planning activity"
          >
            {plan.trace.map((t) => (
              <li key={t.call_id}>
                <strong>{words(t.tool)}</strong>
                <span>
                  {t.status} / {t.source_id}
                </span>
              </li>
            ))}
          </ol>
          <p>
            These are the tools selected in this planning run. The deterministic
            preview below is separate from formal SQL admission and independent
            human approval. This run did not create an earlier recorded payment.
          </p>
          <dl>
            <dt>Product / quantity</dt>
            <dd>
              {plan.proposal.product_id} / {plan.proposal.quantity}
            </dd>
            <dt>Beneficiary</dt>
            <dd>{plan.proposal.beneficiary}</dd>
            <dt>Recorded seller price</dt>
            <dd>{usd(plan.proposal.amount_minor)}</dd>
            <dt>Your maximum</dt>
            <dd>{usd(plan.proposal.spending_limit_minor)}</dd>
            <dt>Seller / merchant</dt>
            <dd>
              {plan.proposal.seller} / {plan.proposal.merchant_id}
            </dd>
            <dt>Historical quote expiry</dt>
            <dd>{displayTime(plan.proposal.source_expires_at)}</dd>
          </dl>
          <p className={styles.warning}>
            Historical authenticated terms. Live availability is unverified. A
            fresh signed quote and independent approval are required; this plan
            cannot authorize spending.
          </p>
          <h4>Deterministic preparation preview</h4>
          <p>
            {plan.policy_preview.policy_amount_permitted === true
              ? "Amount is within the recorded current policy limit."
              : "Policy amount permission is not established."}
          </p>
          {plan.policy_preview.blockers.length > 0 && (
            <ul aria-label="Planning blockers">
              {plan.policy_preview.blockers.map((b) => (
                <li key={b}>{words(b)}</li>
              ))}
            </ul>
          )}
          <p>
            Next human action:{" "}
            <strong>{words(plan.policy_preview.next_action)}</strong>.
          </p>
          <details>
            <summary>Required protected workflow</summary>
            <ol>
              {plan.policy_preview.requirements.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ol>
          </details>
          <details>
            <summary>AI interpretation / {plan.model}</summary>
            <p>{plan.summary}</p>
          </details>
          <details>
            <summary>Planning tool trace ({plan.trace.length})</summary>
            {plan.trace.map((t) => (
              <details key={t.call_id}>
                <summary>
                  {words(t.tool)} · {t.status} · {t.source_id}
                </summary>
                <pre>
                  {JSON.stringify(
                    {
                      arguments: t.arguments,
                      response: t.response,
                      source_sha256: t.source_sha256,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            ))}
          </details>
          <button
            type="button"
            disabled={copied}
            onClick={() => {
              onHandoff(plan);
              setCopied(true);
            }}
          >
            {copied
              ? "Draft copied · no financial request sent"
              : plan.policy_preview.status === "BLOCKED"
                ? "Copy reviewed draft (execution blocked)"
                : "Copy reviewed draft to protected request"}
          </button>
          {copied && (
            <p role="status">
              The exact draft is in the protected request form below. No
              proposal record, quote, action or reservation was created. Current
              authority and deliberate user operations are still required.
            </p>
          )}
          <Link href="/operations">
            Inspect recorded purchases — separate from this draft ↗
          </Link>
        </div>
      )}
    </section>
  );
}
