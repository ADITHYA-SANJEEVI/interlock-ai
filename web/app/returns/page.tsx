"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

type Principal = { capture_fact_id: string; captured: number; processed: number; unresolved: number; refundable: number };
type Grant = { grant_id: string; status: string; bound: number; consumed: number; reserved: number; free: number; epoch: number; version_id: string; expires_at: string };
type Lock = { id: string; purchase_operation: string; raw: string; confirmed_at: string };
type Obligation = { id: string; amount: number; payment_id: string; purchase_id: string; lock_id: string; action_id: string; evidence_id: string; decision_id: string; reservation_id: string };
type Operation = { id: string; obligation_id: string; replay_class: string; runtime: { state: string; blocked: boolean; refund_id: string | null }; recovery_duty: boolean; dispatch_records: object[] };
type Fact = { id: string; kind: string; amount: number; observation_id: string | null; obligation_id: string };
type Posting = { id: string; fact_id: string; template: string; lines: { account: string; side: string; amount: number }[] };
type Truth = { tenant_id: string; principal_id: string; mismatches: Record<string, number>; principal: Principal[]; grants: Grant[]; locks: Lock[]; obligations: Obligation[]; operations: Operation[];
  observations: { id: string; receipt_id: string; refund_id: string; status: string }[]; facts: Fact[]; postings: Posting[]; closures: { lock_id: string }[] };

async function request<T>(path: string, body?: object, headers?: Record<string, string>): Promise<T> {
  const response = await fetch("/api/" + path, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json", ...headers }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request rejected");
  return data;
}
const money = (value: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(value / 100);

export default function ReturnsPage() {
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}><View /></QueryClientProvider>;
}

function View() {
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const query = useQuery({ queryKey: ["returns"], queryFn: () => request<Truth>("returns"), retry: false });
  const pendingKey = "interlock-return-pending-" + query.data?.tenant_id + ":" + query.data?.principal_id;
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const values = new FormData(event.currentTarget);
    (event.currentTarget.elements.namedItem("password") as HTMLInputElement).value = "";
    try { await request("session", { username: values.get("username"), password: values.get("password") }); await query.refetch(); }
    catch (err) { setError(err instanceof Error ? err.message : "Sign in failed"); }
    finally { setBusy(false); }
  }
  async function command(event: FormEvent<HTMLFormElement>, family: string) {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = Object.fromEntries(form.entries());
    const revision = String(body.revision || "0"); delete body.revision;
    for (const key of ["amount", "limit"]) if (key in body) body[key] = Number(body[key]);
    if ("beneficiaries" in body) body.beneficiaries = String(body.beneficiaries).split(",").map(x => x.trim());
    if ("expires_at" in body) body.expires_at = new Date(String(body.expires_at)).toISOString();
    if ("approval_id" in body && !body.approval_id) body.approval_id = null;
    const path = family === "lock" || family === "close" ? "returns/" + family : "returns/commands/" + family;
    const key = "return_" + crypto.randomUUID().replaceAll("-", "");
    try {
      if (family !== "lock" && family !== "close") localStorage.setItem(pendingKey, JSON.stringify({ family: "return-" + family, key, revision, body }));
      const data = await request<object>(path, body, { "Idempotency-Key": key, "If-Match": revision });
      setResult(JSON.stringify(data, null, 2));
      if (family !== "lock" && family !== "close") localStorage.removeItem(pendingKey);
      await query.refetch();
    } catch (err) { setError(err instanceof Error ? err.message : "Command remains unresolved"); }
    finally { setBusy(false); }
  }
  async function recover() {
    const raw = localStorage.getItem(pendingKey);
    if (!raw) { setResult("No retained command identity."); return; }
    const pending = JSON.parse(raw) as { family: string; key: string };
    try { setResult(JSON.stringify(await request("authority/requests/" + pending.family + "/" + pending.key), null, 2)); await query.refetch(); }
    catch { setError("No committed result is visible for the retained identity. Authority and principal remain unchanged unless the server committed admission."); }
  }
  const data = query.data;
  return <main className="economic-page returns-page">
    <nav><Link href="/authority">Authority</Link><Link href="/dispatch">Purchase dispatch</Link></nav>
    <header><p className="eyebrow">Interlock / commercial returns</p><h1>Returns &amp; refunds</h1>
      <p>Lock seller access, approve the exact refund, reserve principal, then independently observe and book the result.</p></header>
    {!data && <section><h2>Merchant refund access</h2><form onSubmit={login}>
      <label>Username<input name="username" defaultValue="merchant-a" autoComplete="username" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label><button disabled={busy}>Sign in</button>
    </form><p>Refund authority requires a separate merchant grant and eligible subject.</p></section>}
    {error && <p role="alert">{error}</p>}
    {data && <><p>{data.tenant_id} · Reference commercial lifecycle · operational principal subledger</p>
      <button onClick={() => void query.refetch()}>Refresh refund truth</button><button onClick={() => void recover()}>Recover command result</button>
      <div className="truth-stages">{data.principal.map(p => <section key={p.capture_fact_id} data-testid="refund-principal"><h2>Captured principal</h2>
        <dl><dt>Original captured principal</dt><dd>{money(p.captured)}</dd><dt>Available refundable principal</dt><dd>{money(p.refundable)}</dd>
          <dt>Unresolved admitted refund</dt><dd>{money(p.unresolved)}</dd><dt>Processed refund</dt><dd>{money(p.processed)}</dd></dl><code>{p.capture_fact_id}</code></section>)}</div>
      <section><h2>Current RefundGrant authority</h2>{data.grants.map(g => <article key={g.grant_id}><h3>{g.status} · epoch {g.epoch}</h3><code>{g.grant_id}</code>
        <p>Lifetime {money(g.bound)} · Spent {money(g.consumed)} · Unresolved {money(g.reserved)} · Available {money(g.free)}</p><p>Current expiry {new Date(g.expires_at).toLocaleString()}</p></article>)}
        <p>Processed refunds permanently spend refund authority. Purchase authority is never replenished.</p></section>
      <section><h2>FULFILLED · seller access and return locks</h2>{data.locks.map(l => <article key={l.id}><h3>{data.closures.some(c => c.lock_id === l.id) ? "RETURN_COMPLETED · ACCESS_REVOKED" : "RETURN_LOCKED · future access blocked"}</h3>
        <p>Historical fulfillment is retained. {new Date(l.confirmed_at).toLocaleString()}</p><code>{l.id}</code><p>Original purchase operation <code>{l.purchase_operation}</code></p>
        <pre>{JSON.stringify(JSON.parse(l.raw).seats, null, 2)}</pre></article>)}</section>
      <section><h2>INTENDED · accepted commercial obligations</h2>{data.obligations.map(b => <article key={b.id}><h3>Requested principal {money(b.amount)}</h3><code>{b.id}</code>
        <p>Purchase <code>{b.purchase_id}</code> · Payment <code>{b.payment_id}</code></p><p>Final action <code>{b.action_id}</code></p><p>Locked evidence <code>{b.evidence_id}</code></p><p>Authority reservation <code>{b.reservation_id}</code></p></article>)}</section>
      <section><h2>Dispatch and recovery</h2>{data.operations.map(o => <article key={o.id}><h3>{o.runtime.state}{o.runtime.blocked ? " · CONTRADICTION BLOCKED" : ""}</h3><code>{o.id}</code>
        <p>Historical obligation <code>{o.obligation_id}</code></p><p>Recovery duty {o.recovery_duty ? "retained" : "not created before cutoff"} · {o.replay_class}</p>
        <p>Historical committed authority</p><pre>{JSON.stringify(o.dispatch_records, null, 2)}</pre>
        {o.runtime.state === "UNKNOWN" && <p>Provider effect remains uncertain. Principal, refund authority and seller lock remain reserved. Independent reads can establish the outcome.</p>}</article>)}</section>
      <section><h2>OBSERVED · independent provider evidence</h2>{data.observations.map(o => <article key={o.id}><h3>{o.status}</h3><p>Refund <code>{o.refund_id}</code> · Receipt <code>{o.receipt_id}</code></p></article>)}</section>
      <section><h2>Economic facts</h2>{data.facts.map(f => <article key={f.id}><h3>{f.kind} · {money(f.amount)}</h3><code>{f.id}</code><p>Source {f.observation_id || "immutable accepted commercial obligation"}</p></article>)}</section>
      <section><h2>BOOKED · trusted refund templates</h2>{data.postings.map(p => <article key={p.id}><h3>{p.template}</h3><code>{p.id}</code>
        {p.lines.map((l, i) => <p key={i}>{l.side} {l.account} {money(l.amount)}</p>)}</article>)}
        <p>{data.facts.filter(f => !data.postings.some(p => p.fact_id === f.id)).length} economic facts await posting.</p></section>
      <section><h2>Independent return mismatches</h2>{Object.entries(data.mismatches).map(([kind, count]) => <p key={kind}>{kind} · {count}</p>)}
        <p>Locked entitlement allocation, seller revocation and commercial closure retain distinct evidence.</p></section>
      <section><h2>Prepare seller return</h2><form onSubmit={e => void command(e, "lock")}><label>Original purchase operation<input name="purchase_operation" required /></label><button disabled={busy}>Confirm seller return lock</button></form></section>
      <section><h2>Issue separate merchant RefundGrant</h2><form onSubmit={e => void command(e, "create-grant")}>
        {[["purchase_operation", "Original purchase operation"], ["subject", "Eligible refund requester"], ["beneficiaries", "Exact beneficiaries, comma separated"], ["limit", "Lifetime refund limit in paise"], ["expires_at", "Grant expiry"]].map(([name, label]) => <label key={name}>{label}<input name={name} type={name === "expires_at" ? "datetime-local" : name === "limit" ? "number" : "text"} required /></label>)}<button disabled={busy}>Issue RefundGrant</button></form></section>
      <section><h2>Plan the already locked refund</h2><form onSubmit={e => void command(e, "propose")}><label>Seller lock<input name="lock_id" required /></label><label>RefundGrant<input name="grant_id" required /></label><label>Requested refund in paise<input name="amount" type="number" min="1" required /></label><button disabled={busy}>Create exact refund plan</button></form></section>
      {["approve", "admit"].map(family => <section key={family}><h2>{family === "approve" ? "Independent approval" : "Accept commercial refund obligation"}</h2><form onSubmit={e => void command(e, family)}>
        {["action_id", "evidence_id", "decision_id"].map(name => <label key={name}>{name.replaceAll("_", " ")}<input name={name} required /></label>)}
        <input name="revision" type="hidden" value="1" />{family === "approve" ? <label>Approval expiry<input name="expires_at" type="datetime-local" required /></label> : <label>Exact approval, when required<input name="approval_id" /></label>}
        <button disabled={busy}>{family === "approve" ? "Approve exact refund" : "Reserve principal and accept obligation"}</button></form></section>)}
      <section><h2>Close a fully processed return</h2><form onSubmit={e => void command(e, "close")}><label>Seller lock<input name="lock_id" required /></label><button disabled={busy}>Revoke entitlement and close return</button></form><p>Requires full principal processing, trusted postings and no contradictions.</p></section>
      {result && <section><h2>Committed command result</h2><pre>{result}</pre></section>}
    </>}
  </main>;
}
