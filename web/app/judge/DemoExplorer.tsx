"use client";
import { useEffect, useState } from "react";
import Frame from "../paypal/Frame";
import StudioSlot from "./StudioSlot";
import FourRealities, { RealitySource } from "./FourRealities";
import InvestigationWorkbench from "./InvestigationWorkbench";
import { questionId, type QuestionId } from "./investigation";
import {
  caseIds,
  download,
  loadEvidence,
  money,
  observed,
  reportHtml,
  type PublicCase,
  type PublicEvidence,
} from "./public-evidence";
import styles from "./judge.module.css";
const tabs = [
  "Overview",
  "Source detail",
  "Evidence",
  "Timeline",
  "Agent traces",
  "Reports",
];
const names = ["Completed purchase", "Approved · capture unverified"];
function Sources({
  record: c,
  initialFact = "",
}: {
  record: PublicCase;
  initialFact?: string;
}) {
  const [fact, setFact] = useState(
    c.economic.facts.some((f) => f.id === initialFact) ? initialFact : "",
  );
  const [account, setAccount] = useState("");
  const facts = c.economic.facts.filter((f) => !fact || f.id === fact);
  const journals = c.economic.journals.filter(
    (j) =>
      (!fact || j.fact_id === fact) &&
      (!account || j.lines.some((l) => l.account === account)),
  );
  return (
    <section id="source-records" aria-label="Source relationships">
      <div className={styles.sectionHeading}>
        <div>
          <p className={styles.eyebrow}>LINKED SOURCE RECORDS</p>
          <h2>Follow the economic evidence</h2>
        </div>
        <button
          onClick={() => {
            setFact("");
            setAccount("");
          }}
        >
          Clear filters
        </button>
      </div>
      <div className={styles.filters}>
        <label>
          Economic fact
          <select
            aria-label="Economic fact"
            value={fact}
            onChange={(e) => setFact(e.target.value)}
          >
            <option value="">All recorded facts</option>
            {c.economic.facts.map((f) => (
              <option key={f.id} value={f.id}>
                {f.kind} · {f.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Journal account
          <select
            aria-label="Journal account"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          >
            <option value="">All accounts</option>
            {Array.from(
              new Set(
                c.economic.journals.flatMap((j) =>
                  j.lines.map((l) => l.account),
                ),
              ),
            ).map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
      </div>
      <p>
        Fact selection filters the related journals through their original fact
        IDs. Amounts across posting templates are separate economic effects, not
        additional spending.
      </p>
      <div className={styles.tableWrap}>
        <table>
          <caption>Economic facts ({facts.length})</caption>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Amount</th>
              <th>Fact → related journals</th>
              <th>Provider effect</th>
            </tr>
          </thead>
          <tbody>
            {facts.map((f) => (
              <tr key={f.id}>
                <td>{f.kind}</td>
                <td>{money(f.amount_minor)}</td>
                <td>
                  <button
                    className={styles.sourceButton}
                    onClick={() => setFact(f.id)}
                  >
                    {f.id}
                  </button>
                </td>
                <td>
                  <code>{f.effect_id}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!facts.length && (
        <p className={styles.warning}>
          No authenticated economic fact is recorded for this operation. Buyer
          approval is not a capture fact.
        </p>
      )}
      <div className={styles.tableWrap}>
        <table>
          <caption>Related journal groups ({journals.length})</caption>
          <thead>
            <tr>
              <th>Posting template</th>
              <th>Account</th>
              <th>Side</th>
              <th>Amount</th>
              <th>Original fact reference</th>
            </tr>
          </thead>
          <tbody>
            {journals.flatMap((j) =>
              j.lines.map((l, i) => (
                <tr key={j.id + i}>
                  <td>{j.template}</td>
                  <td>{l.account}</td>
                  <td>{l.side}</td>
                  <td>{money(l.amount_minor)}</td>
                  <td>
                    <code>{j.fact_id}</code>
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      {!journals.length && (
        <p>No journal matches this recorded case and filter.</p>
      )}
      <details>
        <summary>Original commercial terms and authority references</summary>
        <pre>
          {JSON.stringify(
            { commercial: c.commercial, authority: c.authority },
            null,
            2,
          )}
        </pre>
      </details>
      <details>
        <summary>Provider observation and source integrity</summary>
        <pre>
          {JSON.stringify(
            {
              provider: c.provider,
              integrity: c.integrity,
              source_package_sha256: c.source_package_sha256,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}
function TraceView({
  evidence,
  focusTool = "",
}: {
  evidence: PublicEvidence;
  focusTool?: string;
}) {
  const [kind, setKind] = useState(focusTool ? "investigation" : "planning");
  const trace =
    kind === "planning" ? evidence.planning : evidence.investigation;
  return (
    <section aria-label="Recorded genuine agent executions">
      <p className={styles.eyebrow}>PREVIOUSLY EXECUTED MODEL CALLS</p>
      <h2>Inspect how the agents used their tools</h2>
      <p className={styles.warning}>
        Recorded genuine execution. Selecting a trace does not invoke a model,
        create a purchase or replay a provider request. The planning run did not
        create either historical order.
      </p>
      <label>
        Recorded agent
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="planning">Purchase planner · 4 tools</option>
          <option value="investigation">Payment investigator · 7 tools</option>
        </select>
      </label>
      <div className={styles.traceMeta}>
        <span>{trace.model}</span>
        <span>{(trace.latency_ms / 1000).toFixed(2)}s recorded latency</span>
        <time>{observed(trace.read_at)}</time>
      </div>
      {kind === "planning" && (
        <p>
          <strong>
            Typed source terms: {money(evidence.planning.proposal.amount_minor)}{" "}
            ({evidence.planning.proposal.amount_minor} USD minor units)
          </strong>{" "}
          · {evidence.planning.proposal.quantity}{" "}
          {evidence.planning.proposal.product_id} for{" "}
          {evidence.planning.proposal.beneficiary}. These historical terms do
          not authorize spending.
        </p>
      )}
      <details>
        <summary>Original model interpretation (non-authoritative)</summary>
        <p>
          Model prose can contain imprecise monetary wording. Typed source
          amounts and deterministic checks govern financial terms; the original
          recorded response is retained unchanged below.
        </p>
        <p>{trace.summary}</p>
      </details>
      {kind === "planning" && (
        <p>
          <strong>Execution blocked:</strong> no usable requester grant.
          Historical source terms require a fresh signed quote and independent
          authorization.
        </p>
      )}
      {trace.trace.map((t, i) => (
        <details
          key={t.source_id}
          className={styles.trace}
          open={t.tool === focusTool || undefined}
        >
          <summary>
            <span>
              {String(i + 1).padStart(2, "0")} · {t.tool}
            </span>
            <small>
              {t.status} · {t.source_id}
            </small>
          </summary>
          <pre>
            {JSON.stringify(
              { arguments: t.arguments, response: t.response },
              null,
              2,
            )}
          </pre>
        </details>
      ))}
      <a href="/evidence/v1/bundle.json" download>
        Download versioned evidence and recorded traces
      </a>
    </section>
  );
}
export default function DemoExplorer({ licenseKey }: { licenseKey?: string }) {
  const [evidence, setEvidence] = useState<PublicEvidence>();
  const [error, setError] = useState("");
  const [id, setId] = useState<string>(caseIds[0]);
  const [tab, setTab] = useState("Overview");
  const [question, setQuestion] = useState<QuestionId>("capture");
  const [fact, setFact] = useState("");
  const [tool, setTool] = useState("");
  const [guided, setGuided] = useState(false);
  useEffect(() => {
    const update = () => {
      const query = new URLSearchParams(window.location.search);
      setId(query.get("case") ?? caseIds[0]);
      setQuestion(questionId(query.get("question")));
      setGuided(query.get("journey") === "1");
      setFact(query.get("fact") ?? "");
      setTool(query.get("tool") ?? "");
      const selected = query.get("view");
      setTab(tabs.includes(selected ?? "") ? selected! : "Overview");
    };
    update();
    window.addEventListener("popstate", update);
    const controller = new AbortController();
    loadEvidence(controller.signal)
      .then(setEvidence)
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(
            e instanceof Error ? e.message : "Recorded sources unavailable.",
          );
      });
    return () => {
      controller.abort();
      window.removeEventListener("popstate", update);
    };
  }, []);
  function navigate(
    nextId: string,
    nextTab: string,
    nextQuestion: QuestionId = question,
    nextFact = "",
    nextTool = "",
    nextGuided = guided,
  ) {
    const url = new URL(window.location.href);
    url.searchParams.set("case", nextId);
    url.searchParams.set("view", nextTab);
    url.searchParams.set("question", nextQuestion);
    if (nextGuided && nextId === caseIds[1])
      url.searchParams.set("journey", "1");
    else url.searchParams.delete("journey");
    for (const [key, value] of [
      ["fact", nextFact],
      ["tool", nextTool],
    ]) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    window.history.pushState({}, "", url);
    setId(nextId);
    setTab(nextTab);
    setQuestion(nextQuestion);
    setFact(nextFact);
    setTool(nextTool);
    setGuided(nextGuided && nextId === caseIds[1]);
  }
  const c = evidence?.cases.find((c) => c.operation_id === id);
  return (
    <Frame>
      <main className={styles.main}>
        <div className={styles.pageHeading}>
          <div>
            <p className={styles.eyebrow}>
              EXPLORE DEMO / HISTORICAL SANDBOX EVIDENCE
            </p>
            <h1>Inspect the purchase. Follow the proof.</h1>
            <p>
              Two genuine sandbox operations, independent records and visible
              obligations. No sign-in, payment credentials or live provider
              connection.
            </p>
          </div>
          <a className={styles.outline} href="/documentation/">
            How to verify this demo ↗
          </a>
        </div>
        {error && (
          <p role="alert" className={styles.warning}>
            {error}
          </p>
        )}
        {!evidence && !error && (
          <p role="status">Verifying versioned source integrity…</p>
        )}
        {evidence && (
          <div className={styles.workspace}>
            <aside className={styles.sidebar} aria-label="Recorded operations">
              <p className={styles.eyebrow}>CERTIFIED CASES / USD</p>
              {evidence.cases.map((item, i) => (
                <button
                  key={item.operation_id}
                  className={id === item.operation_id ? styles.selected : ""}
                  onClick={() => navigate(item.operation_id, "Overview")}
                  aria-pressed={id === item.operation_id}
                >
                  <small>
                    {item.provider.capture_confirmed
                      ? "CAPTURE RECORDED"
                      : "OPEN OBLIGATION"}
                  </small>
                  <strong>{names[i]}</strong>
                  <span>
                    {money(item.commercial.amount_minor)} ·{" "}
                    {item.economic.facts.length} facts ·{" "}
                    {item.economic.journals.length} journals
                  </span>
                  <code>{item.operation_id}</code>
                </button>
              ))}
              <div className={styles.sidebarNote}>
                <p>READ-ONLY PUBLIC PROJECTION</p>
                <span>
                  Version {evidence.evidence_version}. Provider observations are
                  from November 10, 2026. No financial or live-model endpoint
                  exists in the standalone demo.
                </span>
              </div>
            </aside>
            <div className={styles.content}>
              {!c ? (
                <section role="alert">
                  <h2>Operation unavailable</h2>
                  <p>
                    This public projection contains only the two certified
                    cases. Unknown IDs do not load private data or trigger a
                    backend lookup.
                  </p>
                  <button onClick={() => navigate(caseIds[0], "Overview")}>
                    Open completed case
                  </button>
                </section>
              ) : (
                <>
                  <div className={styles.caseHeading}>
                    <div>
                      <p className={styles.eyebrow}>
                        {c.provider.capture_confirmed
                          ? "RECORDED COMPLETED PURCHASE"
                          : "RECORDED BUYER CONSENT / CAPTURE UNVERIFIED"}
                      </p>
                      <h2>
                        {c.commercial.lines[0].product_id} for{" "}
                        {c.commercial.lines[0].beneficiary}
                      </h2>
                      <code>{c.operation_id}</code>
                    </div>
                    <strong className={styles.amount}>
                      {money(c.commercial.amount_minor)}
                    </strong>
                  </div>
                  <p className={styles.retained}>
                    Retained obligation:{" "}
                    {money(c.authority.lifetime_budget.reserved)} capacity held.
                    Separate dispatch exposure remains unresolved.
                  </p>
                  <div className={styles.tabBar} aria-label="Case views">
                    {tabs.map((name) => (
                      <button
                        key={name}
                        aria-pressed={tab === name}
                        onClick={() => navigate(id, name)}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                  <div
                    className={styles.view}
                    key={`${id}:${tab}:${fact}:${tool}`}
                  >
                    {tab === "Overview" && (
                      <>
                        <FourRealities
                          record={c}
                          onInspect={(next) =>
                            navigate(id, "Source detail", next)
                          }
                        />
                        <InvestigationWorkbench
                          record={c}
                          guided={guided}
                          onStart={() =>
                            navigate(
                              caseIds[1],
                              "Overview",
                              "consent",
                              "",
                              "",
                              true,
                            )
                          }
                          evidence={evidence}
                          question={question}
                          onQuestion={(next) => navigate(id, "Overview", next)}
                          onNavigate={(view, nextFact, nextTool) =>
                            navigate(id, view, question, nextFact, nextTool)
                          }
                        />
                        <div className={styles.warning}>
                          <strong>
                            Outstanding obligation remains visible
                          </strong>
                          {c.operational_cautions.map((t) => (
                            <p key={t}>{t}</p>
                          ))}
                          <p>
                            {money(c.authority.lifetime_budget.reserved)}{" "}
                            lifetime capacity reserved ·{" "}
                            {money(c.authority.lifetime_budget.free)} free.
                            Dispatch:{" "}
                            {c.authority.reservation.dispatch_exposure_state}.
                            This explorer cannot release or renew authority.
                          </p>
                        </div>
                        <div className={styles.summaryGrid}>
                          <section>
                            <h3>Recorded PayPal outcome</h3>
                            <p>
                              <strong>
                                {c.provider.provider_order_status}
                              </strong>{" "}
                              ·{" "}
                              {c.provider.capture_confirmed
                                ? "Capture independently verified in the recorded run"
                                : "Buyer consent is not proof of capture"}
                            </p>
                            <p>
                              Order <code>{c.provider.provider_order_id}</code>
                            </p>
                            <p>
                              Capture{" "}
                              <code>
                                {c.provider.capture_id ?? "Not verified"}
                              </code>
                            </p>
                            <small>
                              Canonical observation:{" "}
                              {observed(c.provider.last_canonical_read_at)}
                            </small>
                          </section>
                          <section>
                            <h3>Stored reconciliation</h3>
                            <p>
                              {c.reconciliation.runs
                                .map((r) => r.state)
                                .join(", ") || "Unavailable"}{" "}
                              run · {c.reconciliation.findings.length} visible
                              findings
                            </p>
                            {c.reconciliation.findings.map((f) => (
                              <p key={f.id}>
                                <strong>
                                  {f.class} · {f.state}
                                </strong>
                                <br />
                                Next stored step: {f.next_step}
                              </p>
                            ))}
                            <p>
                              A completed run does not resolve a separate
                              exposure record or open finding.
                            </p>
                          </section>
                        </div>
                        <div className={styles.exports}>
                          <div>
                            <h3>Take the evidence with you</h3>
                            <p>
                              Allowlisted JSON and readable HTML. Original
                              identities and source dates retained; private
                              buyer and owner identities excluded.
                            </p>
                          </div>
                          <button
                            onClick={() =>
                              download(
                                `interlock-${c.operation_id}.json`,
                                JSON.stringify(c, null, 2),
                                "application/json",
                              )
                            }
                          >
                            Download JSON
                          </button>
                          <button
                            onClick={() =>
                              download(
                                `interlock-${c.operation_id}.html`,
                                reportHtml(c),
                                "text/html",
                              )
                            }
                          >
                            Download HTML
                          </button>
                        </div>
                      </>
                    )}
                    {tab === "Source detail" && (
                      <RealitySource
                        record={c}
                        question={question}
                        onReturn={() => navigate(id, "Overview", question)}
                      />
                    )}
                    {tab === "Evidence" && (
                      <Sources record={c} initialFact={fact} />
                    )}
                    {tab === "Timeline" && (
                      <section>
                        <p className={styles.eyebrow}>
                          SOURCE TIMESTAMPS / NOT INFERRED EVENTS
                        </p>
                        <h2>Recorded operation timeline</h2>
                        <ol className={styles.timeline}>
                          {c.timeline.map((t, i) => (
                            <li key={t.source_id + String(i)}>
                              <time>{observed(t.at)}</time>
                              <div>
                                <strong>{t.kind.replaceAll("_", " ")}</strong>
                                <p>{t.source_system}</p>
                                <code>{t.source_id}</code>
                              </div>
                            </li>
                          ))}
                        </ol>
                        <p>
                          Individual operator identities are omitted. Missing
                          fact, posting and fulfillment timestamps remain
                          unavailable. Observation time is not an inferred
                          settlement time.
                        </p>
                      </section>
                    )}
                    {tab === "Agent traces" && (
                      <TraceView evidence={evidence} focusTool={tool} />
                    )}
                    {tab === "Reports" && (
                      <>
                        <Sources record={c} initialFact={fact} />
                        <div className={styles.studio}>
                          <StudioSlot
                            record={c}
                            evidence={evidence}
                            licenseKey={licenseKey}
                          />
                        </div>
                      </>
                    )}
                  </div>
                  <div className={styles.provenance}>
                    <span>
                      Evidence {evidence.evidence_version} · source read{" "}
                      {observed(c.read_at)}
                    </span>
                    <a href="/evidence/v1/manifest.json" download>
                      SHA-256 manifest
                    </a>
                    <span>
                      Historical observation; no current provider confirmation
                      or bank-settlement claim.
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </Frame>
  );
}
