"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AgStudio, AgStudioProvider, createWidgets } from "ag-studio-react";
import { AgStudioAiModule, studioTheme } from "ag-studio";
import type { AgWidgetDefinition } from "ag-studio-react";
import type {
  AgBaseRegistry,
  AgDefaultWidgetDefinition,
  AgReportState,
  AgWidgetData,
  AgWidgetDataFormat,
  AgWidgetParams,
  AgDataSourcesDefinition,
  AgResponsiveLayoutBreakpoint,
  AgAiHarness,
  AgAiChatSession,
} from "ag-studio";
import type { Proof } from "./contracts";
import { evidenceDatasets } from "./evidence-datasets";
import styles from "./studio.module.css";
import {
  analystHarness,
  readReconciliation,
  type InvestigationResult,
} from "./studio-analyst";

interface ConsistencyData extends AgWidgetData<
  Record<string, never>,
  AgWidgetDataFormat<unknown>
> {
  type: "evidence-consistency";
}
type ConsistencyDefinition = AgWidgetDefinition<
  "evidence-consistency",
  ConsistencyData
>;
export interface EvidenceRegistry extends AgBaseRegistry {
  widgets: readonly (AgDefaultWidgetDefinition | ConsistencyDefinition)[];
}

// Reads Studio's filtered dataset. It has no network client or financial tools.
function Consistency({ widgetApi }: AgWidgetParams<ConsistencyData>) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    const fields = widgetApi
      .getSchema()
      .find((t) => t.id === "realities")?.fields;
    if (!fields?.length) {
      widgetApi.setDisplayState("noData");
      return;
    }
    widgetApi.setDisplayState("loading");
    const display = (data: Record<string, unknown>[]) => {
      if (!active) return;
      setRows(
        data.map((row) =>
          Object.fromEntries(
            fields.map((f) => [f.fieldId.split(".").at(-1), row[f.key]]),
          ),
        ),
      );
      widgetApi.setDisplayState(data.length ? "displayed" : "noData");
    };
    widgetApi
      .getData({ fields }, { onDataUpdated: (r) => display(r.results.rows) })
      .then((r) => {
        if (!r.replaced) display(r.results.rows);
      })
      .catch(() => {
        if (active) {
          setFailed(true);
          widgetApi.setDisplayState("displayed");
        }
      });
    return () => {
      active = false;
    };
  }, [widgetApi]);
  return (
    <div
      className={styles.consistency}
      aria-label="Evidence consistency widget"
    >
      {failed ? (
        <p role="alert">
          Filtered evidence unavailable. No financial conclusion can be drawn.
        </p>
      ) : (
        rows.map((r) => (
          <a
            key={String(r.reality)}
            href="#source-records"
            onClick={() => {
              const detail = document
                .getElementById("source-records")
                ?.closest("details");
              if (detail) detail.open = true;
            }}
          >
            <span>{String(r.reality)}</span>
            <strong>{String(r.status)}</strong>
            <small>{String(r.source_id)}</small>
          </a>
        ))
      )}
    </div>
  );
}

const definition: ConsistencyDefinition = {
  id: "evidence-consistency",
  label: "Four Realities / Evidence Consistency",
  comp: Consistency,
  form: ({ createDefaults }) => createDefaults({}),
  defaultSize: { width: 800, height: 220 },
  minSize: { width: 260, height: 180 },
};
export const widgets = createWidgets<EvidenceRegistry>({
  additionalTypes: [definition],
  menu: [
    {
      label: "Interlock evidence",
      widgetIds: [
        "evidence-consistency",
        "grid",
        "list-filter",
        "button-filter",
      ],
    },
  ],
});

export const emptySchemas: Record<
  string,
  Record<string, null>
> = Object.fromEntries(
  Object.entries({
    operationBinding: [
      "operation_id",
      "currency",
      "action_id",
      "approval_id",
      "reservation_id",
      "merchant_id",
      "amount_minor",
      "classification",
    ],
    providerObservations: [
      "operation_id",
      "currency",
      "receipt_id",
      "read_at",
      "order_id",
      "capture_id",
      "provider_status",
      "blocked",
      "capture_confirmed",
      "settlement",
    ],
    economicFacts: [
      "operation_id",
      "currency",
      "id",
      "kind",
      "effect_id",
      "amount_minor",
    ],
    journalLines: [
      "operation_id",
      "currency",
      "journal_id",
      "fact_id",
      "template",
      "line_id",
      "account",
      "side",
      "amount_minor",
    ],
    sellerEvidence: [
      "operation_id",
      "currency",
      "fact_id",
      "effect_id",
      "fulfillment_recorded",
      "blocked",
    ],
    realities: ["operation_id", "reality", "status", "source_id"],
  }).map(([id, fields]) => [
    id,
    Object.fromEntries(fields.map((field) => [field, null])),
  ]),
);

const grid = (fields: string[]) => ({
  type: "grid" as const,
  dataMapping: { cols: fields.map((id) => ({ id })) },
});
const responsiveLayout: AgResponsiveLayoutBreakpoint[] = [
  {
    maxWidth: 700,
    layout: (items, { columns }) => {
      let yTrack = 0;
      return items.map((_, index) => {
        const ySpan = index === 0 ? 14 : 9;
        const position = { xTrack: 0, yTrack, xSpan: columns, ySpan };
        yTrack += ySpan;
        return position;
      });
    },
  },
];
export const initialState: AgReportState<EvidenceRegistry> = {
  selectedPageId: "evidence",
  panels: {
    filters: { collapsed: true },
    data: { collapsed: true },
    compose: { collapsed: true },
  },
  pages: [
    {
      id: "evidence",
      widgets: {
        consistency: { type: "evidence-consistency", dataMapping: {} },
        facts: grid([
          "economicFacts.kind",
          "economicFacts.id",
          "economicFacts.amount_minor",
          "economicFacts.currency",
          "economicFacts.effect_id",
        ]),
        ledger: grid([
          "journalLines.template",
          "journalLines.account",
          "journalLines.side",
          "journalLines.amount_minor",
          "journalLines.currency",
          "journalLines.fact_id",
        ]),
        factFilter: {
          type: "list-filter",
          dataMapping: { value: [{ id: "economicFacts.kind" }] },
        },
      },
      widgetLayout: {
        consistency: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 7 },
        factFilter: { xTrack: 0, yTrack: 7, xSpan: 5, ySpan: 17 },
        facts: { xTrack: 5, yTrack: 7, xSpan: 19, ySpan: 8 },
        ledger: { xTrack: 5, yTrack: 15, xSpan: 19, ySpan: 9 },
      },
    },
    {
      id: "bindings",
      widgets: {
        authority: grid([
          "operationBinding.operation_id",
          "operationBinding.action_id",
          "operationBinding.approval_id",
          "operationBinding.merchant_id",
          "operationBinding.amount_minor",
          "operationBinding.currency",
        ]),
        provider: grid([
          "providerObservations.order_id",
          "providerObservations.capture_id",
          "providerObservations.provider_status",
          "providerObservations.read_at",
          "providerObservations.receipt_id",
        ]),
        seller: grid([
          "sellerEvidence.fact_id",
          "sellerEvidence.effect_id",
          "sellerEvidence.fulfillment_recorded",
        ]),
      },
      widgetLayout: {
        authority: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 8 },
        provider: { xTrack: 0, yTrack: 8, xSpan: 24, ySpan: 8 },
        seller: { xTrack: 0, yTrack: 16, xSpan: 24, ySpan: 8 },
      },
    },
  ],
};

export default function EvidenceStudio({
  proof,
  historical,
  licenseKey,
}: {
  proof: Proof;
  historical: boolean;
  licenseKey?: string;
}) {
  const ref = useRef<AgStudio<EvidenceRegistry>>(null);
  const [ready, setReady] = useState(false);
  const [editing, setEditing] = useState(false);
  const [focusFact, setFocusFact] = useState("");
  const [saved, setSaved] = useState<AgReportState<EvidenceRegistry>>();
  const [notice, setNotice] = useState("");
  const [harness, setHarness] = useState<AgAiHarness>();
  const session = useRef<AgAiChatSession | null>(null);
  const [question, setQuestion] = useState(
    "Is this purchase captured, booked and fulfilled? Check the recorded evidence and select the most useful report.",
  );
  const [investigating, setInvestigating] = useState(false);
  const [investigation, setInvestigation] = useState<InvestigationResult>();
  const [investigationError, setInvestigationError] = useState("");
  const [agentPage, setAgentPage] = useState<string | null>(null);
  const comparison = readReconciliation(
    investigation?.observations.reconcile_records,
  );
  const ai = useMemo(
    () =>
      analystHarness(proof.operation_id, {
        ready: (value) => {
          setHarness(() => value);
        },
        started: () => {
          setInvestigating(true);
          setInvestigationError("");
        },
        completed: (result, page) => {
          setInvestigation(result);
          setAgentPage(page);
          setInvestigating(false);
        },
        failed: (message) => {
          setInvestigationError(message);
          setInvestigating(false);
        },
      }),
    [proof.operation_id],
  );
  useEffect(() => () => session.current?.cancel(), []);
  const data = useMemo<AgDataSourcesDefinition<EvidenceRegistry>>(() => {
    const datasets = evidenceDatasets(proof);
    const realities = [
      {
        reality: "01 / Intended",
        status: "Exact admitted action",
        source_id: proof.action_id,
      },
      {
        reality: "02 / Observed",
        status: proof.blocked
          ? "Conflicting evidence — hold"
          : proof.capture_confirmed
            ? historical
              ? "Previously verified capture"
              : "Recorded capture verified"
            : "Capture not established",
        source_id: proof.receipt_id ?? "No canonical receipt",
      },
      {
        reality: "03 / Booked",
        status: `${proof.journals.length} distinct posting groups`,
        source_id: proof.journals.map((j) => j.id).join(", ") || "No journal",
      },
      {
        reality: "04 / Fulfilled",
        status:
          proof.seller_fulfilled && !proof.blocked
            ? "Seller attestation recorded"
            : "Fulfillment not established",
        source_id: datasets.sellerEvidence[0]?.fact_id ?? "No fulfillment fact",
      },
    ].map((r) => ({ ...r, operation_id: proof.operation_id }));
    return {
      sources: Object.entries({ ...datasets, realities }).map(([id, rows]) => ({
        id,
        data: rows,
        transactions: "none",
        // Empty evidence is an empty table with a stable schema, never a fake row.
        fields: Object.keys(rows[0] ?? emptySchemas[id]).map((key) => ({
          id: key,
          format: key === "amount_minor" ? "integerFormat" : "textFormat",
          editable: false,
        })),
      })),
      relationships: [
        {
          id: "realities-to-action",
          source: "realities.operation_id",
          target: "operationBinding.operation_id",
          type: "many-to-one",
        },
        {
          id: "facts-to-action",
          source: "economicFacts.operation_id",
          target: "operationBinding.operation_id",
          type: "many-to-one",
        },
        {
          id: "lines-to-fact",
          source: "journalLines.fact_id",
          target: "economicFacts.id",
          type: "many-to-one",
        },
        {
          id: "observations-to-action",
          source: "providerObservations.operation_id",
          target: "operationBinding.operation_id",
          type: "many-to-one",
        },
        {
          id: "seller-to-fact",
          source: "sellerEvidence.fact_id",
          target: "economicFacts.id",
          type: "many-to-one",
        },
      ],
    };
  }, [proof, historical]);
  return (
    <section
      className={styles.panel}
      aria-label="AG Studio financial investigation"
    >
      <div
        id="investigation-agent"
        className={styles.analyst}
        data-agent-page={agentPage ?? ""}
      >
        <div className={styles.analystHeading}>
          <div>
            <p>MODEL-SELECTED EVIDENCE TOOLS</p>
            <h2>Investigate the records</h2>
          </div>
          <a href="#studio-report">Open Studio report ↓</a>
        </div>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!harness || investigating || question.trim().length < 8) return;
            setInvestigating(true);
            setInvestigationError("");
            try {
              session.current = await harness.createThread({
                agentId: "interlock-investigator",
                title: "Payment investigation",
              });
              session.current.sendMessage(question.trim());
            } catch {
              setInvestigationError(
                "The Studio investigator is unavailable. No financial command was sent.",
              );
              setInvestigating(false);
            }
          }}
        >
          <label htmlFor="investigation-question">Investigation question</label>
          <textarea
            id="investigation-question"
            maxLength={1000}
            rows={2}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={investigating}
          />
          <div className={styles.askActions}>
            <button
              disabled={
                !ready ||
                !harness ||
                investigating ||
                question.trim().length < 8
              }
            >
              {investigating
                ? "Investigating recorded evidence…"
                : "Investigate payment"}
            </button>
            {investigating && (
              <button
                type="button"
                onClick={() => {
                  session.current?.cancel();
                  setInvestigating(false);
                }}
              >
                Stop investigation
              </button>
            )}
            <small>
              The model chooses scoped evidence tools. PayPal is not contacted.
            </small>
          </div>
        </form>
        {investigating && (
          <p role="status">
            Waiting for the model to select evidence tools and compare their
            results. Records remain unchanged.
          </p>
        )}
        {investigationError && (
          <p role="alert" className={styles.agentError}>
            {investigationError}
          </p>
        )}
        {investigation && (
          <div
            className={styles.agentResult}
            aria-label="Investigation findings"
          >
            <div className={styles.resultMeta}>
              <span>{investigation.model}</span>
              <span>
                {investigation.tool_execution_count} tools ·{" "}
                {(investigation.latency_ms / 1000).toFixed(1)}s
              </span>
              <span>Recorded sandbox evidence</span>
            </div>
            <p className={styles.interpretation}>AI interpretation</p>
            <p>{investigation.summary.split(/(?<=[.!?])\s/)[0]}</p>
            <details className={styles.fullInterpretation}>
              <summary>Full model interpretation</summary>
              <p>{investigation.summary}</p>
            </details>
            <div
              className={styles.toolStrip}
              aria-label="Completed model-selected tools"
            >
              {investigation.trace.map((step, index) => (
                <a
                  key={step.call_id}
                  href={`#trace-${step.source_id}`}
                  data-read-status={step.status}
                  onClick={() => {
                    const parent = document.getElementById(
                      "completed-tool-trace",
                    ) as HTMLDetailsElement | null;
                    const detail = document.getElementById(
                      `trace-${step.source_id}`,
                    ) as HTMLDetailsElement | null;
                    if (parent) parent.open = true;
                    if (detail) detail.open = true;
                  }}
                >
                  <span>
                    {String(index + 1).padStart(2, "0")} / {step.source_id}
                  </span>
                  <strong>
                    {(
                      {
                        inspect_authority: "Authority",
                        inspect_provider: "PayPal record",
                        inspect_economic_facts: "Economic facts",
                        inspect_ledger: "Journal entries",
                        inspect_fulfillment: "Fulfillment",
                        reconcile_records: "Reconciliation",
                        select_studio_view: "Studio report",
                        discover_transactions: "Discovery",
                      } as Record<string, string>
                    )[step.tool] ?? step.tool}
                  </strong>
                  <small>
                    {step.status === "OK" ? "Evidence returned" : "Unavailable"}{" "}
                    · {step.latency_ms}ms
                  </small>
                </a>
              ))}
            </div>
            <details className={styles.findingDetails}>
              <summary>
                Source-backed findings & citations (
                {investigation.findings.length})
              </summary>
              <ul>
                {investigation.findings.map((finding) => (
                  <li key={finding.id}>
                    {finding.text}{" "}
                    {finding.source_ids.map((id) => (
                      <a
                        key={id}
                        href={`#trace-${id}`}
                        onClick={() => {
                          const parent = document.getElementById(
                            "completed-tool-trace",
                          ) as HTMLDetailsElement | null;
                          const detail = document.getElementById(
                            `trace-${id}`,
                          ) as HTMLDetailsElement | null;
                          if (parent) parent.open = true;
                          if (detail) detail.open = true;
                        }}
                      >
                        [{id}]
                      </a>
                    ))}
                  </li>
                ))}
              </ul>
            </details>
            <p role="status">
              {agentPage
                ? `Agent selected the ${agentPage} report through the Studio SDK.`
                : "No report change was requested."}
            </p>
            {comparison && (
              <section
                className={styles.comparison}
                aria-label="Reconciliation findings"
              >
                <h3>Reconciliation findings</h3>
                <p>
                  Comparison of recorded sources. No reconciliation run was
                  created.
                </p>
                {comparison.gaps.length ? (
                  <ul>
                    {comparison.gaps.map((gap) => (
                      <li key={gap}>
                        {(
                          {
                            CAPTURE_NOT_VERIFIED: "Capture not verified",
                            NO_JOURNAL: "No journal recorded",
                            FULFILLMENT_NOT_RECORDED:
                              "Fulfillment not recorded",
                            CONFLICT_HOLD:
                              "Conflicting evidence; obligation remains held",
                            UNBALANCED_JOURNAL: "Journal lines do not balance",
                          } as Record<string, string>
                        )[gap] ?? gap}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.agreement}>
                    No gaps in these recorded capture, journal-balance and
                    fulfillment checks.
                  </p>
                )}
                {comparison.stored_reconciliation.available ? (
                  <>
                    <p>
                      {comparison.stored_reconciliation.findings?.length ?? 0}{" "}
                      visible stored findings in the bounded history inspected.
                    </p>
                    {comparison.stored_reconciliation.runs?.map((run) => (
                      <p key={run.id}>
                        Stored run: {run.state} ·{" "}
                        <time>{run.completed_at}</time>
                      </p>
                    ))}
                    {comparison.stored_reconciliation.findings?.map(
                      (finding) => (
                        <p key={finding.id}>
                          {finding.class} · {finding.state} ·{" "}
                          {finding.next_step}
                        </p>
                      ),
                    )}
                  </>
                ) : (
                  <p>
                    Stored reconciliation is unavailable to this session. An
                    empty visible result does not establish reconciliation.
                  </p>
                )}
              </section>
            )}
            <details id="completed-tool-trace" className={styles.trace}>
              <summary>
                Completed tool trace ({investigation.trace.length})
              </summary>
              {investigation.trace.map((step) => (
                <details id={`trace-${step.source_id}`} key={step.call_id}>
                  <summary>
                    <span>{step.tool}</span>
                    <span>
                      {step.status} · {step.latency_ms}ms · {step.source_id}
                    </span>
                  </summary>
                  <pre>
                    {JSON.stringify(
                      { arguments: step.arguments, response: step.response },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              ))}
            </details>
          </div>
        )}
      </div>
      <div id="studio-report" className={styles.toolbar}>
        <div>
          <p>AG STUDIO / SOURCE EXPLORER</p>
          <h2>Financial evidence report</h2>
        </div>
        <div>
          <button
            disabled={!ready}
            onClick={() => {
              if (ref.current) {
                setSaved(structuredClone(ref.current.api.getState()));
                setNotice(
                  "Report layout saved for this view only. Financial records are unchanged.",
                );
              }
            }}
          >
            Save layout
          </button>
          <button
            disabled={!saved || !ready}
            onClick={() => {
              if (saved) ref.current?.api.setState(structuredClone(saved));
              setNotice("Saved layout restored.");
            }}
          >
            Restore layout
          </button>
          <button
            disabled={!ready}
            onClick={() => {
              ref.current?.api.setState(structuredClone(initialState));
              setNotice("Evidence report reset.");
            }}
          >
            Reset report
          </button>
          <button disabled={!ready} onClick={() => setEditing(!editing)}>
            {editing ? "View report" : "Edit report"}
          </button>
        </div>
      </div>
      <p className={styles.scope}>
        {historical
          ? "HISTORICAL SNAPSHOT — latest refresh failed or is pending. "
          : "Authenticated sandbox snapshot. "}
        Report edits change presentation only. Amounts across posting templates
        must not be summed as spending.
      </p>
      <p role="status" className={styles.notice}>
        {notice}
      </p>
      <label className={styles.drill}>
        Drill through to an economic fact
        <select
          value={focusFact}
          onChange={(event) => setFocusFact(event.target.value)}
        >
          <option value="">All evidence in this operation</option>
          {proof.facts.map((fact) => (
            <option value={fact.id} key={fact.id}>
              {fact.kind} · {fact.id}
            </option>
          ))}
        </select>
      </label>
      <div className={styles.canvas}>
        <AgStudioProvider licenseKey={licenseKey} modules={[AgStudioAiModule]}>
          <AgStudio<EvidenceRegistry>
            ref={ref}
            ai={ai}
            theme={studioTheme.withParams({
              backgroundColor: "#10101b",
              fontFamily: "var(--font-body), Arial, sans-serif",
              fontSize: 11,
              studioWrapperBackgroundColor: "#10101b",
              studioCanvasBackgroundColor: "#10101b",
              studioWidgetBackgroundColor: "#11111f",
              foregroundColor: "#ede9f8",
              accentColor: "#aa83ff",
              browserColorScheme: "dark",
            })}
            data={data}
            widgets={widgets}
            initialState={initialState}
            mode={editing ? "edit" : "view"}
            style={{ height: "100%", width: "100%" }}
            layout={{ minWidth: 240, rowHeight: 28 }}
            responsiveLayoutBreakpoints={responsiveLayout}
            onApiReady={(event) => {
              // Studio lazily resolves `ai` when its own chat panel opens. Our
              // embedded console initializes the same cached harness from the
              // documented public API-ready event, without opening that panel.
              if (typeof ai === "function")
                ai({ api: event.api, context: undefined });
              setReady(true);
            }}
            drillThrough={
              focusFact
                ? {
                    filter: {
                      field: { id: "economicFacts.id" },
                      operator: "equals",
                      value: focusFact,
                    },
                  }
                : undefined
            }
          />
        </AgStudioProvider>
      </div>
      <p className={styles.scope}>
        Local AG Studio evaluation. Public deployment awaits licence permission.
        Layouts stay in memory and contain no session credentials.
      </p>
    </section>
  );
}
