"""Pass 0.5 genuine refund-webhook delivery/correlation; no Checkout or capture."""

import hashlib
import json
import re
import time
import urllib.request
from uuid import uuid4

from razorpay_common import EVIDENCE, Probe, ROOT, utc, write_json
from razorpay_probe import snapshot

PUBLIC_ORIGIN = "https://participate-almost-tube-guarantee.trycloudflare.com"
PAYMENT_ID = "pay_TXEHllsCWPOjiD"


def receipts(refund_id=None):
    result = []
    for path in EVIDENCE.glob("webhook_session_*/receipt_*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        entity = item.get("envelope", {}).get("payload", {}).get("refund", {}).get("entity", {})
        if refund_id is None or entity.get("id") == refund_id:
            result.append((path, item))
    return sorted(result, key=lambda pair: (pair[1]["observed_at"], pair[0].name))


def main():
    probe = Probe()
    findings = {"purpose": "One INR 1 Test Mode refund to trigger genuine subscribed webhook events",
                "human_dashboard_evidence": {
                    "status": "ENABLED", "mode": "TEST", "same_local_webhook_secret": True,
                    "url": PUBLIC_ORIGIN + "/webhooks/razorpay",
                    "provenance": "OPERATOR_SUPPLIED",
                },
                "receiver_source_sha256": hashlib.sha256((ROOT / "tools/feasibility/razorpay_webhook_receiver.py").read_bytes()).hexdigest(),
                "receipts_before_trigger": len(receipts()), "started_at": utc()}
    for label, url in (("local", "http://127.0.0.1:8765/health"), ("public", PUBLIC_ORIGIN + "/health")):
        # No credentials/signature/request bodies are sent to either health URL.
        with probe.opener.open(urllib.request.Request(url, method="GET"), timeout=15) as response:
            findings[label + "_health_http_status"] = response.status
            if response.status != 200:
                raise RuntimeError("Receiver health is required")
    print("Local and public receiver health: HTTP 200", flush=True)
    status, payment = probe.call("webhook_fixture_payment", "GET", "/payments/" + PAYMENT_ID)
    if not (status == 200 and payment.get("id") == PAYMENT_ID and payment.get("captured") is True
            and payment.get("currency") == "INR" and payment.get("amount", 0) - payment.get("amount_refunded", 0) >= 100):
        findings["trigger"] = "NOT_ATTEMPTED; known Test Mode captured/refundable fixture required"
        probe.finish(findings)
        return
    status, refund = probe.call("webhook_refund_trigger", "POST", f"/payments/{PAYMENT_ID}/refund",
                               {"amount": 100, "receipt": "p05_hook_" + uuid4().hex[:20], "speed": "normal"},
                               {"X-Refund-Idempotency": uuid4().hex})
    if status != 200 or not re.fullmatch(r"rfnd_[A-Za-z0-9]+", str(refund.get("id", ""))):
        findings["trigger"] = "UNRESOLVED; no automatic mutating retry or replacement"
        probe.finish(findings)
        return
    refund_id = refund["id"]
    findings["trigger"] = {"refund_id": refund_id, "payment_id": PAYMENT_ID, "amount": 100, "currency": "INR"}
    deadline = time.monotonic() + 120
    both_at = None
    while time.monotonic() < deadline:
        current = receipts(refund_id)
        events = {item["envelope"].get("event") for _, item in current}
        if {"refund.created", "refund.processed"} <= events:
            both_at = both_at or time.monotonic()
            if time.monotonic() - both_at >= 15:
                break
        time.sleep(2)
    current = receipts(refund_id)
    status, canonical = probe.call("webhook_refund_canonical", "GET", "/refunds/" + refund_id)
    payment_status, linked = probe.call("webhook_payment_canonical", "GET", "/payments/" + PAYMENT_ID)
    order_id = linked.get("order_id")
    if payment_status == 200 and isinstance(order_id, str) and re.fullmatch(r"order_[A-Za-z0-9]+", order_id):
        probe.call("webhook_order_canonical", "GET", "/orders/" + order_id)
    extraction = snapshot(probe, "refunds", int(time.time()))
    accounts = set()
    correlations = []
    counts = {}
    for path, item in current:
        envelope = item["envelope"]
        entity = envelope["payload"]["refund"]["entity"]
        accounts.add(envelope["account_id"])
        immutable_match = status == 200 and all(entity.get(field) == canonical.get(field)
                                                for field in ("id", "payment_id", "amount", "currency", "created_at"))
        linked_payment_match = payment_status == 200 and canonical.get("payment_id") == linked.get("id")
        payment_snapshot = envelope["payload"].get("payment", {}).get("entity")
        if isinstance(payment_snapshot, dict):
            linked_payment_match = linked_payment_match and all(payment_snapshot.get(field) == linked.get(field)
                                                                for field in ("id", "order_id", "amount", "currency"))
        snapshot_status = entity.get("status")
        status_compatible = snapshot_status in ("pending", "processed") and canonical.get("status") == "processed"
        genuine = (item["signature_valid"] is True and item["disposition"] in ("NEW", "DUPLICATE")
                   and immutable_match and linked_payment_match and status_compatible)
        counts[item["event_id"]] = counts.get(item["event_id"], 0) + 1
        correlations.append({
            "receipt_file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "observed_at": item["observed_at"], "event_id": item["event_id"], "event": envelope["event"],
            "body_sha256": item["body_sha256"], "account_id": envelope["account_id"],
            "raw_body_signature_verified_at_ingress": item["signature_valid"],
            "immutable_refund_fields_match": immutable_match, "linked_payment_fields_match": linked_payment_match,
            "event_refund_status": snapshot_status, "retrieved_refund_status": canonical.get("status"),
            "status_compatible_without_arrival_order_overwrite": status_compatible,
            "disposition": item["disposition"],
            "provenance": "REAL_RAZORPAY_TEST_MODE_WEBHOOK_API_CORRELATED" if genuine else "PRESERVED_UNCLASSIFIED_WEBHOOK",
        })
    binding = len(accounts) == 1 and bool(correlations) and all(row["provenance"] == "REAL_RAZORPAY_TEST_MODE_WEBHOOK_API_CORRELATED" for row in correlations)
    findings["webhooks"] = {
        "correlations": correlations, "signed_account_ids": sorted(accounts), "observed_account_binding": binding,
        "binding_basis": "Signed account_id plus unique just-created Refund/Payment/Order retrieved using locally checked Test Mode credential; human configured Test Mode endpoint",
        "limitations": "API entities have no canonical account_id or mode fields; Dashboard account ID independently supplied is still absent. Exact raw bytes/signatures were checked in receiver memory before JSON parse and not persisted, so no offline re-verification claim.",
        "duplicate_receipts": sum(count - 1 for count in counts.values()),
        "provider_redelivery_forced": False, "locally_signed_fixtures_sent": False,
        "event_arrival_order": [row["event"] for row in correlations],
        "financial_posts": 0,
    }
    findings["independent_refund_extraction"] = extraction
    findings["next_human_step"] = "New legitimate Test Mode Checkout to produce payment authorization/capture and Order events; no Checkout initiated by this probe"
    probe.finish(findings)
    print(f"Webhook receipts: {len(current)}; API-correlated account binding: {binding}; natural duplicate receipts: {sum(count - 1 for count in counts.values())}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Webhook probe stopped safely; diagnostics suppressed; do not automatically repeat a mutating probe", flush=True)
        raise SystemExit(1) from None
