"use client";

import Link from "next/link";
import { FormEvent, useRef, useState, useSyncExternalStore } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

type Exposure = { id: string; provider_order: string; first_disclosed_at: string; amount: number; currency: string };
type Duty = { id: string; origin_kind: string; predicate: string; age_seconds: number;
  runtime: { state: string; blocked_reason: string | null; discharge_observation_id: string | null } };
type Operation = {
  operation: { id: string; tenant_id: string; domain_root: string; action_id: string; evidence_id: string;
    reservation_id: string; amount: number; currency: string; adapter: string; account_id: string;
    integration: string; operation_key: string; request_digest: string; grant_id: string; grant_epoch: number; created_at: string };
  runtime: { state: string; knowledge: string; revision: number; attempt_count: number;
    blocked_reason: string | null; provider_order: string | null; updated_at: string };
  grant_head: { status: string; bound: number; consumed: number; reserved: number; free: number; epoch: number };
  reservation: { id: string; amount: number };
  exposure_state: { state: string };
  replay_contract: { classification: string; verified: boolean; retention_seconds: number };
  observation_permitted: boolean; disclosure_permitted: boolean; age_seconds: number;
  attempts: { id: string; number: number; committed_at: string; runtime: { state: string };
    dispatch: { id: string; authorization_reason: string; service_revision: number; grant_epoch: number } }[];
  exposures: Exposure[]; duties: Duty[];
  observations: { id: string; source: string; observed_at: string; classification: string; body: object }[];
};
type Snapshot = { principal: string; tenant_id: string; operations: Operation[] };
type Pending = { key: string; operation_id: string; revision: number };
type Instructions = { exposure_id: string; provider_order: string; amount: number; currency: string; mode: string };

class ApiError extends Error { constructor(public status: number, detail: string) { super(detail); } }
async function api<T>(path: string, body?: object, headers?: Record<string, string>): Promise<T> {
  const response = await fetch("/api/" + path, { method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const result = await response.json();
  if (!response.ok) throw new ApiError(response.status, typeof result.detail === "string" ? result.detail : "Request rejected");
  return result;
}
const money = (value: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(value / 100);
const when = (value: string) => new Date(value).toLocaleString();
const committedAge = (row: Operation) => row.attempts.length
  ? Math.max(0, Math.floor(row.age_seconds - (new Date(row.attempts[0].committed_at).getTime() - new Date(row.operation.created_at).getTime()) / 1000))
  : 0;
function subscribe(callback: () => void) {
  window.addEventListener("storage", callback); window.addEventListener("dispatch-pending", callback);
  return () => { window.removeEventListener("storage", callback); window.removeEventListener("dispatch-pending", callback); };
}

export default function DispatchView({ selected }: { selected?: string }) {
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}><Dispatch selected={selected} /></QueryClientProvider>;
}
function Dispatch({ selected }: { selected?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [instructions, setInstructions] = useState<Instructions | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  const view = useQuery({ queryKey: ["dispatch", selected], queryFn: () => api<Snapshot>("dispatch" + (selected ? "/" + selected : "")),
    retry: false, refetchInterval: 5000 });
  const data = view.data;
  const scope = data ? `interlock:dispatch-command:${data.tenant_id}:${data.principal}` : "";
  const stored = useSyncExternalStore(subscribe, () => scope ? localStorage.getItem(scope) : null, () => null);
  const pending: Pending | null = stored ? JSON.parse(stored) : null;
  function save(value: Pending | null) {
    if (!scope) throw new Error("Authenticated account required");
    if (value) localStorage.setItem(scope, JSON.stringify(value)); else localStorage.removeItem(scope);
    pendingRef.current = value; window.dispatchEvent(new Event("dispatch-pending"));
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBusy(true);
    const form = event.currentTarget;
    const values = new FormData(form);
    const username = String(values.get("username")); const password = String(values.get("password"));
    (form.elements.namedItem("password") as HTMLInputElement).value = "";
    try { await api("session", { username, password }); await view.refetch(); }
    catch (err) { setError(err instanceof Error ? err.message : "Sign in failed"); }
    finally { setBusy(false); }
  }
  async function disclose(row: Operation) {
    if (pending || pendingRef.current || busy) return;
    const command = { key: "disclose_" + crypto.randomUUID().replaceAll("-", ""), operation_id: row.operation.id, revision: row.runtime.revision };
    save(command); await execute(command);
  }
  async function execute(command: Pending) {
    setBusy(true); setError("");
    try {
      const result = await api<Instructions>("dispatch/disclose", { operation_id: command.operation_id },
        { "Idempotency-Key": command.key, "If-Match": String(command.revision) });
      setInstructions(result); save(null); await view.refetch();
    } catch (err) {
      if (err instanceof ApiError && err.status < 500) save(null);
      setError(err instanceof Error ? err.message : "Disclosure response unresolved");
    } finally { setBusy(false); }
  }
  async function recover() {
    if (!pending) return;
    setBusy(true); setError("");
    try { setInstructions(await api<Instructions>("dispatch/requests/disclose/" + pending.key)); save(null); await view.refetch(); }
    catch (err) { setError(err instanceof Error ? err.message : "Recovery unavailable"); }
    finally { setBusy(false); }
  }
  async function observe(row: Operation) {
    setBusy(true); setError("");
    try { await api("dispatch/observe", { operation_id: row.operation.id }); await view.refetch(); }
    catch (err) { setError(err instanceof Error ? err.message : "Observation unavailable"); }
    finally { setBusy(false); }
  }
  return <main className="authority-page dispatch-page">
    <header><Link href="/" className="brand"><span className="mark">↔</span> interlock</Link>
      <span className="boundary">PASS 2 / DISPATCH & EXPOSURE</span><span className="test">REFERENCE SYNTHETIC</span></header>
    <section className="intro"><p className="eyebrow">CURRENT AUTHORITY · DURABLE UNCERTAINTY</p><h1>Committed to handoff.<br /><span>Accountable for the outcome.</span></h1>
      <p>Inspect the exact operation, its dispatch cutoff, held capacity and the duty to resolve its external effect.</p></section>
    <nav className="authority-nav"><Link href="/authority">Authority & approvals</Link><Link href="/dispatch">All operations</Link><Link href="/">Purchase evidence</Link></nav>
    <aside className="notice"><strong>Queueing does not authorize dispatch.</strong> Each handoff needs a fresh current-authority commitment. UNKNOWN describes local knowledge. It never means zero effect.</aside>
    {error && <p role="alert" className="error">{error}</p>}
    {view.error instanceof ApiError && view.error.status === 403 && <p role="alert" className="error">Operation not visible in this account.</p>}
    {!data ? <section className="login card"><h2>Sign in to dispatch</h2><form onSubmit={login}>
      <label>Username<input name="username" defaultValue="owner-a" autoComplete="username" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      <button disabled={busy}>Sign in</button></form></section> : <>
      <section className="account"><span>{data.principal} · {data.tenant_id}</span><button className="secondary" disabled={busy} onClick={() => view.refetch()}>Refresh server state</button></section>
      <section className="notice" aria-label="Visible operation counts"><strong>{data.operations.length} visible operation(s) · {data.operations.filter(row => row.runtime.knowledge === "UNKNOWN").length} UNKNOWN</strong>
        <p>Oldest visible UNKNOWN: {Math.max(0, ...data.operations.filter(row => row.runtime.knowledge === "UNKNOWN").map(committedAge))}s · {data.operations.filter(row => row.exposures.length > 0).length} exposed path(s)</p></section>
      {pending && <aside className="notice"><strong>Disclosure response unresolved.</strong><code>{pending.key}</code>
        <button disabled={busy} onClick={recover}>Recover existing disclosure</button>
        <button className="secondary" disabled={busy} onClick={() => execute(pending)}>Retry exact disclosure command</button></aside>}
      {instructions && <section className="notice" role="status" data-testid="checkout-instructions"><strong>Exact exposed reference path</strong>
        <p>{instructions.mode} · {instructions.provider_order} · {money(instructions.amount)}</p><code>{instructions.exposure_id}</code></section>}
      {!data.operations.length && <section className="card">No operations visible. Admit exact purchase terms in <Link href="/authority">Authority & approvals</Link>.</section>}
      <div className="dispatch-operations">{data.operations.map(row => <article className="card dispatch-card" data-testid="operation-card" key={row.operation.id}>
        <div className="section-title"><h2>{row.runtime.knowledge}</h2><span className="badge">{row.operation.adapter}</span></div>
        <Link href={"/dispatch/" + row.operation.id}><code>{row.operation.id}</code></Link>
        <Link href={"/economic/" + row.operation.id}>Evidence, booking &amp; fulfillment</Link>
        <p>{row.runtime.state} · {row.age_seconds}s since admission · {row.runtime.attempt_count} committed attempt(s)</p>
        {row.runtime.knowledge === "UNKNOWN" && <aside className="notice"><strong>External effect unresolved.</strong> A handoff may have occurred. Capacity remains held while exact read evidence is sought.<p>{committedAge(row)}s since commitment awaiting exact evidence.</p></aside>}
        {row.grant_head.status !== "ACTIVE" && row.duties.some(d => d.runtime.state !== "DISCHARGED") && <p className="error">An unresolved duty remains. The initiating authority no longer permits another mutation.</p>}
        <div className="capacity-grid"><div><small>BOUND</small><strong>{money(row.grant_head.bound)}</strong></div><div><small>CONSUMED</small><strong>{money(row.grant_head.consumed)}</strong></div>
          <div><small>RESERVED</small><strong>{money(row.grant_head.reserved)}</strong></div><div><small>FREE</small><strong>{money(row.grant_head.free)}</strong></div></div>
        <p className="conservation">B = C + R + F · {row.grant_head.status} · epoch {row.grant_head.epoch}</p>
        <dl><dt>Exact requested amount</dt><dd>{money(row.operation.amount)} {row.operation.currency}</dd><dt>Reservation / exposure</dt><dd><code>{row.reservation.id}</code> · {row.exposure_state.state}</dd>
          <dt>Provider binding</dt><dd>{row.runtime.provider_order ?? (row.runtime.knowledge === "CONFIRMED_EFFECT" ? "Provider order verified; owner disclosure required" : "No exact provider order known")} · {row.operation.account_id} · {row.operation.integration}</dd>
          <dt>Replay classification</dt><dd>{row.replay_contract.classification}</dd><dt>Replay disabled reason</dt><dd>{row.replay_contract.verified ? "Explicit new cutoff required within the verified reference envelope" : "No verified replay envelope; observation only"}</dd>
          <dt>Blocked authority reason</dt><dd>{row.runtime.blocked_reason ?? "None recorded"}</dd><dt>Observation permission</dt><dd>{row.observation_permitted ? "Independently permitted" : "Observation permission required"}</dd></dl>
        <div className="actions"><button className="secondary" disabled={busy || !row.observation_permitted || !row.runtime.attempt_count} onClick={() => observe(row)}>Read exact provider evidence</button>
          {row.disclosure_permitted && <button disabled={busy || !!pending || (row.exposures.length === 0 && (row.runtime.knowledge !== "CONFIRMED_EFFECT" || row.grant_head.status !== "ACTIVE"))} onClick={() => disclose(row)}>
            {row.exposures.length ? "Retrieve existing Checkout path" : "Disclose exact Checkout path"}</button>}</div>
        <h3>Handoff history</h3>{!row.attempts.length && <p>No committed handoff. Queue possession gives no permission.</p>}
        {row.attempts.map(a => <section key={a.id}><p>Attempt {a.number} · {a.runtime.state} · {when(a.committed_at)}</p><code>{a.dispatch.id}</code>
          <p>{a.dispatch.authorization_reason} · service revision {a.dispatch.service_revision} · grant epoch {a.dispatch.grant_epoch}</p></section>)}
        <h3>Checkout exposures</h3>{row.exposures.map(e => <section key={e.id}><code>{e.id}</code><p>{e.provider_order} · first disclosed {when(e.first_disclosed_at)}</p>
          <p>EXPOSED_UNRESOLVED — closing the browser, quote expiry and revocation do not release this reservation.</p></section>)}
        <h3>Recovery duties</h3>{row.duties.map(d => <section key={d.id}><code>{d.id}</code><p>{d.origin_kind} · {d.predicate} · {d.runtime.state} · age {d.age_seconds}s</p>
          <p>{d.runtime.blocked_reason ?? "Duty grants no mutation permission"}</p>{d.runtime.discharge_observation_id && <code>Discharge evidence: {d.runtime.discharge_observation_id}</code>}</section>)}
        <h3>Latest observation</h3>{row.observations.length ? <p>{row.observations.at(-1)!.classification} · {row.observations.at(-1)!.source} · {when(row.observations.at(-1)!.observed_at)}</p> : <p>No outcome evidence yet.</p>}
        <details><summary>Exact immutable bindings and observation evidence</summary><pre>{JSON.stringify({ operation: row.operation, observations: row.observations }, null, 2)}</pre></details>
      </article>)}</div>
    </>}
  </main>;
}
