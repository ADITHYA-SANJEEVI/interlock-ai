import { createAiHarness } from "ag-studio";
import type {
  AgAiHarnessSetup,
  AgAiHarness,
  AgAiEventStream,
  AgStudioApi,
} from "ag-studio";
import { readProof, type Proof } from "./contracts";

export type Trace = {
  tool: string;
  source_id: string;
  call_id: string;
  arguments: Record<string, unknown>;
  status: string;
  latency_ms: number;
  response: Record<string, unknown>;
};
type Reconciliation = {
  gaps: string[];
  stored_reconciliation: {
    available: boolean;
    runs?: { id: string; state: string; completed_at: string }[];
    findings?: {
      id: string;
      class: string;
      state: string;
      next_step: string;
    }[];
  };
};
export function readReconciliation(value: unknown): Reconciliation | undefined {
  if (
    !value ||
    typeof value !== "object" ||
    !("gaps" in value) ||
    !("stored_reconciliation" in value)
  )
    return;
  const { gaps, stored_reconciliation: stored } = value;
  if (
    !Array.isArray(gaps) ||
    gaps.some((g) => typeof g !== "string") ||
    !stored ||
    typeof stored !== "object" ||
    !("available" in stored) ||
    typeof stored.available !== "boolean"
  )
    return;
  for (const key of ["runs", "findings"] as const) {
    const records = (stored as Record<string, unknown>)[key];
    if (
      key in stored &&
      (!Array.isArray(records) ||
        records.some(
          (r: unknown) =>
            !r ||
            typeof r !== "object" ||
            !("id" in r) ||
            !("state" in r) ||
            typeof r.id !== "string" ||
            typeof r.state !== "string",
        ))
    )
      return;
  }
  return value as Reconciliation;
}
export type InvestigationResult = {
  contract_version: "investigation-v1";
  operation_id: string;
  summary: string;
  run_id: string;
  actual_model_invocation: true;
  financial_authority: false;
  database_writes: false;
  provider_contacted: false;
  model: string;
  tool_execution_count: number;
  latency_ms: number;
  read_at: string;
  findings: { id: string; text: string; source_ids: string[] }[];
  trace: Trace[];
  observations: Record<string, unknown>;
  recorded_evidence: Proof;
  view_request: null | {
    operation_id: string;
    page_id: "evidence" | "bindings";
    requested_by_model: true;
    execution: string;
  };
};
export function readInvestigation(
  value: InvestigationResult,
  operation: string,
) {
  const proof = readProof(value.recorded_evidence);
  const sources = new Set(value.trace?.map((t) => t.source_id));
  const allowed = new Set([
    "discover_transactions",
    "inspect_authority",
    "inspect_provider",
    "inspect_economic_facts",
    "inspect_ledger",
    "inspect_fulfillment",
    "reconcile_records",
    "select_studio_view",
  ]);
  if (
    value.contract_version !== "investigation-v1" ||
    value.operation_id !== operation ||
    proof.operation_id !== operation ||
    value.actual_model_invocation !== true ||
    value.financial_authority !== false ||
    value.database_writes !== false ||
    value.provider_contacted !== false ||
    typeof value.summary !== "string" ||
    !value.summary.trim() ||
    value.summary.length > 3000 ||
    !Array.isArray(value.trace) ||
    value.tool_execution_count !== value.trace.length ||
    value.trace.length < 2 ||
    value.trace.length > 12 ||
    sources.size !== value.trace.length ||
    value.trace.some(
      (t) =>
        !allowed.has(t.tool) ||
        t.response.operation_id !== operation ||
        (t.arguments.operation_id !== undefined &&
          t.arguments.operation_id !== operation),
    ) ||
    !Array.isArray(value.findings) ||
    !value.findings.length ||
    value.findings.some(
      (f) =>
        !f.source_ids.length ||
        f.source_ids.some(
          (s) =>
            !sources.has(s) ||
            value.trace.find((t) => t.source_id === s)?.status !== "OK",
        ),
    ) ||
    (value.view_request &&
      (value.view_request.operation_id !== operation ||
        value.view_request.requested_by_model !== true ||
        !["evidence", "bindings"].includes(value.view_request.page_id)))
  )
    throw new Error("Investigation identity or evidence contract mismatch.");
  return value;
}

export function analystHarness(
  operation: string,
  callbacks: {
    ready(harness: AgAiHarness): void;
    started(): void;
    completed(result: InvestigationResult, page: string | null): void;
    failed(message: string): void;
  },
): AgAiHarnessSetup {
  let instance: AgAiHarness | undefined;
  return ({ api: core }) => {
    if (instance) return instance;
    // v3.1's setup type names the core API; its browser factory requires the
    // browser API. Verify those methods instead of assuming a different version.
    if (
      !("getState" in core) ||
      typeof core.getState !== "function" ||
      !("setState" in core) ||
      typeof core.setState !== "function"
    )
      throw new Error("Browser Studio state API required.");
    const api = core as unknown as AgStudioApi;
    const harness = createAiHarness(api, () => ({
      primary: "interlock-investigator",
      agents: [
        {
          id: "interlock-investigator",
          name: "Payment investigator",
          description:
            "Read authenticated payment records. No financial commands.",
          tools: () => [], // Server loop owns the strict evidence allowlist. No default Studio tools.
          run: async function* (input, ctx): AgAiEventStream {
            callbacks.started();
            yield {
              type: "RUN_STARTED",
              threadId: input.threadId,
              runId: input.runId,
            };
            try {
              const question = input.messages
                .filter((m) => m.role === "user")
                .at(-1)?.content;
              if (
                !question ||
                typeof question !== "string" ||
                question.length > 1000
              )
                throw new Error(
                  "Enter an investigation question of up to 1,000 characters.",
                );
              const response = await fetch("/api/investigations", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
                signal: ctx.signal,
                body: JSON.stringify({ operation_id: operation, question }),
              });
              const body = await response.json();
              if (!response.ok)
                throw new Error(
                  typeof body.detail === "string"
                    ? body.detail
                    : "Investigation unavailable. Records are unchanged.",
                );
              const result = readInvestigation(body, operation);
              if (ctx.signal?.aborted) return;
              let selected: string | null = null;
              if (result.view_request) {
                const state = api.getState();
                const page = result.view_request.page_id;
                if (!state.pages.some((p) => p.id === page))
                  throw new Error("Requested report is not available.");
                api.setState({ ...state, selectedPageId: page });
                selected = page;
              }
              // Replay completed, actual server trace into Studio's supported event protocol.
              // This is not a simulated live sequence or a chain-of-thought transcript.
              for (const step of result.trace) {
                yield {
                  type: "TOOL_CALL_START",
                  toolCallId: step.call_id,
                  toolCallName: step.tool,
                };
                yield {
                  type: "TOOL_CALL_ARGS",
                  toolCallId: step.call_id,
                  delta: JSON.stringify(step.arguments),
                };
                yield { type: "TOOL_CALL_END", toolCallId: step.call_id };
                yield {
                  type: "TOOL_CALL_RESULT",
                  toolCallId: step.call_id,
                  messageId: crypto.randomUUID(),
                  content: JSON.stringify({
                    success: step.status === "OK",
                    data: step.response,
                  }),
                };
              }
              callbacks.completed(result, selected);
              yield {
                type: "TEXT_MESSAGE_CHUNK",
                messageId: crypto.randomUUID(),
                role: "assistant",
                delta: result.summary,
              };
              yield {
                type: "RUN_FINISHED",
                threadId: input.threadId,
                runId: input.runId,
              };
            } catch (failure) {
              if (ctx.signal?.aborted) {
                callbacks.failed(
                  "Investigation stopped. No financial command was sent.",
                );
                return;
              }
              const message =
                failure instanceof Error
                  ? failure.message
                  : "Investigation unavailable. Records are unchanged.";
              callbacks.failed(message);
              yield {
                type: "RUN_ERROR",
                message,
                code: "READ_ONLY_INVESTIGATION_FAILED",
              };
            }
          },
        },
      ],
    }));
    instance = harness;
    callbacks.ready(harness);
    return harness;
  };
}
