"use client";

import Link from "next/link";
import { FormEvent, useRef, useState, useSyncExternalStore } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

type Kind = "PURCHASE" | "REFUND";
type Grant = {
  id: string;
  issuer: string;
  subject: string;
  kind: Kind;
  currency: string;
  lifetime_bound: number;
  effective_status: string;
  revoked_unused: number;
  head: {
    revision: number;
    epoch: number;
    status: string;
    consumed: number;
    reserved: number;
    free: number;
  };
  version: {
    id: string;
    version: number;
    expires_at: string;
    seller: string;
    integration: string;
    beneficiaries: string[];
    resource_id: string | null;
  };
};
type Action = {
  id: string;
  intent_id: string;
  requester: string;
  grant_id: string;
  kind: Kind;
  amount: number;
  currency: string;
  request_digest: string;
  terms: Record<string, unknown>;
  current: boolean;
  fresh: boolean;
  head: { revision: number; status: string };
  evidence: {
    id: string;
    digest: string;
    grant_version_id: string;
    grant_epoch: number;
    config_epoch: number;
    policy_version_id: string;
  };
  decision: {
    id: string;
    allowed: boolean;
    approval_required: boolean;
    reasons: string[];
    evaluator_version: string;
  };
};
type Approval = {
  id: string;
  action_id: string;
  approver: string;
  expires_at: string;
  effective_status: string;
  head: { status: string };
};
type Snapshot = {
  principal: string;
  tenant_id: string;
  roles: string[];
  membership_revision: number;
  configuration: {
    kind: Kind;
    epoch: number;
    policy_version_id: string | null;
  }[];
  grants: Grant[];
  actions: Action[];
  approvals: Approval[];
  policies: {
    id: string;
    kind: Kind;
    version: number;
    approval_above: number;
    max_amount: number;
    current: boolean;
  }[];
  reservations: {
    id: string;
    grant_id: string;
    action_id: string;
    amount: number;
    status: string;
  }[];
  movements: {
    id: string;
    grant_id: string;
    kind: string;
    amount: number;
    delta_consumed: number;
    delta_reserved: number;
    delta_free: number;
    occurred_at: string;
  }[];
};
type Pending = { family: string; key: string; body: object; revision: number };

function subscribePending(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener("authority-pending", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("authority-pending", callback);
  };
}

class ApiError extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
  }
}
async function api<T>(
  path: string,
  body?: object,
  headers?: Record<string, string>,
): Promise<T> {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof data.detail === "string" ? data.detail : "Request rejected",
    );
  return data;
}
const money = (value: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(
    value / 100,
  );
function paise(value: string) {
  if (!/^\d+(\.\d{1,2})?$/.test(value))
    throw new Error("Enter an INR amount with at most two decimal places");
  const [whole, fraction = ""] = value.split(".");
  return Number(whole + fraction.padEnd(2, "0"));
}
const expiry = (minutes: number) =>
  new Date(Date.now() + minutes * 60000).toISOString();

export default function AuthorityPage() {
  const [client] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <Authority />
    </QueryClientProvider>
  );
}
function Authority() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState("");
  const pendingRef = useRef<Pending | null>(null);
  const [kind, setKind] = useState<Kind>("PURCHASE");
  const [subject, setSubject] = useState("requester-a");
  const [bound, setBound] = useState("60.00");
  const [threshold, setThreshold] = useState("0.00");
  const [maximum, setMaximum] = useState("150.00");
  const [requestAmount, setRequestAmount] = useState("30.00");
  const [approvalMinutes, setApprovalMinutes] = useState("30");
  const view = useQuery({
    queryKey: ["authority"],
    queryFn: () => api<Snapshot>("authority"),
    retry: false,
    refetchInterval: 5000,
  });
  const s = view.data;
  const pendingKey = s
    ? `interlock:authority-command:${s.tenant_id}:${s.principal}`
    : "";
  const storedPending = useSyncExternalStore(
    subscribePending,
    () => (pendingKey ? window.localStorage.getItem(pendingKey) : null),
    () => null,
  );
  const pending: Pending | null = storedPending
    ? JSON.parse(storedPending)
    : null;
  function setPending(value: Pending | null) {
    if (!pendingKey) throw new Error("An authenticated account is required");
    if (value) window.localStorage.setItem(pendingKey, JSON.stringify(value));
    else window.localStorage.removeItem(pendingKey);
    window.dispatchEvent(new Event("authority-pending"));
  }
  const issuerKinds = (["PURCHASE", "REFUND"] as Kind[]).filter((k) =>
    s?.roles.includes(k + "_ISSUER"),
  );
  const issuerKind = issuerKinds.includes(kind) ? kind : issuerKinds[0];
  const blocked = busy || pending !== null;
  async function refresh() {
    await qc.invalidateQueries({ queryKey: ["authority"] });
  }
  async function command(family: string, body: object, revision: number) {
    if (pending || pendingRef.current) {
      setError(
        "Recover the existing command before preparing another admission.",
      );
      return;
    }
    const request = {
      family,
      body,
      revision,
      key: "web_" + crypto.randomUUID().replaceAll("-", ""),
    };
    pendingRef.current = request;
    setPending(request);
    setBusy(true);
    setError("");
    setReceipt("");
    try {
      const result = await api<{ command_id: string; status: string }>(
        "authority/commands/" + family,
        body,
        { "Idempotency-Key": request.key, "If-Match": String(revision) },
      );
      await refresh();
      setReceipt(result.status + " · " + result.command_id);
      pendingRef.current = null;
      setPending(null);
    } catch (err) {
      if (err instanceof ApiError && err.status < 500) {
        pendingRef.current = null;
        setPending(null);
        await refresh();
      }
      setError(
        (err instanceof Error ? err.message : "Response unavailable") +
          (err instanceof ApiError && err.status === 409
            ? " · Current server revision is shown below; your input is preserved."
            : ""),
      );
    } finally {
      setBusy(false);
    }
  }
  async function recover() {
    if (!pending) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ command_id: string; status: string }>(
        `authority/requests/${pending.family}/${pending.key}`,
      );
      await refresh();
      setReceipt(result.status + " · " + result.command_id);
      pendingRef.current = null;
      setPending(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Command remains unresolved",
      );
    } finally {
      setBusy(false);
    }
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const passwordInput = event.currentTarget.elements.namedItem("password");
    if (passwordInput instanceof HTMLInputElement) passwordInput.value = "";
    setBusy(true);
    setError("");
    try {
      await api("session", {
        username: form.get("username"),
        password: form.get("password"),
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }
  async function prepare(g: Grant, old?: Action) {
    setBusy(true);
    setError("");
    try {
      if (g.kind === "PURCHASE") {
        const body = {
          provider: "REFERENCE_SYNTHETIC",
          lines: [
            { sku: "workspace-seat", beneficiary: g.version.beneficiaries[0] },
          ],
        };
        const quoteRequest = {
          family: "quote",
          key: "web_" + crypto.randomUUID().replaceAll("-", ""),
          body,
          revision: 0,
        };
        pendingRef.current = quoteRequest;
        setPending(quoteRequest);
        const quote = await api<{ id: string }>("authority/quotes", body, {
          "Idempotency-Key": quoteRequest.key,
        });
        pendingRef.current = null;
        setPending(null);
        await command(
          "propose",
          {
            grant_id: g.id,
            quote_id: quote.id,
            intent_id: old?.intent_id ?? null,
          },
          old?.head.revision ?? 0,
        );
      } else {
        await command(
          "propose",
          {
            grant_id: g.id,
            resource_id: g.version.resource_id,
            amount: paise(requestAmount),
            intent_id: old?.intent_id ?? null,
          },
          old?.head.revision ?? 0,
        );
      }
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.status < 500 &&
        pendingRef.current?.family === "quote"
      ) {
        pendingRef.current = null;
        setPending(null);
      }
      setError(err instanceof Error ? err.message : "Preparation failed");
    } finally {
      setBusy(false);
    }
  }
  function exact(a: Action) {
    return {
      action_id: a.id,
      evidence_id: a.evidence.id,
      decision_id: a.decision.id,
    };
  }
  return (
    <main className="authority-page">
      <header>
        <Link href="/" className="brand">
          <span className="mark">↔</span> interlock
        </Link>
        <span className="boundary">PASS 1 / AUTHORITY & EXACT ADMISSION</span>
        <span className="test">LOCAL AUTHORITY</span>
      </header>
      <section className="intro">
        <p className="eyebrow">LIFETIME AUTHORITY · EXACT TERMS</p>
        <h1>
          Every commitment.
          <br />
          <span>Within its authority.</span>
        </h1>
        <p>
          Inspect conserved capacity, freeze the exact commercial terms, and
          admit only the revision that survived policy and approval checks.
        </p>
      </section>
      <nav className="authority-nav">
        <Link href="/">Purchase evidence</Link>
        <Link href="/dispatch">Dispatch & exposure</Link>
        <a href="#grants">Authority</a>
        <a href="#approvals">Terms & approvals</a>
      </nav>
      <aside className="notice">
        <strong>Local admission only.</strong> An admitted record carries no
        permission to send a provider request. Refund capacity fixtures are
        synthetic. No payment or refund is sent here.
      </aside>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {receipt && (
        <p role="status" className="command-receipt">
          {receipt}
        </p>
      )}
      {pending && (
        <section className="notice">
          <strong>Command response unresolved.</strong>
          <code>{pending.key}</code>
          <button className="secondary" disabled={busy} onClick={recover}>
            Recover existing command
          </button>
        </section>
      )}
      {!s ? (
        <section className="login card">
          <h2>Sign in to authority</h2>
          <p>
            Use a distinct authenticated issuer, requester or approver account
            from your local configuration.
          </p>
          <form onSubmit={login}>
            <label>
              Username
              <input
                name="username"
                defaultValue="owner-a"
                autoComplete="username"
                required
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
            <button disabled={busy}>Sign in</button>
          </form>
        </section>
      ) : (
        <>
          <div className="account">
            <span>
              {s.principal}{" "}
              <small>
                / {s.tenant_id} · membership revision {s.membership_revision}
              </small>
            </span>
            <span>{s.roles.join(" · ").replaceAll("_", " ")}</span>
          </div>
          {issuerKinds.length > 0 && (
            <section className="authority-controls card">
              <div>
                <p className="eyebrow">ISSUING AUTHORITY</p>
                <h2>Publish policy. Delegate a fixed bound.</h2>
                <label>
                  Grant type
                  <select
                    value={issuerKind}
                    onChange={(e) => {
                      setKind(e.target.value as Kind);
                      setSubject(
                        e.target.value === "REFUND"
                          ? "refund-requester-a"
                          : "requester-a",
                      );
                    }}
                  >
                    {issuerKinds.map((k) => (
                      <option key={k} value={k}>
                        {k === "PURCHASE"
                          ? "Buyer purchase"
                          : "Merchant refund"}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void command(
                    "publish-policy",
                    {
                      kind: issuerKind,
                      approval_above: paise(threshold),
                      max_amount: paise(maximum),
                    },
                    s.configuration.find((c) => c.kind === issuerKind)?.epoch ??
                      1,
                  );
                }}
              >
                <h3>Typed policy</h3>
                <p className="footnote">
                  Current policy/configuration epoch{" "}
                  {s.configuration.find((c) => c.kind === issuerKind)?.epoch ??
                    1}
                </p>
                <label>
                  Independent approval above (INR)
                  <input
                    value={threshold}
                    onChange={(e) => setThreshold(e.target.value)}
                    inputMode="decimal"
                    required
                  />
                </label>
                <label>
                  Maximum action amount (INR)
                  <input
                    value={maximum}
                    onChange={(e) => setMaximum(e.target.value)}
                    inputMode="decimal"
                    required
                  />
                </label>
                <button disabled={blocked}>Publish policy</button>
              </form>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void command(
                    "create-grant",
                    {
                      kind: issuerKind,
                      subject,
                      lifetime_bound: paise(bound),
                      currency: "INR",
                      beneficiaries: [
                        s.tenant_id === "tenant-a" ? "alice" : "bob",
                      ],
                      expires_at: expiry(1440),
                      resource_id:
                        issuerKind === "REFUND"
                          ? "refund_fixture_" + s.tenant_id.slice(-1)
                          : null,
                    },
                    0,
                  );
                }}
              >
                <h3>Fixed lifetime grant</h3>
                <label>
                  Authenticated subject
                  <input
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    required
                  />
                </label>
                <label>
                  Lifetime bound (INR)
                  <input
                    value={bound}
                    onChange={(e) => setBound(e.target.value)}
                    inputMode="decimal"
                    required
                  />
                </label>
                <p className="footnote">
                  INR · named beneficiary · fixed seller/integration · expires
                  in 24 hours. Version changes retain the lifetime bound.
                </p>
                <button disabled={blocked}>Create draft grant</button>
              </form>
            </section>
          )}
          <section id="grants">
            <div className="section-title">
              <h2>Authority</h2>
              <button className="secondary" disabled={busy} onClick={refresh}>
                Refresh server state
              </button>
            </div>
            <p>
              Revocation blocks new commitments but does not erase existing
              admitted exposure or history.
            </p>
            {s.grants.length === 0 && (
              <section className="card empty">
                No grants are visible to this account.
              </section>
            )}
            <div className="authority-grants">
              {s.grants.map((g) => (
                <article
                  className="card grant-card"
                  key={g.id}
                  data-testid="grant-card"
                >
                  <div className="section-title">
                    <h3>
                      {g.kind === "PURCHASE"
                        ? "Buyer purchase authority"
                        : "Merchant refund authority"}
                    </h3>
                    <span className="source">{g.effective_status}</span>
                  </div>
                  <code>{g.id}</code>
                  <div className="capacity-grid">
                    {[
                      [
                        g.kind === "PURCHASE" ? "B" : "G",
                        "Lifetime",
                        g.lifetime_bound,
                      ],
                      [
                        g.kind === "PURCHASE" ? "C" : "S",
                        "Consumed",
                        g.head.consumed,
                      ],
                      [
                        g.kind === "PURCHASE" ? "R" : "Q",
                        "Reserved",
                        g.head.reserved,
                      ],
                      [
                        g.kind === "PURCHASE" ? "F" : "A",
                        "Unused",
                        g.head.free,
                      ],
                    ].map(([symbol, label, value]) => (
                      <div key={String(symbol)}>
                        <small>
                          {symbol} · {label}
                        </small>
                        <strong>{money(Number(value))}</strong>
                      </div>
                    ))}
                  </div>
                  <p className="conservation">
                    {g.kind === "PURCHASE" ? "B = C + R + F" : "G = S + Q + A"}{" "}
                    · {g.currency}
                  </p>
                  <dl>
                    <Info
                      name="Issuer → subject"
                      value={`${g.issuer} → ${g.subject}`}
                    />
                    <Info
                      name="Version / epoch / head revision"
                      value={`${g.version.version} / ${g.head.epoch} / ${g.head.revision}`}
                    />
                    <Info
                      name="Expires"
                      value={new Date(g.version.expires_at).toLocaleString()}
                    />
                    <Info
                      name="Seller / integration"
                      value={`${g.version.seller} / ${g.version.integration}`}
                    />
                    <Info
                      name="Exact scope"
                      value={`${g.version.beneficiaries.join(", ")}${g.version.resource_id ? " / " + g.version.resource_id + " (local fixture)" : " / workspace-seat"}`}
                    />
                  </dl>
                  {g.revoked_unused > 0 && (
                    <p className="notice">
                      {money(g.revoked_unused)} remains unused but is blocked by
                      revocation.
                    </p>
                  )}
                  <div className="actions">
                    {g.issuer === s.principal && g.head.status === "DRAFT" && (
                      <button
                        disabled={blocked}
                        onClick={() =>
                          command(
                            "activate-grant",
                            { grant_id: g.id },
                            g.head.revision,
                          )
                        }
                      >
                        Activate grant
                      </button>
                    )}
                    {g.issuer === s.principal &&
                      g.effective_status === "ACTIVE" && (
                        <>
                          <button
                            className="secondary"
                            disabled={blocked}
                            onClick={() =>
                              command(
                                "revoke-grant",
                                { grant_id: g.id },
                                g.head.revision,
                              )
                            }
                          >
                            Revoke future authority
                          </button>
                          <button
                            className="secondary"
                            disabled={blocked}
                            onClick={() =>
                              command(
                                "revise-grant",
                                {
                                  grant_id: g.id,
                                  beneficiaries: g.version.beneficiaries,
                                  expires_at: expiry(1440),
                                },
                                g.head.revision,
                              )
                            }
                          >
                            Publish successor grant version
                          </button>
                        </>
                      )}
                    {g.subject === s.principal &&
                      g.effective_status === "ACTIVE" && (
                        <button disabled={blocked} onClick={() => prepare(g)}>
                          Prepare exact action
                        </button>
                      )}
                  </div>
                  {g.kind === "REFUND" && g.subject === s.principal && (
                    <label>
                      Local refund capacity request (INR)
                      <input
                        value={requestAmount}
                        onChange={(e) => setRequestAmount(e.target.value)}
                        inputMode="decimal"
                      />
                    </label>
                  )}
                  <details>
                    <summary>
                      Reservations and immutable authority movements
                    </summary>
                    <table>
                      <thead>
                        <tr>
                          <th>Transition / resource</th>
                          <th>Amount</th>
                          <th>State / delta</th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.reservations
                          .filter((r) => r.grant_id === g.id)
                          .map((r) => (
                            <tr key={r.id}>
                              <td>
                                <code>{r.id}</code>
                              </td>
                              <td>{money(r.amount)}</td>
                              <td>{r.status}</td>
                            </tr>
                          ))}
                        {s.movements
                          .filter((m) => m.grant_id === g.id)
                          .map((m) => (
                            <tr key={m.id}>
                              <td>
                                {m.kind}
                                <small>{m.id}</small>
                              </td>
                              <td>{money(m.amount)}</td>
                              <td>
                                {m.delta_consumed} / {m.delta_reserved} /{" "}
                                {m.delta_free}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </details>
                </article>
              ))}
            </div>
          </section>
          <section id="approvals">
            <h2>Immutable terms & exact approval</h2>
            <p>
              Each approval binds one action, evidence revision, policy decision
              and admission target.
            </p>
            {s.actions.length === 0 && (
              <section className="card empty">
                No prepared action revisions are visible.
              </section>
            )}
            {s.actions.map((a) => {
              const grant = s.grants.find((g) => g.id === a.grant_id);
              const approval = s.approvals.find(
                (p) =>
                  p.action_id === a.id && p.effective_status === "AVAILABLE",
              );
              const eligible =
                a.current &&
                a.fresh &&
                a.head.status === "PREPARED" &&
                a.decision.allowed;
              return (
                <article
                  className="card approval-card"
                  key={a.id}
                  data-testid="action-card"
                >
                  <div className="section-title">
                    <h3>
                      {money(a.amount)} ·{" "}
                      {a.kind === "PURCHASE"
                        ? "Exact workspace purchase"
                        : "Local refund capacity fixture"}
                    </h3>
                    <span className="source">
                      {a.head.status === "ADMITTED" && a.current
                        ? "ADMITTED · LOCAL ONLY"
                        : !a.current
                          ? "SUPERSEDED"
                          : a.fresh
                            ? "PREPARED"
                            : "STALE"}
                    </span>
                  </div>
                  <code>{a.id}</code>
                  <dl>
                    <Info name="Requester" value={a.requester} />
                    <Info name="Evidence revision" value={a.evidence.id} />
                    <Info
                      name="Grant version / epoch"
                      value={`${a.evidence.grant_version_id} / ${a.evidence.grant_epoch}`}
                    />
                    <Info
                      name="Policy / configuration epoch"
                      value={`${a.evidence.policy_version_id} / ${a.evidence.config_epoch}`}
                    />
                    <Info
                      name="Policy explanation"
                      value={a.decision.reasons.join(", ").replaceAll("_", " ")}
                    />
                    <Info
                      name="Evaluator"
                      value={a.decision.evaluator_version}
                    />
                    <Info name="Exact terms digest" value={a.request_digest} />
                  </dl>
                  <details>
                    <summary>Frozen commercial terms and resources</summary>
                    <pre>{JSON.stringify(a.terms, null, 2)}</pre>
                  </details>
                  <div className="actions">
                    {eligible &&
                      a.decision.approval_required &&
                      s.roles.includes(a.kind + "_APPROVER") &&
                      a.requester !== s.principal && (
                        <>
                          <label>
                            Approval validity (minutes)
                            <input
                              type="number"
                              min="1"
                              max="60"
                              value={approvalMinutes}
                              onChange={(e) =>
                                setApprovalMinutes(e.target.value)
                              }
                            />
                          </label>
                          <button
                            disabled={blocked}
                            onClick={() =>
                              command(
                                "approve",
                                {
                                  ...exact(a),
                                  expires_at: expiry(Number(approvalMinutes)),
                                },
                                a.head.revision,
                              )
                            }
                          >
                            Approve exact revision
                          </button>
                        </>
                      )}
                    {eligible &&
                      a.requester === s.principal &&
                      (!a.decision.approval_required || approval) && (
                        <button
                          disabled={blocked}
                          onClick={() =>
                            command(
                              "admit",
                              {
                                ...exact(a),
                                approval_id: approval?.id ?? null,
                              },
                              a.head.revision,
                            )
                          }
                        >
                          Admit exact action locally
                        </button>
                      )}
                    {a.current &&
                      a.head.status === "PREPARED" &&
                      a.requester === s.principal &&
                      grant?.effective_status === "ACTIVE" && (
                        <button
                          className="secondary"
                          disabled={blocked}
                          onClick={() => prepare(grant, a)}
                        >
                          Prepare successor revision
                        </button>
                      )}
                  </div>
                  {a.decision.approval_required &&
                    a.requester === s.principal &&
                    !approval &&
                    a.head.status === "PREPARED" && (
                      <p className="notice">
                        Independent approval is required. The requester cannot
                        approve their own action.
                      </p>
                    )}
                  {s.approvals
                    .filter((p) => p.action_id === a.id)
                    .map((p) => (
                      <p key={p.id} className="approval-receipt">
                        <code>{p.id}</code> · {p.approver} ·{" "}
                        {p.effective_status} · expires{" "}
                        {new Date(p.expires_at).toLocaleString()}
                      </p>
                    ))}
                </article>
              );
            })}
          </section>
          <section className="card">
            <h2>Published policy versions</h2>
            {s.policies.map((p) => (
              <p key={p.id}>
                <code>{p.id}</code> · {p.kind} v{p.version} ·{" "}
                {p.current ? "CURRENT" : "HISTORICAL"} · independent approval
                above {money(p.approval_above)} · maximum {money(p.max_amount)}
              </p>
            ))}
          </section>
        </>
      )}
      <footer>
        Interlock · Pass 1 authority and exact admission
        <span>Local commitments carry no dispatch permission</span>
      </footer>
    </main>
  );
}
function Info({ name, value }: { name: string; value: string }) {
  return (
    <div>
      <dt>{name}</dt>
      <dd>{value}</dd>
    </div>
  );
}
