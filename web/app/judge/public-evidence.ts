import type recorded from "../../public/evidence/v1/bundle.json";

export type PublicEvidence = typeof recorded;
export type PublicCase = PublicEvidence["cases"][number];
export const caseIds = [
  "op_3c78b42e074541dfac3a3ed8456fb59b",
  "op_ad9410eb8f604b28bc6c31f74836e2e9",
] as const;
export const money = (minor: number) => `USD ${(minor / 100).toFixed(2)}`;
export const observed = (at: string | null) =>
  at
    ? new Date(at).toLocaleString("en-GB", {
        timeZone: "Asia/Kolkata",
        timeZoneName: "short",
        hour12: false,
      })
    : "Timestamp not exposed by the source";
export async function loadEvidence(
  signal: AbortSignal,
): Promise<PublicEvidence> {
  const [manifestResponse, sourceResponse] = await Promise.all([
    fetch("/evidence/v1/manifest.json", { signal, cache: "no-store" }),
    fetch("/evidence/v1/bundle.json", { signal, cache: "no-store" }),
  ]);
  if (!manifestResponse.ok || !sourceResponse.ok)
    throw Error(
      "Historical evidence unavailable. No live source is substituted.",
    );
  const manifest = await manifestResponse.json();
  const bytes = await sourceResponse.arrayBuffer();
  const hex = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
  if (
    manifest.evidence_version !== "2026-11-11.v1" ||
    manifest.files?.length !== 1 ||
    manifest.files[0].path !== "bundle.json" ||
    manifest.files[0].bytes !== bytes.byteLength ||
    manifest.files[0].sha256 !== hex
  )
    throw Error(
      "Evidence integrity check failed. The recorded cases were not loaded.",
    );
  const data = JSON.parse(new TextDecoder().decode(bytes)) as PublicEvidence;
  if (
    data.contract_version !== "interlock-public-evidence-v1" ||
    data.classification !== "HISTORICAL_SANDBOX_EVIDENCE" ||
    data.evidence_version !== manifest.evidence_version ||
    data.cases.length !== 2 ||
    data.capabilities.financial_mutations !== false ||
    data.capabilities.live_model !== false ||
    data.cases.some(
      (c, i) =>
        c.operation_id !== caseIds[i] ||
        c.commercial.currency !== "USD" ||
        c.commercial.amount_minor !== 100 ||
        c.commercial.merchant_id !== "JB7TR6VHB4GTQ" ||
        c.financial_authority !== false ||
        c.current_provider_state_established !== false ||
        c.authority.reservation.currency !== "USD" ||
        c.authority.reservation.amount_minor !== 100,
    )
  )
    throw Error("Historical case scope is invalid.");
  return data;
}
const escape = (value: unknown) =>
  String(value ?? "Unavailable").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
export function reportHtml(c: PublicCase) {
  const rows = [
    [
      "Original purchase",
      `One workspace-seat for ${c.commercial.lines[0].beneficiary} · ${money(c.commercial.amount_minor)}`,
    ],
    [
      "Authorization recorded",
      `Action ${c.authority.action_state}; policy allowed when evaluated: ${c.authority.policy_allowed_when_evaluated}. Original authority is now expired.`,
    ],
    [
      "Recorded PayPal outcome",
      `${c.provider.provider_order_status}; capture ${c.provider.capture_id ?? "not verified"}. Observed ${observed(c.provider.last_canonical_read_at)}.`,
    ],
    [
      "Accounting",
      `${c.economic.facts.length} facts; ${c.economic.journals.length} journals. ${c.economic.journals_balanced === true ? "Recorded journals balance." : "No balanced posting established."}`,
    ],
    [
      "Seller fulfillment",
      c.seller.fulfillment_recorded
        ? "Recorded fulfillment fact"
        : "Not recorded",
    ],
    [
      "Reconciliation",
      `${c.reconciliation.runs.map((r) => r.state).join(", ") || "Unavailable"}; ${c.reconciliation.findings.length} visible findings.`,
    ],
    [
      "Unresolved findings",
      c.reconciliation.findings
        .map((f) => f.class + " / " + f.state + " / next: " + f.next_step)
        .join("; ") ||
        "None visible in bounded history; separate dispatch exposure remains.",
    ],
    [
      "Outstanding obligation",
      `${c.authority.reservation.dispatch_exposure_state}; ${money(c.authority.lifetime_budget.reserved)} lifetime capacity reserved; no release authorized.`,
    ],
  ];
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>Interlock · recorded sandbox case</title><style>body{font:16px/1.65 system-ui;max-width:960px;padding:32px;margin:auto;color:#162328}h1{font-size:30px}dt{font-weight:700;margin-top:16px}dd{margin-left:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.notice{background:#fff4d7;padding:16px}li{margin-bottom:12px}</style><h1>Interlock AI · purchase evidence</h1><p>HISTORICAL SANDBOX EVIDENCE · ${escape(c.evidence_version)}</p><p>${escape(c.operation_id)}</p><dl>${rows.map(([k, v]) => `<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join("")}</dl><div class="notice">${c.operational_cautions.map(escape).join("<br>")}<br>Not current provider confirmation, external certification or bank settlement.</div><h2>Source timeline</h2><ol>${c.timeline.map((t) => `<li><b>${escape(t.kind)}</b> — ${escape(observed(t.at))}<br>${escape(t.source_system)} · ${escape(t.source_id)}</li>`).join("")}</ol><h2>Exact terms, references and technical appendix</h2><p>Original source package: ${escape(c.source_package_sha256)}. Hashes identify content; they do not establish an externally trusted signature. Buyer identity, credentials and private owner identities are excluded.</p><pre>${escape(JSON.stringify(c, null, 2))}</pre></html>`;
}
export function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
