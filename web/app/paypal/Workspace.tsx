"use client";
import OperationContext from "../product/OperationContext";
import {
  FormEvent,
  useCallback,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import Frame from "./Frame";
import Link from "next/link";
import PurchasePlanner from "../commerce/PurchasePlanner";
import Realities from "./Realities";
import {
  Action,
  ApiError,
  Command,
  Proposal,
  Proof,
  Snapshot,
  api,
  identifier,
  readProof,
  usd,
} from "./contracts";
import styles from "./paypal.module.css";

const subscribe = (cb: () => void) => {
  window.addEventListener("storage", cb);
  window.addEventListener("paypal-command", cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener("paypal-command", cb);
  };
};
const message = (e: unknown) =>
  e instanceof Error
    ? e.message
    : "Service unavailable. Preserve the original request.";
type Props = {
  returnToken?: string;
  cancelled?: boolean;
  initialOperation?: string;
};
type Quote = {
  quote_id: string;
  currency: "USD";
  quote: {
    amount: number;
    currency: string;
    expires_at: string;
    merchant?: string;
    lines: { beneficiary: string; unit_price: number }[];
  };
};
export default function Workspace(props: Props) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false },
          mutations: { retry: false },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <PurchaseWorkspace {...props} />
    </QueryClientProvider>
  );
}
function PurchaseWorkspace({
  returnToken,
  cancelled,
  initialOperation = "",
}: Props) {
  const qc = useQueryClient();
  const sending = useRef(false);
  const [approvalAck, setApprovalAck] = useState<{
    actionId: string;
    evidenceId: string;
    decisionId: string;
    id: string;
  }>();
  const [approvalError, setApprovalError] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [receipt, setReceipt] = useState("");
  const [intent, setIntent] = useState(""),
    [grantId, setGrantId] = useState("");
  const [proposal, setProposal] = useState<Proposal>(),
    [quote, setQuote] = useState<Quote>();
  const [recordedProposalId, setRecordedProposalId] = useState("");
  const [actionId, setActionId] = useState(""),
    [approvalId, setApprovalId] = useState("");
  const [reviewed, setReviewed] = useState(false),
    [operationId, setOperationId] = useState("");
  const [operationInput, setOperationInput] = useState(initialOperation),
    [approvalUrl, setApprovalUrl] = useState("");
  const [reconciliation, setReconciliation] =
    useState<Record<string, unknown>>();
  const status = useQuery({
    queryKey: ["paypal-status"],
    queryFn: () =>
      api<{ configured: boolean; payment_actions_enabled: boolean }>(
        "paypal/status",
      ),
  });
  const me = useQuery({
    queryKey: ["paypal-me"],
    queryFn: () =>
      api<{ username: string; tenant_id: string; role: string }>("me"),
  });
  const view = useQuery({
    queryKey: ["paypal-authority", me.data?.username],
    enabled: Boolean(me.data),
    queryFn: async () => {
      const s = await api<Snapshot>("paypal/authority");
      if (
        s.currency !== "USD" ||
        s.contract_version !== "paypal-v1" ||
        !Array.isArray(s.grants) ||
        !Array.isArray(s.actions) ||
        !Array.isArray(s.roles)
      )
        throw new Error(
          "Authority contract unavailable. USD scope could not be verified.",
        );
      return s;
    },
  });
  const s = view.data;
  const detail = useQuery({
    queryKey: ["paypal-action", me.data?.username, actionId],
    enabled: Boolean(s && identifier(actionId)),
    queryFn: async () => {
      const a = await api<Action>(`paypal/actions/${actionId}`);
      if (a.id !== actionId || a.currency !== "USD")
        throw new Error("Exact action identity or currency mismatch.");
      return a;
    },
  });
  const evidence = useQuery({
    queryKey: ["paypal-proof", me.data?.username, operationId],
    enabled: Boolean(s && identifier(operationId)),
    queryFn: async () => {
      const p = readProof(await api<Proof>(`paypal/evidence/${operationId}`));
      if (p.operation_id !== operationId)
        throw new Error("Operation identity mismatch.");
      return p;
    },
  });
  const p = evidence.data,
    a = detail.data;
  const readUnavailable = Boolean(
    p?.provider_order_id && !p.last_canonical_read_at,
  );
  const scope = s ? `${s.tenant_id}:${s.principal}` : "";
  const pendingKey = scope ? `interlock:paypal:pending:${scope}` : "";
  const readPending = useCallback(
    () => (pendingKey ? window.localStorage.getItem(pendingKey) : null),
    [pendingKey],
  );
  const pendingRaw = useSyncExternalStore(subscribe, readPending, () => null);
  let pending: Command | null = null;
  try {
    if (pendingRaw) {
      pending = JSON.parse(pendingRaw);
      if (!pending?.key || !pending.path || !pending.body) pending = null;
    }
  } catch {
    /* Invalid local recovery data stays locked below. */
  }
  const locked = busy || Boolean(pendingRaw) || !s || view.isError;
  const paymentEnabled = status.data?.payment_actions_enabled === true;
  const mismatch = Boolean(
    returnToken && p && p.provider_order_id !== returnToken,
  );
  const grants =
    s?.grants.filter(
      (g) =>
        g.currency === "USD" &&
        g.subject === s.principal &&
        g.effective_status === "ACTIVE",
    ) ?? [];
  const grant = grants.find((g) => g.id === grantId);
  const exactApprovals =
    s?.approvals.filter(
      (ap) =>
        a &&
        ap.action_id === a.id &&
        ap.evidence_id === a.evidence.id &&
        ap.decision_id === a.decision.id,
    ) ?? [];
  const approvals = exactApprovals.filter(
    (ap) => ap.effective_status === "AVAILABLE",
  );
  const approver = Boolean(
    s?.roles.includes("PURCHASE_APPROVER") && a && a.requester !== s.principal,
  );
  const ownApproval = approvals.some((ap) => ap.approver === s?.principal);
  const exactApprovalAck = Boolean(
    a &&
    approvalAck?.actionId === a.id &&
    approvalAck.evidenceId === a.evidence.id &&
    approvalAck.decisionId === a.decision.id,
  );
  const requester = Boolean(
    s?.roles.includes("PURCHASE_REQUESTER") && a?.requester === s.principal,
  );
  const buyerOwner = s?.roles.includes("PURCHASE_ISSUER") === true;
  function persist(command: Command | null) {
    if (!pendingKey) throw new Error("Authenticated USD scope required.");
    if (command)
      window.localStorage.setItem(pendingKey, JSON.stringify(command));
    else window.localStorage.removeItem(pendingKey);
    window.dispatchEvent(new Event("paypal-command"));
  }
  async function refresh() {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["paypal-authority"] }),
      qc.invalidateQueries({ queryKey: ["paypal-action"] }),
      qc.invalidateQueries({ queryKey: ["paypal-proof"] }),
    ]);
  }
  async function send(command: Command) {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError("");
    setReceipt("");
    if (command.path === "commands/approve") setApprovalError("");
    try {
      // Stored before network I/O. A timeout keeps this exact body, key and revision.
      persist(command);
      const result = await api<Record<string, unknown>>(
        `paypal/${command.path}`,
        command.body,
        command,
      );
      if (command.path === "proposals") {
        setProposal(result as unknown as Proposal);
        setQuote(undefined);
      }
      if (command.path === "quotes") {
        if (result.currency !== "USD")
          throw new Error("Signed quote currency mismatch");
        setQuote(result as unknown as Quote);
      }
      if (
        command.path === "commands/propose" &&
        typeof result.action_id === "string"
      ) {
        setActionId(result.action_id);
        setReviewed(false);
      }
      if (
        command.path === "commands/admit" &&
        typeof result.operation_id === "string"
      ) {
        setOperationId(result.operation_id);
        setOperationInput(result.operation_id);
        window.localStorage.setItem(
          `interlock:paypal:operation:${s!.tenant_id}`,
          result.operation_id,
        );
      }
      if (command.path === "consent") {
        const url = new URL(String(result.approval_url));
        if (
          url.origin !== "https://www.sandbox.paypal.com" ||
          url.pathname !== "/checkoutnow" ||
          url.searchParams.get("token") !== p?.provider_order_id ||
          url.username ||
          url.password
        )
          throw new Error("Consent URL binding mismatch. Do not navigate.");
        setApprovalUrl(url.href);
      }
      if (command.path === "reconciliation/runs") setReconciliation(result);
      if (
        command.path === "commands/approve" &&
        typeof result.approval_id === "string"
      ) {
        setApprovalAck({
          actionId: String(command.body.action_id),
          evidenceId: String(command.body.evidence_id),
          decisionId: String(command.body.decision_id),
          id: result.approval_id,
        });
        setReviewed(false);
      }
      persist(null);
      setReceipt(
        `Server acknowledged ${command.path}. Financial status comes from refreshed evidence below.`,
      );
      await refresh();
    } catch (e) {
      // Rejections are explicit; transient/server failures retain the pending identity.
      if (
        e instanceof ApiError &&
        e.status >= 400 &&
        e.status < 500 &&
        e.status !== 408 &&
        e.status !== 429 &&
        !["create", "consent", "capture-requests"].includes(command.path)
      )
        persist(null);
      setError(message(e));
      if (command.path === "commands/approve") setApprovalError(message(e));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  function command(
    path: string,
    body: Record<string, unknown>,
    revision?: number,
  ) {
    if (locked) return;
    void send({
      path,
      body,
      revision,
      key: `web_${crypto.randomUUID().replaceAll("-", "")}`,
    });
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError("");
    try {
      await api("session", {
        username: data.get("username"),
        password: data.get("password"),
      });
      form.reset();
      await qc.invalidateQueries({ queryKey: ["paypal-me"] });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    setError("");
    try {
      await api("logout", {});
      qc.clear();
      setProposal(undefined);
      setQuote(undefined);
      setIntent("");
      setGrantId("");
      setApprovalId("");
      setActionId("");
      setOperationId("");
      setOperationInput("");
      setApprovalUrl("");
      setReviewed(false);
      setReceipt("");
      setApprovalAck(undefined);
      setApprovalError("");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  function openOperation(event: FormEvent) {
    event.preventDefault();
    const id =
      operationInput.trim() ||
      (s &&
        window.localStorage.getItem(
          `interlock:paypal:operation:${s.tenant_id}`,
        )) ||
      "";
    if (!identifier(id)) {
      setError("Enter an existing operation ID. No new order will be created.");
      return;
    }
    setOperationId(id);
    setOperationInput(id);
    setApprovalUrl("");
    setError("");
    void qc.invalidateQueries({ queryKey: ["paypal-proof"] });
  }
  const suggestion = proposal?.result.extraction.proposal;
  async function loadRecordedProposal() {
    if (locked || !/^[a-fA-F0-9-]{36}$/.test(recordedProposalId)) return;
    setBusy(true);
    setError("");
    try {
      const value = await api<Proposal>(
        `paypal/proposals/${recordedProposalId}`,
      );
      if (
        value.proposal_id !== recordedProposalId ||
        value.result.financial_authority !== false
      )
        throw new Error("Recorded proposal identity or authority mismatch.");
      setProposal(value);
      setGrantId(value.grant_id ?? "");
      setQuote(undefined);
      setReceipt(
        "Loaded the recorded proposal. No new model request was made.",
      );
    } catch (failure) {
      setProposal(undefined);
      setQuote(undefined);
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }
  const signedQuote =
    a?.terms.quote && typeof a.terms.quote === "object"
      ? (a.terms.quote as Record<string, unknown>)
      : undefined;
  const signedLines = Array.isArray(signedQuote?.lines)
    ? signedQuote.lines.filter(
        (line): line is Record<string, unknown> =>
          Boolean(line) && typeof line === "object",
      )
    : [];
  return (
    <Frame>
      <OperationContext operation={p?.operation_id} />
      <main
        className={styles.workspace}
        data-role={approver && !requester ? "approver" : "requester"}
      >
        <div className={styles.workspaceTitle}>
          <div>
            <p className={styles.eyebrow}>PURCHASE WORKSPACE</p>
            <h1>Requests &amp; approvals</h1>
          </div>
          <div className={styles.modeLabel}>
            USD <small>Controlled seller / US PayPal Sandbox</small>
          </div>
        </div>
        <div className={styles.gate} role="status">
          <span className={styles.lockIcon}>⊡</span>
          <div>
            <strong>
              {paymentEnabled
                ? "Payment controls enabled by operator"
                : "Financial actions locked"}
            </strong>
            <p>
              {paymentEnabled
                ? "Backend policy, independent approval and current-authority checks still apply."
                : "Sandbox payment actions are disabled in this preview. You can inspect recorded evidence; new payments require operator authorization."}
            </p>
          </div>
          <span className={styles.badge}>SANDBOX ONLY</span>
        </div>
        {(returnToken || cancelled) && (
          <div className={styles.returnNotice} role="status">
            <strong>
              {cancelled
                ? "You returned from checkout cancellation."
                : "You returned from PayPal."}
            </strong>
            <p>
              This browser event proves neither approval nor settlement.{" "}
              {cancelled
                ? "Cancellation does not release a reservation or prove no payment occurred."
                : "Load the existing operation, match its order, then request a canonical observation."}{" "}
              No capture runs automatically.
            </p>
            {returnToken && <code>Returned order: {returnToken}</code>}
          </div>
        )}
        <div className={styles.sessionBar}>
          {me.data ? (
            <>
              <div>
                <span className={styles.dot} />
                <strong>{me.data.username}</strong>
                <span>{me.data.tenant_id}</span>
                <span>
                  {s?.roles
                    .map(
                      (role) =>
                        (
                          ({
                            PURCHASE_REQUESTER: "Purchase requester",
                            PURCHASE_APPROVER: "Independent approver",
                            PURCHASE_ISSUER: "Issuer",
                          }) as Record<string, string>
                        )[role] ?? role,
                    )
                    .join(" · ") || "Authority scope unavailable"}
                </span>
              </div>
              <button
                className={styles.textButton}
                disabled={busy}
                onClick={logout}
              >
                Sign out / change account
              </button>
            </>
          ) : (
            <form onSubmit={login} className={styles.login}>
              <div>
                <strong>Sign in to your workspace</strong>
                <small>Independent roles use separate accounts.</small>
              </div>
              <label>
                Username
                <input
                  name="username"
                  autoComplete="username"
                  required
                  maxLength={80}
                />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              <button disabled={busy} className={styles.secondary}>
                Sign in
              </button>
            </form>
          )}
        </div>
        {status.data?.configured === false && (
          <div className={styles.warning} role="status">
            <strong>Protected API unavailable</strong>
            <p>
              No protected service endpoint is configured. No financial request
              can be sent.
            </p>
          </div>
        )}
        {me.isError &&
          me.error instanceof ApiError &&
          me.error.status >= 500 && (
            <div className={styles.warning} role="alert">
              <strong>Session service unavailable</strong>
              <p>
                Your identity could not be verified. No session or financial
                authority has been assumed.
              </p>
            </div>
          )}
        {view.isError && (
          <div className={styles.warning} role="alert">
            <strong>Protected authority unavailable</strong>
            <p>
              {message(view.error)} No grant, balance, decision or transaction
              has been invented.
            </p>
            <button
              className={styles.textButton}
              onClick={() => void view.refetch()}
            >
              Refresh authority
            </button>
          </div>
        )}
        {error && (
          <div className={styles.error} role="alert">
            {error}
          </div>
        )}
        {receipt && (
          <div className={styles.receipt} role="status">
            {receipt}
          </div>
        )}
        {pendingRaw && (
          <div className={styles.warning} role="alert">
            <strong>Original command retained · outcome unresolved</strong>
            <p>
              New commands are paused. Recovery uses the stored body, revision
              and identity. Never repeat checkout or create a replacement
              payment.
            </p>
            {pending ? (
              <>
                <code>
                  {pending.path} · {pending.key}
                </code>
                <button
                  className={styles.secondary}
                  disabled={busy}
                  onClick={() => void send(pending!)}
                >
                  Recover original command
                </button>
              </>
            ) : (
              <p>
                Local recovery data is invalid. Investigate before issuing
                another command.
              </p>
            )}
          </div>
        )}
        <section
          className={styles.operationContext}
          aria-label="Current operation"
        >
          <div className={styles.contextHeading}>
            <span>RECORDED OPERATION</span>
            {p && (
              <Link
                href={`/intelligence?operation=${encodeURIComponent(p.operation_id)}`}
              >
                Investigate this purchase ↗
              </Link>
            )}
          </div>
          <form className={styles.operationForm} onSubmit={openOperation}>
            <label>
              Existing protected operation
              <input
                value={operationInput}
                onChange={(e) => setOperationInput(e.target.value)}
                placeholder="Enter an operation ID, or leave blank to recover this tenant’s last admission"
                maxLength={100}
                disabled={!s || busy}
              />
            </label>
            <button className={styles.secondary} disabled={!s || busy}>
              Load evidence
            </button>
          </form>
          {p ? (
            <div
              className={styles.operationState}
              data-outcome={
                p.blocked || evidence.isError || readUnavailable
                  ? "uncertain"
                  : p.capture_confirmed
                    ? "recorded"
                    : "pending"
              }
            >
              <strong>
                {evidence.isError || readUnavailable
                  ? "Historical evidence; refresh unavailable"
                  : p.blocked
                    ? "Conflict hold"
                    : p.capture_confirmed
                      ? "Recorded capture verified"
                      : p.provider_order_status === "APPROVED"
                        ? "Buyer approved · capture not verified"
                        : "Capture not verified"}
              </strong>
              <span>
                {usd(p.amount_minor)} · {p.merchant_id}
              </span>
              <small>
                Provider observation:{" "}
                {p.last_canonical_read_at
                  ? new Date(p.last_canonical_read_at).toLocaleString("en-IN", {
                      timeZone: "Asia/Kolkata",
                      timeZoneName: "short",
                    })
                  : "Unavailable"}
              </small>
            </div>
          ) : (
            <p className={styles.contextEmpty}>
              Load an existing operation to inspect its payment, accounting and
              fulfillment records. This does not contact PayPal.
            </p>
          )}
        </section>
        <div
          className={styles.journey}
          aria-label="Financial authority sequence"
        >
          {[
            ["Request", Boolean(intent.trim() || a || p)],
            ["Proposal", Boolean(proposal)],
            [
              "Policy",
              Boolean(
                p?.decision_id || (a?.decision.allowed && a.current && a.fresh),
              ),
            ],
            ["Approval", Boolean(p?.approval_id || approvals.length)],
            [
              "Buyer consent",
              Boolean(
                p?.last_canonical_read_at &&
                ["APPROVED", "COMPLETED"].includes(
                  p.provider_order_status ?? "",
                ),
              ),
            ],
            [
              "Capture",
              Boolean(
                p?.capture_confirmed &&
                !p.blocked &&
                !evidence.isError &&
                !readUnavailable,
              ),
            ],
            [
              "Verification",
              Boolean(
                p?.capture_confirmed &&
                p.journals.length &&
                p.seller_fulfilled &&
                !p.blocked &&
                !evidence.isError &&
                !readUnavailable,
              ),
            ],
          ].map(([name, recorded], i) => (
            <div
              key={String(name)}
              className={recorded ? styles.journeyActive : ""}
            >
              <span>{String(i + 1).padStart(2, "0")}</span>
              {name}
              <small>
                {recorded ? "Evidence available" : "Not established"}
              </small>
            </div>
          ))}
        </div>
        <div className={styles.workGrid}>
          <section className={`${styles.panel} ${styles.requestPanel}`}>
            <div id="purchase-agent">
              <PurchasePlanner
                key={`${s?.tenant_id}:${s?.principal}`}
                scope={s}
                onHandoff={(plan) => {
                  setIntent(plan.handoff.intent);
                  setGrantId(
                    grants.some((g) => g.id === plan.handoff.grant_id)
                      ? (plan.handoff.grant_id ?? "")
                      : "",
                  );
                  setProposal(undefined);
                  setQuote(undefined);
                  setReceipt(
                    "Reviewed planning draft copied locally. No financial request sent; current authority and explicit protected preparation are still required.",
                  );
                }}
              />
            </div>
            <div id="requests-approvals" className={styles.panelHeading}>
              <span className={styles.stepNumber}>01</span>
              <div>
                <h2>Purchase request</h2>
                <p>Propose a purchase within your authorized grant.</p>
              </div>
              <span className={styles.aiTag}>AI PROPOSAL</span>
            </div>
            <label>
              Authorized USD grant
              <select
                value={grantId}
                onChange={(e) => {
                  setGrantId(e.target.value);
                  setProposal(undefined);
                  setQuote(undefined);
                }}
                disabled={locked}
              >
                <option value="">
                  {s
                    ? "Choose your active grant"
                    : "Connect authority to load grants"}
                </option>
                {grants.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.id} · {usd(g.head.free)} free
                  </option>
                ))}
              </select>
            </label>
            {grant && (
              <div className={styles.budgetStrip}>
                <div>
                  <small>LIFETIME LIMIT</small>
                  <b>{usd(grant.lifetime_bound)}</b>
                </div>
                <div>
                  <small>CONSUMED</small>
                  <b>{usd(grant.head.consumed)}</b>
                </div>
                <div>
                  <small>RESERVED</small>
                  <b>{usd(grant.head.reserved)}</b>
                </div>
                <div>
                  <small>FREE</small>
                  <b>{usd(grant.head.free)}</b>
                </div>
              </div>
            )}
            <label htmlFor="purchase-intent">
              What should your agent propose?
            </label>
            <textarea
              id="purchase-intent"
              value={intent}
              onChange={(e) => {
                setIntent(e.target.value);
                setProposal(undefined);
                setQuote(undefined);
              }}
              maxLength={1000}
              rows={4}
              placeholder="A workspace seat for an authorized beneficiary, at most USD 1.00"
              disabled={locked}
            />
            <div className={styles.fieldNote}>
              <span>No search, arbitrary destinations or payment tools.</span>
              <span>{intent.length}/1000</span>
            </div>
            <button
              className={styles.primary}
              disabled={locked || !grant || !intent.trim()}
              onClick={() =>
                command("proposals", {
                  grant_id: grantId,
                  intent: intent.trim(),
                })
              }
            >
              {busy ? "Working…" : "Generate proposal"}
              <span aria-hidden="true">✧</span>
            </button>
            {!me.data && (
              <p className={styles.emptyText}>
                Sign in to use your scoped grants. No model call is made before
                authority is available.
              </p>
            )}
            <details>
              <summary>Resume a recorded proposal</summary>
              <label>
                Recorded proposal ID
                <input
                  value={recordedProposalId}
                  onChange={(e) => setRecordedProposalId(e.target.value)}
                  autoComplete="off"
                  disabled={locked}
                />
              </label>
              <button
                className={styles.secondary}
                disabled={
                  locked || !/^[a-fA-F0-9-]{36}$/.test(recordedProposalId)
                }
                onClick={() => void loadRecordedProposal()}
              >
                Load recorded proposal
              </button>
              <small>
                Original requester and active grant required. This read does not
                call the model.
              </small>
            </details>
            {proposal && (
              <div className={styles.proposal}>
                <div className={styles.sectionHeading}>
                  <h3>Structured proposal</h3>
                  <span className={styles.badge}>NO SPENDING AUTHORITY</span>
                </div>
                {suggestion ? (
                  <dl>
                    <dt>Product</dt>
                    <dd>{suggestion.product_id}</dd>
                    <dt>Beneficiary</dt>
                    <dd>{suggestion.beneficiary}</dd>
                    <dt>Quantity</dt>
                    <dd>{suggestion.quantity}</dd>
                    <dt>Maximum</dt>
                    <dd>{usd(suggestion.spending_limit_minor)}</dd>
                  </dl>
                ) : (
                  <p>
                    {proposal.result.extraction.status} ·{" "}
                    {proposal.result.extraction.reason}
                  </p>
                )}
                <small>
                  {proposal.result.provenance.actual_model_invocation
                    ? "Actual model invocation"
                    : "Model invocation not established"}{" "}
                  · {proposal.result.provenance.model}
                  {proposal.result.provenance.latency_ms !== undefined &&
                    ` · ${proposal.result.provenance.latency_ms} ms`}
                </small>
                <button
                  className={styles.secondary}
                  disabled={locked || !proposal.proposal_id || !suggestion}
                  onClick={() =>
                    command("quotes", { proposal_id: proposal.proposal_id })
                  }
                >
                  Obtain signed seller quote
                </button>
              </div>
            )}
            {quote && (
              <div className={styles.proposal}>
                <h3>Canonical seller terms</h3>
                <p>
                  {usd(quote.quote.amount)} · expires {quote.quote.expires_at}
                </p>
                <code>{quote.quote_id}</code>
                <details>
                  <summary>Inspect signed quote terms</summary>
                  <pre>{JSON.stringify(quote.quote, null, 2)}</pre>
                </details>
                <button
                  className={styles.secondary}
                  disabled={locked}
                  onClick={() =>
                    command(
                      "commands/propose",
                      {
                        grant_id: proposal?.grant_id ?? grantId,
                        quote_id: quote.quote_id,
                        currency: "USD",
                      },
                      0,
                    )
                  }
                >
                  Evaluate deterministic policy
                </button>
              </div>
            )}
          </section>
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <span className={styles.stepNumber}>02</span>
              <div>
                <h2>Review & authorize</h2>
                <p>Exact terms, independent human approval.</p>
              </div>
            </div>
            <label>
              Existing action
              <select
                value={actionId}
                disabled={!s || busy || view.isError}
                onChange={(e) => {
                  setActionId(e.target.value);
                  setApprovalId("");
                  setReviewed(false);
                }}
              >
                <option value="">Select an action to review</option>
                {s?.actions.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.id} ·{" "}
                    {x.currency === "USD"
                      ? usd(x.amount)
                      : "Unsupported currency"}
                  </option>
                ))}
              </select>
            </label>
            {detail.isError && (
              <p className={styles.error} role="alert">
                {message(detail.error)}
              </p>
            )}
            {a ? (
              <>
                <dl
                  className={styles.exactTerms}
                  aria-label="Exact purchase terms"
                >
                  <dt>Exact price</dt>
                  <dd>{usd(a.amount)}</dd>
                  {signedLines.length > 0 && (
                    <>
                      <dt>Product</dt>
                      <dd>
                        {signedLines
                          .map((line) =>
                            typeof line.sku === "string"
                              ? line.sku
                              : "Unavailable",
                          )
                          .join(", ")}
                      </dd>
                      <dt>Beneficiary</dt>
                      <dd>
                        {signedLines
                          .map((line) =>
                            typeof line.beneficiary === "string"
                              ? line.beneficiary
                              : "Unavailable",
                          )
                          .join(", ")}
                      </dd>
                    </>
                  )}
                  <dt>Requester</dt>
                  <dd>{a.requester}</dd>
                  {Boolean(a.terms.quote) &&
                    typeof a.terms.quote === "object" && (
                      <>
                        <dt>Signed terms</dt>
                        <dd>{a.fresh ? "Current" : "Expired or stale"}</dd>
                        {typeof (a.terms.quote as Record<string, unknown>)
                          .merchant_id === "string" && (
                          <>
                            <dt>Merchant</dt>
                            <dd>
                              {String(
                                (a.terms.quote as Record<string, unknown>)
                                  .merchant_id,
                              )}
                            </dd>
                          </>
                        )}
                        {typeof (a.terms.quote as Record<string, unknown>)
                          .expires_at === "string" && (
                          <>
                            <dt>Quote expiry</dt>
                            <dd>
                              <time>
                                {new Date(
                                  String(
                                    (a.terms.quote as Record<string, unknown>)
                                      .expires_at,
                                  ),
                                ).toLocaleString("en-IN", {
                                  timeZone: "Asia/Kolkata",
                                  timeZoneName: "short",
                                })}
                              </time>
                            </dd>
                          </>
                        )}
                      </>
                    )}
                  <dt>Action revision</dt>
                  <dd>{a.head.revision}</dd>
                </dl>
                <div
                  className={`${styles.policy} ${a.decision.allowed && a.current && a.fresh && !detail.isError ? styles.policyAllowed : styles.policyDenied}`}
                >
                  <strong>
                    {detail.isError
                      ? "Policy refresh unavailable"
                      : !a.current || !a.fresh
                        ? "Stale authority — refresh required"
                        : a.decision.allowed
                          ? "Policy permitted · approval required"
                          : "Policy denied"}
                  </strong>
                  <p>
                    {a.decision.reasons.join(" · ") ||
                      "Deterministic policy evaluated the exact action."}
                  </p>
                </div>
                <details>
                  <summary>Inspect source terms & digest</summary>
                  <dl>
                    <dt>Evidence</dt>
                    <dd>{a.evidence.id}</dd>
                    <dt>Decision</dt>
                    <dd>{a.decision.id}</dd>
                  </dl>
                  <pre>{JSON.stringify(a.terms, null, 2)}</pre>
                  <code>{a.request_digest}</code>
                </details>
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    checked={reviewed}
                    onChange={(e) => setReviewed(e.target.checked)}
                    disabled={
                      !approver || locked || ownApproval || exactApprovalAck
                    }
                  />
                  I reviewed this exact USD action, beneficiary and evidence.
                </label>
                <button
                  className={styles.secondary}
                  disabled={
                    locked ||
                    detail.isError ||
                    !approver ||
                    ownApproval ||
                    exactApprovalAck ||
                    !reviewed ||
                    !a.current ||
                    !a.fresh ||
                    !a.decision.allowed
                  }
                  onClick={() =>
                    command(
                      "commands/approve",
                      {
                        action_id: a.id,
                        evidence_id: a.evidence.id,
                        decision_id: a.decision.id,
                        expires_at: new Date(
                          Date.now() + 15 * 60000,
                        ).toISOString(),
                        currency: "USD",
                      },
                      a.head.revision,
                    )
                  }
                >
                  Approve exact action
                </button>
                {exactApprovals.length > 0 && (
                  <div
                    className={
                      approvals.length === 1 ? styles.receipt : styles.warning
                    }
                    role="status"
                    aria-label="Exact action approval status"
                  >
                    <strong>
                      {approvals.length === 0
                        ? "Historical approval records"
                        : approvals.length > 1
                          ? "Approval ambiguity — admission blocked"
                          : "Independent approval recorded"}
                    </strong>
                    <p>
                      Canonical authority reports {approvals.length} available
                      approval record(s) for this exact action. No budget has
                      been reserved by approval.
                    </p>
                    {exactApprovals.map((ap) => (
                      <p key={ap.id}>
                        {ap.approver} · <code>{ap.id}</code> ·{" "}
                        {ap.effective_status}
                      </p>
                    ))}
                  </div>
                )}
                {exactApprovalAck && !ownApproval && (
                  <p className={styles.receipt} role="status">
                    Server acknowledged approval <code>{approvalAck!.id}</code>.
                    Refresh authority to verify its current validity; do not
                    approve again.
                  </p>
                )}
                {approvalError && approver && (
                  <p className={styles.error} role="alert">
                    {approvalError}
                  </p>
                )}
                {!approver && (
                  <p className={styles.emptyText}>
                    An independent PURCHASE_APPROVER account must sign in.
                    Requesters cannot approve their own action.
                  </p>
                )}
                <label>
                  Exact active approval
                  <select
                    value={approvalId}
                    onChange={(e) => setApprovalId(e.target.value)}
                    disabled={locked || !requester}
                  >
                    <option value="">
                      Choose approval after independent review
                    </option>
                    {approvals.map((ap) => (
                      <option key={ap.id} value={ap.id}>
                        {ap.approver} · {ap.id}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className={styles.primary}
                  disabled={
                    locked ||
                    detail.isError ||
                    !requester ||
                    approvals.length !== 1 ||
                    !approvals.some((ap) => ap.id === approvalId) ||
                    !a.current ||
                    !a.fresh ||
                    !a.decision.allowed
                  }
                  onClick={() =>
                    command(
                      "commands/admit",
                      {
                        action_id: a.id,
                        evidence_id: a.evidence.id,
                        decision_id: a.decision.id,
                        approval_id: approvalId,
                        currency: "USD",
                      },
                      a.head.revision,
                    )
                  }
                >
                  Reserve budget & admit
                </button>
                <p className={styles.emptyText}>
                  Admission allocates budget atomically. It does not create or
                  capture a PayPal order.
                </p>
              </>
            ) : (
              <div className={styles.emptyState}>
                <span>◇</span>
                <h3>No action selected</h3>
                <p>
                  Select an existing action or evaluate a signed quote. Policy
                  and approval will appear here after the server returns them.
                </p>
              </div>
            )}
          </section>
        </div>
        <section className={styles.panel}>
          <div className={styles.panelHeading}>
            <span className={styles.stepNumber}>03</span>
            <div>
              <h2 id="payments">PayPal consent & verification</h2>
              <p>Separate financial steps. No automatic checkout or capture.</p>
            </div>
          </div>

          {evidence.isError && (
            <p className={styles.error} role="alert">
              Evidence unavailable: {message(evidence.error)}
            </p>
          )}
          {mismatch && (
            <p className={styles.error} role="alert">
              Returned token does not match this operation’s native order.
              Navigation is not trusted; payment controls remain blocked.
            </p>
          )}
          <div className={styles.paymentSteps}>
            <div>
              <span>01 / PROTECTED HANDOFF</span>
              <h3>Create sandbox order</h3>
              <p>
                Broker checks current authority and commits a durable CREATE
                identity before sending.
              </p>
              <button
                className={styles.secondary}
                disabled={
                  locked ||
                  !paymentEnabled ||
                  !p ||
                  evidence.isError ||
                  p.blocked ||
                  p.create_committed ||
                  mismatch
                }
                onClick={() =>
                  command("create", {
                    operation_id: operationId,
                    currency: "USD",
                  })
                }
              >
                Create PayPal order
              </button>
              {p?.create_committed && !p.provider_order_id && (
                <small>
                  Order outcome unknown. Investigate; do not create another
                  order.
                </small>
              )}
            </div>
            <div>
              <span>02 / MANUAL BUYER CONSENT</span>
              <h3>Approve on PayPal</h3>
              <p>
                Buyer-owner disclosure must succeed before the bound sandbox
                checkout link is available.
              </p>
              <button
                className={styles.secondary}
                disabled={
                  locked ||
                  !paymentEnabled ||
                  !buyerOwner ||
                  !p?.provider_order_id ||
                  evidence.isError ||
                  readUnavailable ||
                  p.blocked ||
                  p.capture_confirmed ||
                  mismatch
                }
                onClick={() =>
                  command(
                    "consent",
                    { operation_id: operationId, currency: "USD" },
                    p?.dispatch_revision,
                  )
                }
              >
                Prepare buyer consent
              </button>
              {approvalUrl &&
                !mismatch &&
                p &&
                !p.blocked &&
                !p.capture_confirmed &&
                !readUnavailable &&
                !evidence.isError && (
                  <a
                    className={styles.textLink}
                    href={approvalUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open bound PayPal Sandbox checkout ↗
                  </a>
                )}
            </div>
            <div>
              <span>03 / INDEPENDENT REAUTHORIZATION</span>
              <h3>Verify, then capture</h3>
              <p>
                Canonical APPROVED, an exact buyer-owner request and a fresh
                authority check are all required.
              </p>
              <button
                className={styles.secondary}
                disabled={
                  locked ||
                  !identifier(operationId) ||
                  mismatch ||
                  Boolean(returnToken && !p)
                }
                onClick={() =>
                  command("observe", {
                    operation_id: operationId,
                    currency: "USD",
                  })
                }
              >
                Read canonical PayPal status
              </button>
              <button
                className={styles.primary}
                disabled={
                  locked ||
                  !paymentEnabled ||
                  !buyerOwner ||
                  !p ||
                  evidence.isError ||
                  readUnavailable ||
                  p.provider_order_status !== "APPROVED" ||
                  p.blocked ||
                  p.capture_confirmed ||
                  p.capture_committed ||
                  mismatch
                }
                onClick={() =>
                  command(
                    "capture-requests",
                    { operation_id: operationId, currency: "USD" },
                    p?.revision,
                  )
                }
              >
                Authorize protected capture
              </button>
            </div>
          </div>
        </section>
        <Realities proof={p} stale={evidence.isError || readUnavailable} />
        <section className={styles.reconcile}>
          <div>
            <p className={styles.eyebrow}>RECONCILIATION</p>
            <h3>Reconcile independent records</h3>
            <p>
              Read-only comparison creates findings. It never authorizes a
              payment, releases a reservation or invents agreement.
            </p>
          </div>
          <button
            className={styles.secondary}
            disabled={locked || !s?.roles.includes("PURCHASE_ISSUER")}
            onClick={() =>
              command("reconciliation/runs", {
                request_id: crypto.randomUUID(),
                batch_size: 10,
              })
            }
          >
            Run bounded reconciliation
          </button>
          {reconciliation && (
            <details>
              <summary>Inspect actual reconciliation response</summary>
              <pre>{JSON.stringify(reconciliation, null, 2)}</pre>
            </details>
          )}
        </section>
        <div className={styles.workspaceFoot}>
          <span>
            Financial records come only from authenticated backend responses.
          </span>
          <button
            className={styles.textButton}
            disabled={busy || !me.data}
            onClick={() => void refresh()}
          >
            Refresh workspace ↻
          </button>
        </div>
      </main>
    </Frame>
  );
}
