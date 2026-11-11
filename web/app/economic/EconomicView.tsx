"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

type Observation = { id: string; receipt_id: string; kind: string; object_id: string; authentication_basis: string; observed_at: string };
type Fact = { id: string; kind: string; amount: number; observation_id: string; parent_fact_id: string | null; fulfillment_fact_id: string | null };
type Posting = { id: string; fact_id: string; template: string; template_version: number; lines: { account: string; side: string; amount: number }[] };
type Truth = { operation_id: string; purchase_id: string; tenant_id: string; provider: string; amount: number; currency: string;
  intended: { action_id: string; evidence_id: string; quote_id: string }; observations: Observation[]; facts: Fact[]; postings: Posting[];
  unresolved: { reason: string; receipt_id: string }[];
  seller_entitlement: { order_id: string; workspace_id: string; state: string; seats: { id: string; beneficiary: string }[] } | null };

async function read<T>(path: string, body?: object): Promise<T> {
  const result = await fetch("/api/" + path, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}) });
  const data = await result.json();
  if (!result.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request rejected");
  return data;
}
const money = (value: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(value / 100);

export default function EconomicView({ operation }: { operation: string }) {
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}><View operation={operation} /></QueryClientProvider>;
}

function View({ operation }: { operation: string }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const query = useQuery({ queryKey: ["economic", operation], queryFn: () => read<Truth>("economic/" + operation), retry: false });
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = event.currentTarget; const values = new FormData(form);
    const password = String(values.get("password"));
    (form.elements.namedItem("password") as HTMLInputElement).value = "";
    try { await read("session", { username: String(values.get("username")), password }); await query.refetch(); }
    catch (err) { setError(err instanceof Error ? err.message : "Sign in failed"); }
    finally { setBusy(false); }
  }
  const data = query.data;
  const capture = data?.facts.find(x => x.kind === "CAPTURE");
  const fulfilled = data?.facts.find(x => x.kind === "SELLER_FULFILLED");
  const revenue = data?.facts.find(x => x.kind === "REVENUE");
  const unposted = data?.facts.filter(x => x.kind !== "SELLER_FULFILLED" && !data.postings.some(p => p.fact_id === x.id)) || [];
  return <main className="economic-page">
    <nav><Link href="/dispatch">Dispatch &amp; exposure</Link><Link href="/authority">Authority</Link></nav>
    <header><p className="eyebrow">Interlock / purchase truth</p><h1>Evidence, booking &amp; fulfillment</h1>
      <p>Each stage retains its own evidence and provenance.</p></header>
    {!data && <section><form onSubmit={login}><h2>Scoped account access</h2>
      <label>Username<input name="username" defaultValue="owner-a" autoComplete="username" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      <button disabled={busy}>Sign in</button></form>
      {query.error && <p role="status">Purchase truth requires an authorized account.</p>}
      {error && <p role="alert">{error}</p>}</section>}
    {data && <>
      <p className="account">{data.tenant_id} · {data.provider === "REFERENCE_SYNTHETIC" ? "REFERENCE_SYNTHETIC — synthetic evidence" : "Razorpay Test evidence"}</p>
      <div className="truth-stages">
        <section data-testid="intended"><h2>INTENDED</h2><p>Exact admitted purchase · {money(data.amount)}</p><code>{data.intended.action_id}</code></section>
        <section data-testid="observed"><h2>OBSERVED</h2><p>{capture ? "Captured economic effect authenticated" : "No confirmed captured economic fact"}</p><p>{data.observations.length} authenticated observations</p></section>
        <section data-testid="booked"><h2>BOOKED</h2><p>{data.postings.length} trusted journal postings</p><p>{unposted.length ? `${unposted.length} economic facts await posting` : "No economic facts await posting"}</p>
          <p>{revenue && data.postings.some(p => p.fact_id === revenue.id) ? "Revenue recognized from matched fulfillment" : "Revenue predicates not yet posted"}</p></section>
        <section data-testid="fulfilled"><h2>FULFILLED</h2><p>{fulfilled ? "Exact named workspace seats finalized" : "Seller fulfillment not established"}</p><p>Protected seller access has independent entitlement provenance.</p></section>
      </div>
      <button onClick={() => void query.refetch()}>Refresh truth</button>
      <section><h2>Evidence and provenance</h2><dl><dt>Purchase</dt><dd><code>{data.purchase_id}</code></dd><dt>Operation</dt><dd><code>{data.operation_id}</code></dd>
        <dt>Evidence revision</dt><dd><code>{data.intended.evidence_id}</code></dd><dt>Immutable seller quote</dt><dd><code>{data.intended.quote_id}</code></dd></dl>
        {data.observations.map(o => <article key={o.id}><h3>{o.kind}</h3><p>{o.authentication_basis} · {new Date(o.observed_at).toLocaleString()}</p><p>Observation <code>{o.id}</code></p><p>Raw receipt <code>{o.receipt_id}</code></p></article>)}
      </section>
      <section><h2>Economic facts</h2>{data.facts.length === 0 && <p>No economic facts established.</p>}
        {data.facts.map(f => <article key={f.id}><h3>{f.kind}</h3><p>{money(f.amount)} · <code>{f.id}</code></p><p>Source observation <code>{f.observation_id}</code></p>
          {f.parent_fact_id && <p>Parent fact <code>{f.parent_fact_id}</code></p>}{f.fulfillment_fact_id && <p>Seller fulfillment fact <code>{f.fulfillment_fact_id}</code></p>}</article>)}</section>
      <section><h2>Controlled seller entitlements</h2>{data.seller_entitlement ? <>
        <p>{data.seller_entitlement.state} · {data.seller_entitlement.workspace_id}</p>
        <p>Seller order <code>{data.seller_entitlement.order_id}</code></p>
        {data.seller_entitlement.seats.map(seat => <p key={seat.id}>{seat.beneficiary} · workspace-seat · <code>{seat.id}</code></p>)}
        <p>Finalized delivery is historical evidence. Current protected access is checked independently by the seller.</p>
      </> : <p>No matched seller entitlement finalization.</p>}</section>
      <section><h2>Principal subledger</h2><p>Fixed purchase templates · INR minor units · operational principal only</p>
        {data.postings.map(p => <article key={p.id}><h3>{p.template} · v{p.template_version}</h3><p>Posting <code>{p.id}</code> · fact <code>{p.fact_id}</code></p>
          <table><thead><tr><th>Account</th><th>Side</th><th>Principal</th></tr></thead><tbody>{p.lines.map((l, i) => <tr key={i}><td>{l.account}</td><td>{l.side}</td><td>{money(l.amount)}</td></tr>)}</tbody></table></article>)}</section>
      <section><h2>Unresolved truth</h2>{data.unresolved.length ? data.unresolved.map((x, i) => <p key={i}>{x.reason} · <code>{x.receipt_id}</code></p>) : <p>No correlated contradictions recorded.</p>}
        <p>Unmatched account evidence remains unassigned; it cannot establish this purchase or its fulfillment.</p></section>
    </>}
  </main>;
}
