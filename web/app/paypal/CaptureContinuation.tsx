"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, Command, Snapshot } from "./contracts";
import { Continuation, readContinuation } from "./continuation-contract";
import Frame from "./Frame";
import styles from "./paypal.module.css";

const ist = (value: string) =>
  new Date(value).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  });
export default function CaptureContinuation({ id }: { id: string }) {
  const [data, setData] = useState<ReturnType<typeof readContinuation>>();
  const [scope, setScope] = useState<Snapshot>();
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(0);
  const sending = useRef(false);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  async function load() {
    const [value, authority] = await Promise.all([
      api<Continuation>(`paypal/continuations/${id}`),
      api<Snapshot>("paypal/authority"),
    ]);
    const parsed = readContinuation(value, id);
    const digest = Array.from(
      new Uint8Array(
        await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(value.attestation_raw),
        ),
      ),
      (x) => x.toString(16).padStart(2, "0"),
    ).join("");
    if (
      digest !== value.digest ||
      authority.currency !== "USD" ||
      authority.tenant_id !== value.tenant_id ||
      !Array.isArray(authority.roles)
    )
      throw new Error("Continuation digest or authenticated scope mismatch.");
    setData(parsed);
    setScope(authority);
    setNow(Date.now());
    return parsed;
  }

  async function refresh() {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError("");
    setReviewed(false);
    try {
      await load();
    } catch (failure) {
      setData(undefined);
      setError(
        failure instanceof Error
          ? failure.message
          : "Canonical evidence unavailable.",
      );
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const c = data?.continuation;
  const expired = Boolean(c && (c.expired || Date.parse(c.expires_at) <= now));
  const canApprove = Boolean(
    c &&
    scope?.roles.includes("PURCHASE_APPROVER") &&
    scope.principal !== c.requester &&
    !c.approval &&
    !c.revoked &&
    !expired,
  );

  async function approve() {
    if (sending.current || !c || !scope || !canApprove || !reviewed) return;
    sending.current = true;
    setBusy(true);
    setError("");
    try {
      // Persist BEFORE sending. A timeout/refresh must retain the exact same
      // principal, digest, body and command identity; nothing retries itself.
      const storage = `interlock:continuation:${scope.tenant_id}:${scope.principal}:${id}`;
      const body = {
        operation_id: c.operation_id,
        currency: "USD",
        continuation_digest: c.digest,
        expires_at: c.expires_at,
      };
      const stored = window.localStorage.getItem(storage);
      const command: Command = stored
        ? JSON.parse(stored)
        : {
            key: crypto.randomUUID(),
            path: `paypal/continuations/${id}/approvals`,
            body,
            revision: 1,
          };
      if (
        command.path !== `paypal/continuations/${id}/approvals` ||
        command.revision !== 1 ||
        !command.key ||
        JSON.stringify(command.body) !== JSON.stringify(body)
      )
        throw new Error(
          "Stored approval differs. Preserve it and inspect canonical evidence.",
        );
      window.localStorage.setItem(storage, JSON.stringify(command));
      const result = await api<{ approval_id: string }>(
        command.path,
        command.body,
        command,
      );
      const canonical = await load();
      if (
        canonical.continuation.approval?.id !== result.approval_id ||
        canonical.continuation.approval.approver !== scope.principal
      )
        throw new Error(
          "Approval outcome needs canonical review. Do not submit another command.",
        );
      window.localStorage.removeItem(storage);
      setReviewed(false);
    } catch (failure) {
      setError(
        `${failure instanceof Error ? failure.message : "Approval outcome unavailable."} Refresh canonical evidence before any retry. The original command identity is retained.`,
      );
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Frame>
      <main className={styles.workspace}>
        <div className={styles.workspaceTitle}>
          <div>
            <p className={styles.eyebrow}>INDEPENDENT REVIEW</p>
            <h1>Continue the same purchase.</h1>
            <p>Review fresh capture authority for an existing sandbox order.</p>
          </div>
        </div>
        <section className={styles.panel} aria-busy={busy}>
          <h2>Exact continuation approval</h2>
          <p>
            Sign in personally in the <Link href="/workspace">workspace</Link>,
            then load this record. This page records independent approval only.
            Issuer capture authorization is a separate step.
          </p>
          <p>
            <strong>Continuation:</strong> <code>{id}</code>
          </p>
          <button onClick={refresh} disabled={busy}>
            Refresh canonical continuation
          </button>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          {c && data && (
            <>
              <p>
                <strong>Signed-in principal:</strong> {scope?.principal}
              </p>
              <dl className={styles.proofDetail}>
                <dt>Product / beneficiary</dt>
                <dd>One workspace-seat for alice</dd>
                <dt>Amount / merchant</dt>
                <dd>USD 1.00 / {c.merchant_id}</dd>
                <dt>Existing operation</dt>
                <dd>
                  <code>{c.operation_id}</code>
                </dd>
                <dt>Existing PayPal order</dt>
                <dd>
                  <code>{c.provider_order_id}</code>
                </dd>
                <dt>Held reservation</dt>
                <dd>
                  <code>{c.reservation_id}</code>
                </dd>
                <dt>Persisted capture identity</dt>
                <dd>
                  <code>{c.capture_request_id}</code>
                </dd>
                <dt>Original allocation</dt>
                <dd>
                  <code>{data.attestation.quote.lines[0].allocation_id}</code>
                </dd>
                <dt>Original quote expiry, preserved</dt>
                <dd>{ist(data.attestation.quote.expires_at)}</dd>
                <dt>Fresh seller attestation expires</dt>
                <dd>{ist(c.expires_at)}</dd>
                <dt>Deterministic policy</dt>
                <dd>Allowed at preparation; checked again before capture.</dd>
                <dt>Exact continuation digest</dt>
                <dd>
                  <code>{c.digest}</code>
                </dd>
              </dl>
              <p>
                This is new authorization evidence. It does not change the
                expired quote, create a new reservation or prove capture,
                settlement or fulfillment.
              </p>
              {c.approval ? (
                <div role="status" className={styles.success}>
                  <strong>Canonical approval recorded</strong>
                  <p>
                    <code>{c.approval.id}</code> by {c.approval.approver}
                  </p>
                  <p>
                    Recorded {ist(c.approval.created_at)}. Approval expires{" "}
                    {ist(c.approval.expires_at)}.
                  </p>
                </div>
              ) : (
                <p role="status">
                  No independent continuation approval is recorded.
                </p>
              )}
              {(expired || c.revoked) && (
                <p role="alert">
                  {c.revoked
                    ? "Continuation revoked."
                    : "Continuation expired."}{" "}
                  No approval can be submitted.
                </p>
              )}
              {c.approval && Date.parse(c.approval.expires_at) <= now && (
                <p role="alert">
                  Recorded approval has expired. It cannot authorize capture.
                </p>
              )}
              <label>
                <input
                  type="checkbox"
                  checked={reviewed}
                  disabled={!canApprove || busy}
                  onChange={(event) => setReviewed(event.target.checked)}
                />{" "}
                I reviewed the exact order, original allocation, USD terms and
                new deadline.
              </label>
              <button
                onClick={approve}
                disabled={!canApprove || !reviewed || busy}
              >
                Approve exact continuation once
              </button>
              <details>
                <summary>Source signed attestation</summary>
                <pre
                  style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {JSON.stringify(data.attestation, null, 2)}
                </pre>
              </details>
            </>
          )}
        </section>
      </main>
    </Frame>
  );
}
