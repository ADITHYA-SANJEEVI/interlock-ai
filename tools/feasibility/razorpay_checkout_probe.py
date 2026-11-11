"""Read-only Pass 0.5 Checkout object and already-admitted webhook correlation."""

import hashlib
import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from razorpay_common import EVIDENCE, Probe, ROOT
from razorpay_probe import snapshot

PAYMENT = "pay_TjkzAehO12GaHg"
ORDER = "order_TjkyFg6LNxsDjx"
LINK = "plink_Tjky9IBUjddxPa"
ACCOUNT = "acc_TStyPZcYqDkNbI"


def main():
    probe = Probe()
    findings = {"mutation": "NONE; read-only API calls and local sanitized receipt inspection",
                "operator_evidence": {"provenance": "OPERATOR_SUPPLIED", "mode": "TEST",
                    "payment_id": PAYMENT, "order_id": ORDER, "payment_link_id": LINK,
                    "amount": 6000, "currency": "INR", "status": "captured",
                    "created_local": "2026-10-04 13:28 Asia/Calcutta",
                    "authorized_local": "2026-10-04 13:29 Asia/Calcutta",
                    "captured_local": "2026-10-04 13:29 Asia/Calcutta",
                    "instrument_details": "OMITTED; no fixed-instrument proof inferred"},
                "receiver_source_sha256": hashlib.sha256((ROOT / "tools/feasibility/razorpay_webhook_receiver.py").read_bytes()).hexdigest()}
    payment_status, payment = probe.call("checkout_payment", "GET", "/payments/" + PAYMENT)
    order_status, order = probe.call("checkout_order", "GET", "/orders/" + ORDER)
    link_status, link = probe.call("checkout_payment_link", "GET", "/payment_links/" + LINK)
    attempts_status, attempts = probe.call("checkout_order_payments", "GET", f"/orders/{ORDER}/payments")
    checks = {
        "payment_http_200": payment_status == 200, "order_http_200": order_status == 200,
        "payment_link_http_200": link_status == 200, "order_payments_http_200": attempts_status == 200,
        "payment_identity": payment.get("id") == PAYMENT and payment.get("order_id") == ORDER,
        "payment_full_capture": payment.get("status") == "captured" and payment.get("captured") is True and payment.get("amount") == 6000 and payment.get("currency") == "INR",
        "order_paid_exact_amount": order.get("id") == ORDER and order.get("status") == "paid" and order.get("amount") == order.get("amount_paid") == 6000 and order.get("amount_due") == 0 and order.get("currency") == "INR",
        "link_paid_exact_amount": link.get("id") == LINK and link.get("status") == "paid" and link.get("amount") == link.get("amount_paid") == 6000 and link.get("currency") == "INR",
        "link_full_payment_only": link.get("accept_partial") is False,
        "link_reference_correlates_order_receipt": bool(link.get("reference_id")) and link.get("reference_id") == order.get("receipt"),
        "link_payment_identity": any(item.get("payment_id") == PAYMENT and item.get("plink_id", LINK) == LINK and item.get("amount") == 6000 and item.get("status") == "captured" for item in link.get("payments", [])),
        "order_payment_identity": any(item.get("id") == PAYMENT and item.get("order_id") == ORDER and item.get("amount") == 6000 and item.get("currency") == "INR" and item.get("captured") is True for item in attempts.get("items", [])),
    }
    findings["canonical_checks"] = checks
    rows = []
    event_counts = {}
    for path in EVIDENCE.glob("webhook_session_*/receipt_*.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        envelope = receipt["envelope"]
        event_payment = envelope.get("payload", {}).get("payment", {}).get("entity", {})
        event_order = envelope.get("payload", {}).get("order", {}).get("entity", {})
        if event_payment.get("id") != PAYMENT and event_order.get("id") != ORDER:
            continue
        payment_match = payment_status == 200 and all(event_payment.get(field) == payment.get(field) for field in ("id", "order_id", "amount", "currency", "created_at", "method"))
        order_match = not event_order or (order_status == 200 and all(event_order.get(field) == order.get(field) for field in ("id", "amount", "amount_paid", "amount_due", "currency", "created_at", "status")))
        event = envelope.get("event")
        state_match = ((event == "payment.authorized" and event_payment.get("status") == "authorized" and event_payment.get("captured") is False)
                       or (event in ("payment.captured", "order.paid") and event_payment.get("status") == "captured" and event_payment.get("captured") is True))
        raw_verified = receipt.get("signature_valid") is True and receipt.get("signature") == "OMITTED"
        account_match = envelope.get("account_id") == ACCOUNT
        genuine = raw_verified and payment_match and order_match and state_match and account_match
        event_counts[receipt["event_id"]] = event_counts.get(receipt["event_id"], 0) + 1
        key = hashlib.sha256((envelope["account_id"] + ":" + receipt["event_id"]).encode()).hexdigest()
        canonical_path = path.parent / ("event_" + key + ".json")
        epoch = envelope.get("created_at")
        rows.append({
            "receipt_file": path.relative_to(ROOT).as_posix(), "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "canonical_event_file": canonical_path.relative_to(ROOT).as_posix(), "canonical_event_sha256": hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
            "body_sha256": receipt["body_sha256"], "event_id": receipt["event_id"], "event": event,
            "observed_at": receipt["observed_at"], "observed_local": datetime.fromisoformat(receipt["observed_at"]).astimezone(ZoneInfo("Asia/Calcutta")).isoformat(),
            "event_created_at_epoch": epoch,
            "event_created_at_utc": datetime.fromtimestamp(epoch, timezone.utc).isoformat() if isinstance(epoch, int) else None,
            "payment_snapshot_status": event_payment.get("status"), "payment_snapshot_captured": event_payment.get("captured"),
            "canonical_payment_status": payment.get("status"), "account_id": envelope.get("account_id"),
            "raw_body_signature_verified_before_parse_at_ingress": raw_verified,
            "payment_immutable_fields_match": payment_match, "order_fields_match": order_match,
            "event_state_compatible_with_later_capture": state_match, "known_account_match": account_match,
            "disposition": receipt["disposition"],
            "provenance": "REAL_RAZORPAY_TEST_MODE_WEBHOOK_API_CORRELATED" if genuine else "PRESERVED_UNCLASSIFIED_WEBHOOK",
        })
    rows.sort(key=lambda row: (row["observed_at"], row["receipt_file"]))
    findings["webhooks"] = {
        "correlations": rows, "observed_arrival_order": [row["event"] for row in rows],
        "duplicate_receipts": sum(count - 1 for count in event_counts.values()),
        "observed_account_binding": bool(rows) and all(row["known_account_match"] and row["provenance"] == "REAL_RAZORPAY_TEST_MODE_WEBHOOK_API_CORRELATED" for row in rows),
        "binding_basis": "Signed known account_id from refund probe; Checkout Payment and linked Order/Payment Link retrieved using checked Test Mode credential; human Test Mode attestation",
        "mode_limitation": "No signed TEST body flag or API account_id field; mode derives from endpoint attestation, checked credential and unique correlated objects",
        "signature_limitation": "Ingress verified exact raw bytes before parsing; sanitized artifacts cannot be re-signed/reverified offline; no raw payload/signature persisted",
        "capture_effect_identity": ["razorpay", ACCOUNT, "TEST", PAYMENT, "SINGLE_CAPTURE_EFFECT"],
        "semantic_captures_observed": 1 if checks["payment_full_capture"] else 0,
        "financial_posts": 0,
    }
    findings["capture_execution"] = {
        "provider_capture_observed": checks["payment_full_capture"],
        "agent_capture_post_invoked": False,
        "automatic_capture": "Consistent with automatic capture in this hosted Checkout: authorized then captured in under a second at receiver; no agent capture POST and human completed requested success flow. API does not name capture initiator or prove global settings.",
        "original_prepared_order_executed": False,
        "fixed_owner_instrument_bound": "NOT_ESTABLISHED; instrument/customer fields deliberately excluded; human card selection is not provider enforcement",
        "dispatch_fence_enforced": "NOT_ESTABLISHED; diagnostic human hosted Checkout bypassed any product command/permit",
    }
    findings["independent_extraction"] = {resource: snapshot(probe, resource, int(time.time())) for resource in ("orders", "payments", "refunds")}
    probe.finish(findings)
    print(f"Canonical checks: {sum(checks.values())}/{len(checks)}; Checkout webhook receipts: {len(rows)}; read-only; no capture/refund initiated", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Checkout correlation stopped safely; diagnostic details suppressed", flush=True)
        raise SystemExit(1) from None
