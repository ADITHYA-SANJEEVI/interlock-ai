"""Scoped case files and allowlisted exports, built from existing read interfaces.

No new SQL, database privilege, financial action, provider call or audit event.
"""

import html
import re
from datetime import UTC, datetime

from interlock.investigation_reads import InvestigationError, digest
from interlock.purchase_planner import at


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) else None


def sha(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None


def timestamp(value):
    parsed = at(value)
    return parsed.isoformat() if parsed else None


def state(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z_0-9]{0,99}", value) else "UNAVAILABLE"


def case_file(reads, token, opid):
    identity = reads.identity(token)
    if not set(identity["roles"]) & {"PURCHASE_REQUESTER", "PURCHASE_ISSUER", "PURCHASE_APPROVER"}:
        raise InvestigationError("OPERATIONS_READER_REQUIRED", 403)
    dispatch = reads.sql("dispatch", token, opid=opid)
    if (
        dispatch.get("tenant_id") != identity["tenant_id"]
        or len(dispatch["operations"]) != 1
        or dispatch["operations"][0]["operation"]["id"] != opid
    ):
        raise InvestigationError("CASE_SCOPE_DENIED", 403)
    # Establish exact operation visibility before reading provider projections.
    # Unknown/foreign IDs must be scope denials, not misleading source outages.
    proof = reads.proof(token, opid)
    item = dispatch["operations"][0]
    operation, reservation = item["operation"], item["reservation"]
    if (
        operation["tenant_id"] != identity["tenant_id"]
        or operation["currency"] != "USD"
        or operation["provider_mode"] != "PAYPAL_SANDBOX"
        or operation["amount"] != proof["amount_minor"]
        or operation["account_id"] != proof["merchant_id"]
        or operation["reservation_id"] != proof["reservation_id"]
        or operation["action_id"] != proof["action_id"]
        or operation.get("evidence_id") != proof["evidence_id"]
        or operation.get("decision_id") != proof["decision_id"]
        or operation.get("approval_id") != proof["approval_id"]
        or reservation["id"] != proof["reservation_id"]
        or reservation["amount"] != proof["amount_minor"]
        or reservation["currency"] != "USD"
    ):
        raise InvestigationError("CASE_FINANCIAL_BINDING_MISMATCH", 409)
    snapshot = reads.get(token, "authority")
    if snapshot["tenant_id"] != identity["tenant_id"] or snapshot["principal"] != identity["principal"]:
        raise InvestigationError("CASE_SCOPE_CHANGED", 403)
    action = reads.get(token, "actions/" + proof["action_id"])
    if (
        action["id"] != proof["action_id"]
        or action["amount"] != proof["amount_minor"]
        or action["currency"] != "USD"
        or action.get("evidence", {}).get("id") != proof["evidence_id"]
        or action.get("decision", {}).get("id") != proof["decision_id"]
    ):
        raise InvestigationError("CASE_ACTION_MISMATCH", 409)
    q = action.get("terms", {}).get("quote", {})
    if (
        q.get("merchant_id") != proof["merchant_id"]
        or q.get("amount") != proof["amount_minor"]
        or q.get("currency") != "USD"
        or q.get("tenant_id") != identity["tenant_id"]
    ):
        raise InvestigationError("CASE_COMMERCIAL_BINDING_MISMATCH", 409)
    lines = [
        {
            "product_id": identifier(line.get("sku")),
            "beneficiary": identifier(line.get("beneficiary")),
            "unit_price_minor": line["unit_price"],
        }
        for line in q.get("lines", [])
        if identifier(line.get("sku"))
        and identifier(line.get("beneficiary"))
        and type(line.get("unit_price")) is int
    ]
    if (
        len(lines) != len(q.get("lines", []))
        or not lines
        or sum(line["unit_price_minor"] for line in lines) != proof["amount_minor"]
    ):
        raise InvestigationError("CASE_COMMERCIAL_LINE_MISMATCH", 409)
    comparison = reads.reconciliation(token, opid, proof)
    stored = comparison["stored_reconciliation"]
    findings = [
        {
            "id": identifier(f.get("id")),
            "class": state(f.get("class")),
            "state": state(f.get("state")),
            "next_step": state(f.get("next_step")),
            "last_seen_at": timestamp(f.get("last_seen_at")),
        }
        for f in stored.get("findings", [])
    ]
    runs = [
        {
            "id": identifier(r.get("id")),
            "state": state(r.get("state")),
            "completed_at": timestamp(r.get("completed_at")),
        }
        for r in stored.get("runs", [])
    ]
    approvals = [a for a in snapshot.get("approvals", []) if a["id"] == proof["approval_id"]]
    approval = approvals[0] if len(approvals) == 1 else None
    grants = [g for g in snapshot.get("grants", []) if g["id"] == operation["grant_id"]]
    grant = grants[0] if len(grants) == 1 else None
    head = item["grant_head"]
    reservation_rows = [r for r in snapshot.get("reservations", []) if r["id"] == reservation["id"]]
    exposure = item.get("exposure_state") or {}
    timeline = []

    def event(kind, when, source_id, subsystem, actor=None, note=None):
        timeline.append(
            {
                "kind": kind,
                "at": timestamp(when),
                "source_id": identifier(source_id),
                "source_system": subsystem,
                "actor": identifier(actor),
                "actor_evidence": "AUTHENTICATED_SOURCE_FIELD" if identifier(actor) else "NOT_EXPOSED",
                "note": note,
            }
        )

    event(
        "ACTION_RECORDED", action.get("created_at"), action["id"], "authority action", action.get("requester")
    )
    event(
        "POLICY_DECISION_RECORDED",
        action.get("decision", {}).get("created_at"),
        proof["decision_id"],
        "deterministic policy",
    )
    if approval:
        event(
            "INDEPENDENT_APPROVAL_RECORDED",
            approval.get("created_at"),
            approval["id"],
            "authority approval",
            approval.get("approver"),
        )
    event(
        "CAPACITY_RESERVED",
        reservation.get("created_at"),
        reservation["id"],
        "atomic admission",
        note="Actor is not exposed by this reservation projection.",
    )
    event(
        "OPERATION_ADMITTED",
        operation.get("created_at"),
        opid,
        "protected dispatch",
        note="Bound requester: "
        + (identifier(operation.get("requester")) or "unavailable")
        + "; not a claim about the executor.",
    )
    if proof["last_canonical_read_at"]:
        event(
            "PROVIDER_OBSERVATION_RECORDED",
            proof["last_canonical_read_at"],
            proof["receipt_id"],
            "authenticated provider observation",
            note="Observation timestamp, not an inferred payment-event timestamp. No new PayPal read.",
        )
    for fact in proof["facts"]:
        event(
            "ECONOMIC_FACT_RECORDED",
            None,
            fact["id"],
            "authenticated economic facts",
            note="Creation time is not exposed by this source API.",
        )
    for journal in proof["journals"]:
        event(
            "JOURNAL_RECORDED",
            None,
            journal["id"],
            "protected posting records",
            note="Posting time is not exposed by this source API.",
        )
    if proof["seller_fulfilled"]:
        event(
            "FULFILLMENT_RECORDED",
            None,
            next((f["id"] for f in proof["facts"] if f["kind"] == "SELLER_FULFILLED"), None),
            "recorded seller attestation",
            note="Attestation time and individual actor are not exposed.",
        )
    for run in runs:
        event(
            "RECONCILIATION_RUN_RECORDED",
            run["completed_at"],
            run["id"],
            "stored reconciliation",
            note="Run completion alone does not resolve open findings.",
        )
    for finding in findings:
        event(
            "RECONCILIATION_FINDING_LAST_SEEN",
            finding["last_seen_at"],
            finding["id"],
            "stored reconciliation",
        )
    timeline.sort(key=lambda row: (row["at"] is None, row["at"] or "", row["kind"]))
    open_findings = [f for f in findings if f["state"] == "OPEN"]
    completed = bool(
        proof["capture_confirmed"] and not comparison["gaps"] and comparison["journals_balanced"] is True
    )
    cancelled = proof.get("settlement") == "CANCELLED_NEVER_SENT" and proof.get("cancellation") is not None
    status = (
        "CANCELLED_BEFORE_PROVIDER_REPORTED"
        if cancelled
        else "CONFLICT_HOLD"
        if proof["blocked"]
        else "RECORDED_COMPLETED_RECONCILED"
        if completed
        and stored.get("available")
        and any(r["state"] == "COMPLETE" for r in runs)
        and not open_findings
        else "RECORDED_CAPTURE_RECONCILIATION_REVIEW"
        if completed
        else "RECORDED_CAPTURE_WITH_ECONOMIC_GAPS"
        if proof["capture_confirmed"]
        else "UNRESOLVED_RECORDED_OUTCOME"
        if proof["capture_committed"] or open_findings
        else "CAPTURE_NOT_VERIFIED"
    )
    next_action = (
        "REVIEW_RECORDED_CANCELLATION_NO_NEW_PAYMENT"
        if cancelled
        else "REVIEW_CONFLICT_PRESERVE_OBLIGATIONS"
        if proof["blocked"]
        else "REVIEW_OPEN_FINDING_PRESERVE_OBLIGATIONS"
        if open_findings
        else "REVIEW_RECORDED_ECONOMIC_GAPS_NO_FINANCIAL_MUTATION"
        if proof["capture_confirmed"] and not completed
        else "REVIEW_RETAINED_DISPATCH_EXPOSURE_NO_FINANCIAL_MUTATION"
        if exposure.get("state") == "EXPOSED_UNRESOLVED"
        else "REVIEW_HISTORICAL_EVIDENCE_NO_NEW_PAYMENT"
        if completed
        else "HUMAN_PROVIDER_AWARE_RECOVERY_REVIEW_NO_CAPTURE_OR_RELEASE"
    )
    result = {
        "contract_version": "operation-case-v1",
        "classification": "AUTHENTICATED_RECORDED_SANDBOX_CASE",
        "operation_id": opid,
        "tenant_id": identifier(identity["tenant_id"]),
        "read_at": datetime.now(UTC).isoformat(),
        "status": status,
        "next_action": next_action,
        "operational_cautions": [
            "Stored dispatch exposure remains EXPOSED_UNRESOLVED. Capture, accounting and fulfillment evidence do not close this separate record."
        ]
        if exposure.get("state") == "EXPOSED_UNRESOLVED"
        else [],
        "financial_authority": False,
        "database_writes": False,
        "provider_contacted": False,
        "current_provider_state_established": False,
        "cancellation": {
            key: proof["cancellation"][key]
            for key in (
                "cancellation_id",
                "status",
                "reason",
                "provider_mutation_performed",
                "settlement_proven",
                "replacement_operation_id",
            )
        }
        if cancelled
        else None,
        "commercial": {
            "seller": identifier(q.get("seller")),
            "merchant_id": proof["merchant_id"],
            "amount_minor": proof["amount_minor"],
            "currency": "USD",
            "quantity": len(lines),
            "lines": lines,
            "quote_id": identifier(q.get("id")),
            "quote_expires_at": timestamp(q.get("expires_at")),
            "quote_expired": bool(at(q.get("expires_at")) and at(q["expires_at"]) <= datetime.now(UTC)),
            "quote_integrity_hash": sha(action["terms"].get("quote_digest")),
        },
        "authority": {
            "action_id": proof["action_id"],
            "action_revision": action["head"]["revision"],
            "action_state": state(action["head"].get("status")),
            "current_original_authority": bool(action["current"] and action["fresh"]),
            "evidence_id": proof["evidence_id"],
            "decision_id": proof["decision_id"],
            "policy_version_id": identifier(action.get("decision", {}).get("policy_version_id")),
            "policy_allowed_when_evaluated": action.get("decision", {}).get("allowed"),
            "approval_id": proof["approval_id"],
            "approval_details": {
                "visible": approval is not None,
                "status": state(approval.get("effective_status")) if approval else "PERMISSION_LIMITED",
                "approver": identifier(approval.get("approver")) if approval else None,
                "expires_at": timestamp(approval.get("expires_at")) if approval else None,
            },
            "grant_id": identifier(operation["grant_id"]),
            "grant_effective_status": state(grant.get("effective_status")) if grant else "PERMISSION_LIMITED",
            "grant_expires_at": timestamp(grant["version"].get("expires_at")) if grant else None,
            "lifetime_budget": {key: head[key] for key in ("bound", "consumed", "reserved", "free")},
            "reservation": {
                "id": reservation["id"],
                "amount_minor": reservation["amount"],
                "currency": "USD",
                "status": state(reservation_rows[0].get("status"))
                if len(reservation_rows) == 1
                else "UNAVAILABLE",
                "dispatch_exposure_state": state(exposure.get("state")),
                "source_terms_digest": sha(reservation.get("source_terms_digest")),
            },
        },
        "provider": {
            key: proof[key]
            for key in (
                "provider_order_id",
                "provider_order_status",
                "capture_id",
                "capture_confirmed",
                "last_canonical_read_at",
                "receipt_id",
                "create_committed",
                "capture_committed",
            )
        },
        "economic": {
            "facts": [
                {k: f[k] for k in ("id", "kind", "effect_id", "amount_minor", "currency")}
                for f in proof["facts"]
            ],
            "journals": [
                {
                    **{k: j[k] for k in ("id", "fact_id", "template", "amount_minor", "currency")},
                    "lines": [
                        {k: line[k] for k in ("account", "side", "amount_minor", "currency")}
                        for line in j["lines"]
                    ],
                }
                for j in proof["journals"]
            ],
            "journals_balanced": comparison["journals_balanced"],
        },
        "seller": {"fulfillment_recorded": proof["seller_fulfilled"], "live_seller_contacted": False},
        "reconciliation": {
            "available": bool(stored.get("available")),
            "gaps": comparison["gaps"],
            "runs": runs,
            "findings": findings,
            "bounded_history": True,
            "empty_does_not_prove_absence": True,
        },
        "timeline": timeline,
        "integrity": {
            "action_digest": sha(operation.get("action_digest")),
            "evidence_digest": sha(operation.get("evidence_digest")),
            "request_digest": sha(operation.get("request_digest")),
            "quote_digest": sha(action["terms"].get("quote_digest")),
            "meaning": "Existing source integrity hashes; not proof of an externally trusted signature.",
        },
        "unavailable": [
            "Current PayPal state",
            "Private buyer/payment identity",
            "Unexposed fact/posting/fulfillment timestamps",
            "Provider mutation request identities are not returned by these scoped APIs",
        ],
        "redaction": {
            "mode": "ALLOWLISTED_CASE_FIELDS",
            "raw_transport_bodies_included": False,
            "private_buyer_data_included": False,
            "model_reasoning_included": False,
        },
    }
    result["package_sha256"] = digest(result)
    # Recheck authentication and role before delivering an export across a long read.
    current = reads.identity(token)
    if any(current[key] != identity[key] for key in ("principal", "tenant_id", "roles")):
        raise InvestigationError("CASE_SCOPE_CHANGED", 403)
    return result


def readable_report(case):
    """Self-contained escaped HTML attachment. No active content or external assets."""

    def esc(value):
        return html.escape(str(value) if value is not None else "Unavailable")

    def section(title, values):
        rows = "".join(
            "<tr><th>" + esc(k.replace("_", " ")) + "</th><td>" + esc(v) + "</td></tr>"
            for k, v in values.items()
        )
        return "<section><h2>" + esc(title) + "</h2><table>" + rows + "</table></section>"

    parts = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>Interlock operation evidence</title><style>body{max-width:1000px;margin:40px auto;padding:0 24px;font:15px/1.6 system-ui;color:#182036}h1{font-size:30px}h2{margin-top:34px}table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #ddd;padding:9px;overflow-wrap:anywhere}th{width:28%}small{color:#555}.notice{padding:16px;background:#f1eff9}code{overflow-wrap:anywhere}</style>",
        "<h1>Interlock AI - operation evidence</h1><p class='notice'>Authenticated historical PayPal SANDBOX evidence. No current provider state, new payment authority or externally trusted signature is established by this report. Monetary integers are USD minor units (100 = USD 1.00).</p>",
    ]
    commercial, auth, provider = case["commercial"], case["authority"], case["provider"]
    economic, rec = case["economic"], case["reconciliation"]
    total = f"USD {commercial['amount_minor'] // 100}.{commercial['amount_minor'] % 100:02d}"
    parts.append(
        section(
            "Purchase outcome and outstanding obligations",
            {
                "original_purchase": ", ".join(
                    f"{line['product_id']} for {line['beneficiary']}" for line in commercial["lines"]
                ) + f" / quantity {commercial['quantity']} / {total}",
                "authorization_recorded": (
                    f"{auth['action_state']}; policy allowed when evaluated={auth['policy_allowed_when_evaluated']}; "
                    f"approval reference={auth['approval_id']}; current original authority={auth['current_original_authority']}"
                ),
                "recorded_paypal_outcome": (
                    f"{provider['provider_order_status']}; capture verified={provider['capture_confirmed']}; "
                    f"capture={provider['capture_id']}; observed={provider['last_canonical_read_at']}"
                ),
                "accounting_result": (
                    f"{len(economic['facts'])} facts / {len(economic['journals'])} journal groups / "
                    f"balanced={economic['journals_balanced']}. Posting templates are separate effects, not extra spending."
                ),
                "seller_fulfillment": case["seller"]["fulfillment_recorded"],
                "stored_reconciliation": (
                    ", ".join(run["state"] for run in rec["runs"]) or "Unavailable"
                ) + f"; {len(rec['findings'])} visible findings. Run completion does not close an open finding.",
                "unresolved_findings": "; ".join(
                    f"{finding['class']} / {finding['state']} / next: {finding['next_step']}"
                    for finding in rec["findings"]
                ) or "None visible in bounded history; separate dispatch exposure remains.",
                "outstanding_obligations": (
                    f"{auth['reservation']['dispatch_exposure_state']}; "
                    f"{auth['lifetime_budget']['reserved']} USD minor units lifetime capacity reserved. "
                ) + " ".join(case["operational_cautions"]),
                "required_review": case["next_action"],
            },
        )
    )
    parts.append(
        "<section><h2>Source timeline</h2><p>Unavailable times and actors are not inferred. "
        "Events describe authenticated records, not invented external payment timestamps.</p>"
    )
    for event in case["timeline"]:
        parts.append(section(event["kind"], {k: v for k, v in event.items() if k != "kind"}))
    parts.append("</section><h2>Commercial terms, references and technical appendices</h2>")
    parts.append(
        section(
            "Scope and status",
            {key: case[key] for key in ("operation_id", "tenant_id", "read_at", "status", "next_action")},
        )
    )
    commercial = case["commercial"]
    parts.append(
        section(
            "Original purchase total",
            {
                "price": f"USD {commercial['amount_minor'] // 100}.{commercial['amount_minor'] % 100:02d}",
                "quantity": commercial["quantity"],
            },
        )
    )
    parts.append(section("Original commercial terms", {k: v for k, v in commercial.items() if k != "lines"}))
    for line in commercial["lines"]:
        parts.append(section("Purchased item (USD minor units)", line))
    auth = case["authority"]
    parts.append(
        section("Authority and original approval", {k: v for k, v in auth.items() if not isinstance(v, dict)})
    )
    for key in ("approval_details", "lifetime_budget", "reservation"):
        parts.append(section(key.replace("_", " "), auth[key]))
    parts.append(section("Recorded provider observation", case["provider"]))
    parts.append(
        section(
            "Economic evidence",
            {
                "facts": len(case["economic"]["facts"]),
                "journals": len(case["economic"]["journals"]),
                "journals_balanced": case["economic"]["journals_balanced"],
            },
        )
    )
    for fact in case["economic"]["facts"]:
        parts.append(section("Authenticated economic fact", fact))
    for journal in case["economic"]["journals"]:
        parts.append(section("Journal reference", {k: v for k, v in journal.items() if k != "lines"}))
        for line in journal["lines"]:
            parts.append(section("Journal line (USD minor units)", line))
    parts.append(section("Seller fulfillment", case["seller"]))
    rec = case["reconciliation"]
    parts.append(
        section(
            "Stored reconciliation",
            {
                "available": rec["available"],
                "gaps": ", ".join(rec["gaps"]) or "No gaps in recorded comparison",
                "history_is_bounded": True,
                "run_completion_does_not_resolve_open_findings": True,
            },
        )
    )
    for record in rec["runs"] + rec["findings"]:
        parts.append(section("Stored source record", record))
    parts.extend(
        [
            section("Source integrity", {**case["integrity"], "package_sha256": case["package_sha256"]}),
            "<h2>Unavailable / excluded</h2><ul>"
            + "".join("<li>" + esc(v) + "</li>" for v in case["unavailable"])
            + "</ul>",
            "<small>No password, token, session cookie, provider credential, personal buyer payment identity or hidden model reasoning is included.</small></html>",
        ]
    )
    return "".join(parts)
