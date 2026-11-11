"use client";
import { FormEvent, useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

type Line = {
  beneficiary: string;
  allocation_id: string;
  unit_price: number;
  sku: string;
};
type Purchase = {
  id: string;
  tenant_id: string;
  buyer: string;
  owner: string;
  seller: string;
  provider: string;
  account: string;
  mode: string;
  amount: number;
  currency: string;
  quote: {
    id: string;
    revision: number;
    catalog_revision: string;
    lines: Line[];
  };
  request_digest: string;
  seller_order: string | null;
  provider_order: string | null;
  payment_id: string | null;
  preparation: string;
  canonical_status: string;
  provider_status: string;
  entitlement: string;
  exposed_at: string | null;
  dismissed_at: string | null;
  conflict: boolean;
  operations: {
    id: string;
    kind: string;
    knowledge: string;
    amount: number;
    resource_id: string | null;
  }[];
  evidence: {
    id: string;
    source: string;
    observed_at: string;
    provider: string;
    payload: object;
  }[];
};
type CheckoutResult = {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
};
declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const result = await fetch(`/api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await result.json();
  if (!result.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The exact request was rejected",
    );
  return data;
}
const money = (amount: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(
    amount / 100,
  );
const show = (value: string | null) => value || "Awaiting evidence";

export default function Page() {
  const [client] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <EvidencePage />
    </QueryClientProvider>
  );
}
function EvidencePage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState("REFERENCE_SYNTHETIC");
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () =>
      api<{ username: string; tenant_id: string; beneficiary: string }>("me"),
    retry: false,
  });
  const list = useQuery({
    queryKey: ["purchases"],
    queryFn: () =>
      api<{ id: string; provider: string; amount: number }[]>("purchases"),
    enabled: !!me.data,
  });
  const detail = useQuery({
    queryKey: ["purchase", selected],
    queryFn: () => api<Purchase>(`purchases/${selected}`),
    enabled: !!selected,
    refetchInterval: 4000,
  });
  const p = detail.data;
  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["purchase"] });
      await qc.invalidateQueries({ queryKey: ["purchases"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await action(async () => {
      await api("session", {
        username: data.get("username"),
        password: data.get("password"),
      });
      setSelected(null);
      await qc.invalidateQueries();
    });
  }
  async function create() {
    await action(async () => {
      const result = await api<{ id: string }>("purchases", {
        provider,
        lines: [{ sku: "workspace-seat", beneficiary: me.data?.beneficiary }],
      });
      setSelected(result.id);
    });
  }
  async function checkout() {
    if (!p) return;
    await action(async () => {
      const options = await api<Record<string, unknown>>(
        `purchases/${p.id}/checkout`,
        {},
      );
      if (!window.Razorpay)
        await new Promise<void>((resolve, reject) => {
          const script = document.createElement("script");
          script.src = "https://checkout.razorpay.com/v1/checkout.js";
          script.onload = () => resolve();
          script.onerror = () =>
            reject(
              new Error("Checkout could not load. Exposure remains committed."),
            );
          document.head.appendChild(script);
        });
      if (!window.Razorpay)
        throw new Error("Checkout unavailable; exposure remains committed");
      const flow = new window.Razorpay({
        ...options,
        handler: (result: CheckoutResult) => {
          void action(async () => {
            await api(`purchases/${p.id}/callback`, result);
          });
        },
        modal: {
          ondismiss: () => {
            void action(async () => {
              await api(`purchases/${p.id}/dismiss`, {});
            });
          },
        },
      });
      flow.open();
    });
  }
  return (
    <main>
      <header>
        <a href="/" className="brand">
          <span className="mark">↔</span> interlock
        </a>
        <span className="boundary">PASS 0 / CONNECTED CONTRACT PROOF</span>
        <span className="test">TEST ENVIRONMENT</span>
      </header>
      <section className="intro">
        <a className="authority-entry" href="/authority">
          Authority & exact approvals →
        </a>
        <p className="eyebrow">FINANCIAL OPERATIONS CONTROL PLANE</p>
        <h1>Reference payment lab</h1>
        <p>
          Follow one purchase from frozen commercial terms to a server-observed
          payment and a protected workspace seat.
        </p>
      </section>
      <aside className="notice">
        <strong>Test mode. No live money.</strong> Owner Checkout authenticates
        funding. Browser success and dismissal do not establish the financial
        outcome. Reference results are synthetic.
      </aside>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {!me.data ? (
        <section className="login card">
          <h2>Sign in to your local owner account</h2>
          <p>
            Distinct accounts enforce tenant scope. Local credentials are in the
            ignored .env.pass0 file.
          </p>
          <form onSubmit={login}>
            <label>
              Username
              <input
                name="username"
                autoComplete="username"
                defaultValue="owner-a"
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
              {me.data.username} <small> / {me.data.tenant_id}</small>
            </span>
            <span>
              Owner-authenticated funding · INR · one controlled seller
            </span>
          </div>
          <div className="grid">
            <aside className="sidebar card">
              <p className="eyebrow">PURCHASES</p>
              <h2>Exact seat requests</h2>
              <label>
                Evidence source
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  <option value="REFERENCE_SYNTHETIC">
                    Reference / synthetic
                  </option>
                  <option value="RAZORPAY_TEST">Razorpay / TEST</option>
                </select>
              </label>
              <p>
                One named seat for <strong>{me.data.beneficiary}</strong> ·
                ₹30.00 principal.
              </p>
              <button disabled={busy} onClick={create}>
                Freeze seller quote
              </button>
              <nav>
                {list.data?.map((row) => (
                  <button
                    className={`purchase-link ${selected === row.id ? "selected" : ""}`}
                    key={row.id}
                    onClick={() => setSelected(row.id)}
                  >
                    <span>{row.id.slice(0, 12)}…</span>
                    <small>
                      {row.provider === "RAZORPAY_TEST"
                        ? "Razorpay Test"
                        : "Reference / synthetic"}{" "}
                      · {money(row.amount)}
                    </small>
                  </button>
                ))}
              </nav>
            </aside>
            <section className="content">
              {detail.error ? (
                <div role="alert" className="error">
                  Evidence could not be loaded. Refresh or sign in again.
                </div>
              ) : p ? (
                <>
                  <div className="purchase-head">
                    <div>
                      <p className="eyebrow">PURCHASE EVIDENCE</p>
                      <h2>
                        {money(p.amount)}{" "}
                        <span className="currency">INR principal</span>
                      </h2>
                      <code>{p.id}</code>
                    </div>
                    <span
                      className={
                        p.provider === "RAZORPAY_TEST"
                          ? "source real"
                          : "source"
                      }
                    >
                      {p.provider === "RAZORPAY_TEST"
                        ? "RAZORPAY / TEST"
                        : "REFERENCE / SYNTHETIC"}
                    </span>
                  </div>
                  <div className="realities">
                    <Reality label="Intended" value="Frozen exact seats" />
                    <Reality label="Observed" value={p.canonical_status} />
                    <Reality label="Booked" value="Later pass" />
                    <Reality label="Fulfilled" value={p.entitlement} />
                  </div>
                  {p.conflict && (
                    <div className="error">
                      Conflicting evidence blocks fulfillment and new effects.
                    </div>
                  )}
                  {p.exposed_at && !p.payment_id && (
                    <div className="notice">
                      Checkout disclosed at{" "}
                      {new Date(p.exposed_at).toLocaleString()}. Exposure
                      remains unresolved, including after dismissal.
                    </div>
                  )}
                  <div className="actions">
                    <button
                      disabled={busy || !!p.provider_order}
                      onClick={() =>
                        action(() => api(`purchases/${p.id}/prepare`, {}))
                      }
                    >
                      Accept & prepare exact order
                    </button>
                    {p.provider_order &&
                      (p.provider === "RAZORPAY_TEST" ? (
                        <button
                          disabled={busy || !!p.payment_id}
                          onClick={checkout}
                        >
                          Open owner Test Checkout
                        </button>
                      ) : (
                        <button
                          disabled={busy || !!p.payment_id}
                          onClick={() =>
                            action(() =>
                              api(`purchases/${p.id}/synthetic-capture`, {}),
                            )
                          }
                        >
                          Run synthetic capture
                        </button>
                      ))}
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        action(() => api(`purchases/${p.id}/refresh`, {}))
                      }
                    >
                      Refresh canonical evidence
                    </button>
                  </div>
                  <section className="card terms">
                    <div className="section-title">
                      <h3>Commercial binding</h3>
                      <span>Immutable quote · revision {p.quote.revision}</span>
                    </div>
                    <dl>
                      <Field
                        label="Tenant / buyer"
                        value={`${p.tenant_id} / ${p.buyer}`}
                      />
                      <Field label="Seller" value={p.seller} />
                      <Field
                        label="Quote"
                        value={`${p.quote.id} / ${p.quote.catalog_revision}`}
                      />
                      <Field
                        label="Accepted seller order"
                        value={show(p.seller_order)}
                      />
                    </dl>
                    <table>
                      <thead>
                        <tr>
                          <th>Seat</th>
                          <th>Named beneficiary</th>
                          <th>Principal</th>
                        </tr>
                      </thead>
                      <tbody>
                        {p.quote.lines.map((x) => (
                          <tr key={x.allocation_id}>
                            <td>
                              Workspace seat<small>{x.allocation_id}</small>
                            </td>
                            <td>{x.beneficiary}</td>
                            <td>{money(x.unit_price)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="footnote">
                      Workspace notebook access is enforced by the seller’s
                      protected API. Taxes, fees and settlement are excluded.
                    </p>
                  </section>
                  <section className="card">
                    <h3>Provider correlation</h3>
                    <dl>
                      <Field
                        label="Account / environment"
                        value={`${p.account} / ${p.mode}`}
                      />
                      <Field
                        label="Provider order"
                        value={show(p.provider_order)}
                      />
                      <Field label="Payment" value={show(p.payment_id)} />
                      <Field
                        label="Provider status"
                        value={p.provider_status}
                      />
                      <Field
                        label="Server canonical status"
                        value={p.canonical_status}
                      />
                      <Field label="Seller entitlement" value={p.entitlement} />
                    </dl>
                    {p.operations.map((op) => (
                      <div className="operation" key={op.id}>
                        <span>
                          {op.kind}
                          <small>{op.id}</small>
                        </span>
                        <span>{op.knowledge}</span>
                      </div>
                    ))}
                  </section>
                  <section className="card">
                    <div className="section-title">
                      <h3>Evidence trail</h3>
                      <span>Server state · latest 40 observations</span>
                    </div>
                    {p.evidence.length ? (
                      p.evidence.map((item) => (
                        <details key={item.id}>
                          <summary>
                            <span>
                              {item.source}
                              <small>{item.provider}</small>
                            </span>
                            <time>
                              {new Date(item.observed_at).toLocaleString()}
                            </time>
                          </summary>
                          <pre>{JSON.stringify(item.payload, null, 2)}</pre>
                        </details>
                      ))
                    ) : (
                      <p className="empty">
                        No provider observations yet. Frozen intent is not
                        financial success.
                      </p>
                    )}
                  </section>
                </>
              ) : (
                <section className="card empty">
                  <span className="empty-icon">↔</span>
                  <h2>Select a purchase to inspect its evidence</h2>
                  <p>
                    Start with one exact quote. Each boundary records its own
                    result.
                  </p>
                </section>
              )}
            </section>
          </div>
        </>
      )}
      <footer>
        Interlock · Financial Operations Control Plane{" "}
        <span>Pass 0 — no release certification</span>
      </footer>
    </main>
  );
}
function Reality({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <small>{label}</small>
      <strong>{value.replaceAll("_", " ")}</strong>
    </div>
  );
}
function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
