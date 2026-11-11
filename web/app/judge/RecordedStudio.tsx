"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { AgStudio, AgStudioProvider } from "ag-studio-react";
import { AgStudioAiModule, createAiHarness, studioTheme } from "ag-studio";
import type {
  AgAiHarness,
  AgAiChatSession,
  AgAiEventStream,
  AgDataSourcesDefinition,
  AgReportState,
  AgStudioApi,
} from "ag-studio";
import {
  widgets,
  emptySchemas,
  initialState,
  type EvidenceRegistry,
} from "../paypal/EvidenceStudio";
import { money, type PublicCase, type PublicEvidence } from "./public-evidence";
import styles from "../paypal/studio.module.css";
import { answerQuestion } from "./investigation";

// Compact native layout for the two genuine cases; no fabricated volume.
const recordedInitialState = structuredClone(initialState);
for (const page of recordedInitialState.pages) {
  if (page.id === "bindings")
    page.widgetLayout = {
      authority: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 5 },
      provider: { xTrack: 0, yTrack: 5, xSpan: 24, ySpan: 5 },
      seller: { xTrack: 0, yTrack: 10, xSpan: 24, ySpan: 5 },
    };
}
// Reuses the original official widget and report definitions. The only inputs
// are sanitized recorded cases; there is no fetch, model client or payment API.
export default function RecordedStudio({
  record,
  evidence,
  licenseKey,
}: {
  record: PublicCase;
  evidence: PublicEvidence;
  licenseKey?: string;
}) {
  const ref = useRef<AgStudio<EvidenceRegistry>>(null);
  const [ready, setReady] = useState(false),
    [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState(""),
    [page, setPage] = useState("evidence");
  const [saved, setSaved] = useState<AgReportState<EvidenceRegistry>>();
  const harness = useRef<AgAiHarness | null>(null);
  const threadRef = useRef<AgAiChatSession | null>(null);
  const [replayed, setReplayed] = useState(false);
  const [appliedTools, setAppliedTools] = useState<string[]>([]);
  const [replayError, setReplayError] = useState("");
  const data = useMemo<AgDataSourcesDefinition<EvidenceRegistry>>(() => {
    const rows = {
      operationBinding: evidence.cases.map((c) => ({
        operation_id: c.operation_id,
        currency: "USD",
        action_id: c.authority.action_id,
        approval_id: c.authority.approval_id,
        reservation_id: c.authority.reservation.id,
        merchant_id: c.commercial.merchant_id,
        amount_minor: c.commercial.amount_minor,
        classification: c.classification,
      })),
      providerObservations: evidence.cases.map((c) => ({
        operation_id: c.operation_id,
        currency: "USD",
        receipt_id: c.provider.receipt_id,
        read_at: c.provider.last_canonical_read_at,
        order_id: c.provider.provider_order_id,
        capture_id: c.provider.capture_id,
        provider_status: c.provider.provider_order_status,
        blocked: false,
        capture_confirmed: c.provider.capture_confirmed,
        settlement: c.provider.capture_confirmed
          ? "VERIFIED_HISTORICAL_CAPTURE"
          : "CAPTURE_UNVERIFIED",
      })),
      economicFacts: evidence.cases.flatMap((c) =>
        c.economic.facts.map((f) => ({ ...f, operation_id: c.operation_id })),
      ),
      journalLines: evidence.cases.flatMap((c) =>
        c.economic.journals.flatMap((j) =>
          j.lines.map((l, i) => ({
            ...l,
            operation_id: c.operation_id,
            journal_id: j.id,
            fact_id: j.fact_id,
            template: j.template,
            line_id: j.id + ":" + i,
          })),
        ),
      ),
      sellerEvidence: evidence.cases.flatMap((c) =>
        c.economic.facts
          .filter((f) => f.kind === "SELLER_FULFILLED")
          .map((f) => ({
            operation_id: c.operation_id,
            currency: "USD",
            fact_id: f.id,
            effect_id: f.effect_id,
            fulfillment_recorded: c.seller.fulfillment_recorded,
            blocked: false,
          })),
      ),
      realities: evidence.cases.flatMap((c) =>
        [
          {
            reality: "01 / Intended",
            status: "Exact admitted action",
            source_id: c.authority.action_id,
          },
          {
            reality: "02 / Observed",
            status: c.provider.capture_confirmed
              ? "Verified historical capture"
              : "Capture unverified",
            source_id: c.provider.receipt_id,
          },
          {
            reality: "03 / Booked",
            status: c.economic.journals.length + " recorded journals",
            source_id: c.economic.journals[0]?.id ?? "No journal",
          },
          {
            reality: "04 / Fulfilled",
            status: c.seller.fulfillment_recorded
              ? "Recorded fulfillment"
              : "Not recorded",
            source_id:
              c.economic.facts.find((f) => f.kind === "SELLER_FULFILLED")?.id ??
              "No fulfillment fact",
          },
        ].map((r) => ({ ...r, operation_id: c.operation_id })),
      ),
    };
    return {
      sources: Object.entries(rows).map(([id, items]) => ({
        id,
        data: items,
        transactions: "none",
        fields: Object.keys(items[0] ?? emptySchemas[id]).map((key) => ({
          id: key,
          format: key === "amount_minor" ? "integerFormat" : "textFormat",
          editable: false,
        })),
      })),
      relationships: [
        {
          id: "realities-action",
          source: "realities.operation_id",
          target: "operationBinding.operation_id",
          type: "many-to-one",
        },
        {
          id: "facts-action",
          source: "economicFacts.operation_id",
          target: "operationBinding.operation_id",
          type: "many-to-one",
        },
        {
          id: "journal-fact",
          source: "journalLines.fact_id",
          target: "economicFacts.id",
          type: "many-to-one",
        },
        {
          id: "provider-action",
          source: "providerObservations.operation_id",
          target: "operationBinding.operation_id",
          type: "many-to-one",
        },
        {
          id: "seller-fact",
          source: "sellerEvidence.fact_id",
          target: "economicFacts.id",
          type: "many-to-one",
        },
      ],
    };
  }, [evidence]);
  useEffect(() => () => threadRef.current?.cancel(), []);
  const replay = () => {
    if (
      !ready ||
      !harness.current ||
      record.operation_id !== evidence.investigation.operation_id
    )
      return;
    setReplayed(false);
    setAppliedTools([]);
    setReplayError("");
    void harness.current
      .createThread({
        agentId: "recorded-interlock-investigator",
        title: "Recorded genuine investigation",
      })
      .then((thread) => {
        threadRef.current = thread;
        thread.sendMessage(
          "Apply the recorded report selection; no fresh inference.",
        );
      })
      .catch(() =>
        setReplayError(
          "Recorded Studio playback unavailable. No source or financial record changed.",
        ),
      );
  };
  return (
    <section
      className={styles.panel}
      aria-label="Official recorded AG Studio evaluation"
    >
      <div className={styles.toolbar}>
        <div>
          <p>OFFICIAL AG STUDIO 3.1.0 / LOCAL EVALUATION</p>
          <h2>Financial evidence report</h2>
        </div>
        <div>
          <button
            disabled={!ready}
            onClick={() => {
              if (ref.current) {
                setSaved(structuredClone(ref.current.api.getState()));
                setNotice(
                  "Recorded report layout saved in memory. Source records unchanged.",
                );
              }
            }}
          >
            Save layout
          </button>
          <button
            disabled={!ready || !saved}
            onClick={() => {
              if (saved) {
                ref.current?.api.setState(structuredClone(saved));
                setPage(saved.selectedPageId ?? "evidence");
                setNotice("Saved recorded report restored.");
              }
            }}
          >
            Restore layout
          </button>
          <button
            disabled={!ready}
            onClick={() => {
              ref.current?.api.setState(structuredClone(recordedInitialState));
              setPage("evidence");
              setNotice("Original report restored.");
            }}
          >
            Reset report
          </button>
          <button disabled={!ready} onClick={() => setEditing(!editing)}>
            {editing ? "View report" : "Edit report"}
          </button>
          {editing && (
            <button
              onClick={() => {
                if (!ref.current) return;
                const state = structuredClone(ref.current.api.getState());
                const selected = state.pages.find(
                  (p) => p.id === state.selectedPageId,
                );
                if (!selected?.widgets) return;
                selected.widgetLayout = Object.fromEntries(
                  Object.keys(selected.widgets).map((id, index) => [
                    id,
                    { xTrack: 0, yTrack: index * 8, xSpan: 24, ySpan: 8 },
                  ]),
                );
                ref.current.api.setState(state);
                setNotice(
                  "Widgets arranged vertically through the Studio state API. Sources unchanged.",
                );
              }}
            >
              Arrange vertically
            </button>
          )}
        </div>
      </div>
      <p className={styles.scope}>
        HISTORICAL SANDBOX EVIDENCE · source version {evidence.evidence_version}
        . Amounts are USD minor units. Report edits never change financial
        records. Separate exposure remains{" "}
        {record.authority.reservation.dispatch_exposure_state};{" "}
        {money(record.authority.lifetime_budget.reserved)} capacity retained.
      </p>
      <div className={styles.recordedContext}>
        <strong>{answerQuestion(record, "differences").title}</strong>
        <p>
          Follow the source question into this actual Studio report. Native
          filters and report edits affect presentation only. The incomplete case
          has a genuine recorded seven-tool investigation; replaying it selects
          the model-requested bindings page through the official Agent
          Framework.
        </p>
      </div>
      <label className={styles.drill}>
        Report page
        <select
          value={page}
          disabled={!ready}
          onChange={(e) => {
            setPage(e.target.value);
            if (ref.current) {
              const state = ref.current.api.getState();
              ref.current.api.setState({
                ...state,
                selectedPageId: e.target.value,
              });
            }
            setNotice("Studio page selected: " + e.target.value);
          }}
        >
          <option value="evidence">Four Realities and linked journals</option>
          <option value="bindings">
            Authority, PayPal and seller bindings
          </option>
        </select>
      </label>
      <p role="status" className={styles.notice}>
        {notice}
      </p>
      <div
        className={styles.canvas}
        style={{ height: page === "bindings" ? 480 : 680 }}
        data-recorded-studio-ready={ready}
        data-report-page={page}
        data-editing={editing}
      >
        <AgStudioProvider licenseKey={licenseKey} modules={[AgStudioAiModule]}>
          <AgStudio<EvidenceRegistry>
            ref={ref}
            data={data}
            widgets={widgets}
            initialState={recordedInitialState}
            mode={editing ? "edit" : "view"}
            style={{ height: "100%", width: "100%" }}
            layout={{ minWidth: 240, rowHeight: 28 }}
            theme={studioTheme.withParams({
              backgroundColor: "#10101b",
              fontFamily: "var(--font-body),Arial,sans-serif",
              fontSize: 11,
              foregroundColor: "#ede9f8",
              accentColor: "#aa83ff",
              browserColorScheme: "dark",
            })}
            drillThrough={{
              filter: {
                field: { id: "operationBinding.operation_id" },
                operator: "equals",
                value: record.operation_id,
              },
            }}
            onApiReady={(event) => {
              const api = event.api as AgStudioApi;
              harness.current = createAiHarness(api, () => ({
                primary: "recorded-interlock-investigator",
                agents: [
                  {
                    id: "recorded-interlock-investigator",
                    name: "Recorded payment investigation",
                    description:
                      "Replay a previously executed real model trace. No fresh model or financial call.",
                    tools: () => [],
                    run: async function* (input): AgAiEventStream {
                      yield {
                        type: "RUN_STARTED",
                        threadId: input.threadId,
                        runId: input.runId,
                      };
                      const trace = evidence.investigation;
                      for (const step of trace.trace) {
                        yield {
                          type: "TOOL_CALL_START",
                          toolCallId: step.source_id,
                          toolCallName: step.tool,
                        };
                        yield {
                          type: "TOOL_CALL_ARGS",
                          toolCallId: step.source_id,
                          delta: JSON.stringify(step.arguments),
                        };
                        yield {
                          type: "TOOL_CALL_END",
                          toolCallId: step.source_id,
                        };
                        yield {
                          type: "TOOL_CALL_RESULT",
                          toolCallId: step.source_id,
                          messageId: crypto.randomUUID(),
                          content: JSON.stringify({
                            recorded: true,
                            data: step.response,
                          }),
                        };
                      }
                      const selected = trace.view_request.page_id;
                      const state = api.getState();
                      if (!state.pages.some((p) => p.id === selected)) {
                        setReplayError(
                          "Recorded report page unavailable. Source records unchanged.",
                        );
                        yield {
                          type: "RUN_ERROR",
                          message: "Recorded report page unavailable",
                        };
                        return;
                      }
                      api.setState({ ...state, selectedPageId: selected });
                      setAppliedTools(trace.trace.map((step) => step.tool));
                      setPage(selected);
                      setReplayed(true);
                      setNotice(
                        "Recorded genuine seven-tool investigation applied the bindings page through the actual Studio Agent Framework. No new inference.",
                      );
                      yield {
                        type: "TEXT_MESSAGE_CHUNK",
                        messageId: crypto.randomUUID(),
                        role: "assistant",
                        delta: "RECORDED EXECUTION: " + trace.summary,
                      };
                      yield {
                        type: "RUN_FINISHED",
                        threadId: input.threadId,
                        runId: input.runId,
                      };
                    },
                  },
                ],
              }));
              setReady(true);
            }}
          />
        </AgStudioProvider>
      </div>
      <div className={styles.askActions}>
        <button
          disabled={
            !ready ||
            record.operation_id !== evidence.investigation.operation_id
          }
          onClick={replay}
        >
          Apply recorded investigation through Studio
        </button>
        <small>
          Previously executed genuine model choices, explicitly replayed. No
          live model endpoint. Available for the incomplete case only.
        </small>
      </div>
      {replayError && <p role="alert">{replayError}</p>}
      {appliedTools.length > 0 && (
        <details className={styles.recordedContext} open>
          <summary>
            Recorded tool choices applied ({appliedTools.length})
          </summary>
          <ol>
            {appliedTools.map((tool) => (
              <li key={tool}>{tool.replaceAll("_", " ")}</li>
            ))}
          </ol>
          <p>
            Previously executed tool results from the versioned bundle; no fresh
            inference or backend tool execution. The last recorded selection
            changes the actual Studio report above.
          </p>
        </details>
      )}
      {replayed && (
        <p role="status">
          Recorded investigation applied: seven real source tools, report page
          bindings.
        </p>
      )}
      <p className={styles.scope}>
        Evaluation-only key; public hosting requires the judging-period
        licensing arrangement. Saving and restoring changes presentation only
        and is scoped to this in-memory view.
      </p>
    </section>
  );
}
