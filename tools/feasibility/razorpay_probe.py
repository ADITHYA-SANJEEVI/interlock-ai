"""Isolated Test Mode preparation, extraction and explicitly selected refund probes."""

import argparse
import json
import time
from urllib.parse import urlencode
from uuid import uuid4

from razorpay_common import EVIDENCE, Probe


def snapshot(probe, resource, cutoff):
    ids = []
    complete = False
    for page in range(10):
        query = urlencode({"count": 100, "skip": page * 100, "from": 946684800, "to": cutoff})
        status, body = probe.call(f"list_{resource}_{page}", "GET", f"/{resource}?{query}")
        if status != 200 or not isinstance(body.get("items"), list):
            break
        items = body["items"]
        ids.extend(item.get("id") for item in items)
        if len(items) < 100:
            complete = True
            break
    return {"ids": ids, "complete_for_observed_pages": complete,
            "provider_atomic_snapshot": False, "cutoff_epoch": cutoff,
            "scope": "Account API enumeration independent of local journal; no journal exists"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--read-only", action="store_true")
    mode.add_argument("--refund-probes", action="store_true")
    parser.add_argument("--previous-run")
    args = parser.parse_args()
    probe = Probe()
    findings = {"initial_extraction": {}}
    for resource in ("orders", "payments", "refunds"):
        findings["initial_extraction"][resource] = snapshot(probe, resource, int(time.time()))
    if args.refund_probes:
        eligible = []
        for payment_id in findings["initial_extraction"]["payments"]["ids"][:3]:
            status, payment = probe.call("refund_fixture_" + payment_id, "GET", "/payments/" + payment_id)
            if status == 200 and payment.get("captured") is True and payment.get("currency") == "INR" and payment.get("amount_refunded") == 0 and payment.get("amount", 0) >= 2000:
                eligible.append(payment)
        if len(eligible) < 2:
            findings["refunds"] = "Dedicated captured Test Mode fixture required; no creation guessed"
            probe.finish(findings)
            return
        partial, full = eligible[:2]
        key = uuid4().hex
        shape = {"amount": 1000, "receipt": "p05_partial_" + uuid4().hex[:20]}
        endpoint = f"/payments/{partial['id']}/refund"
        status, refund = probe.call("partial_refund_create", "POST", endpoint, shape, {"X-Refund-Idempotency": key})
        if status == 200 and str(refund.get("id", "")).startswith("rfnd_"):
            probe.call("partial_refund_retrieve", "GET", "/refunds/" + refund["id"])
            probe.call("partial_refund_same_key_same_body", "POST", endpoint, shape, {"X-Refund-Idempotency": key})
            probe.call("partial_refund_same_key_changed_amount", "POST", endpoint, dict(shape, amount=1001), {"X-Refund-Idempotency": key})
        full_shape = {"amount": full["amount"], "receipt": "p05_full_" + uuid4().hex[:20]}
        full_status, full_refund = probe.call("full_refund_create", "POST", f"/payments/{full['id']}/refund", full_shape, {"X-Refund-Idempotency": uuid4().hex})
        if full_status == 200 and str(full_refund.get("id", "")).startswith("rfnd_"):
            probe.call("full_refund_retrieve", "GET", "/refunds/" + full_refund["id"])
        for payment in (partial, full):
            probe.call("payment_after_refund_" + payment["id"], "GET", "/payments/" + payment["id"])
            probe.call("payment_refund_list_" + payment["id"], "GET", f"/payments/{payment['id']}/refunds")
        findings["final_independent_refund_extraction"] = snapshot(probe, "refunds", int(time.time()))
        findings["refunds"] = {
            "fixture_origin": "Pre-existing captured Test Mode payments retrieved with authorized test credential; initial checkout/capture not exercised",
            "partial_payment_id": partial["id"], "partial_refund_id": refund.get("id"), "partial_http_status": status,
            "full_payment_id": full["id"], "full_refund_id": full_refund.get("id"), "full_http_status": full_status,
            "retention": "Immediate same-key behavior only; no retention guarantee established",
        }
        probe.finish(findings)
        return
    if args.read_only:
        if args.previous_run:
            previous = EVIDENCE / args.previous_run / "manifest.json"
            if previous.resolve().parent.parent != EVIDENCE.resolve():
                raise RuntimeError("Previous run must be inside evidence root")
            prior = json.loads(previous.read_text(encoding="utf-8"))
            preparation = prior["findings"].get("order_preparation", {})
            receipt = preparation.get("receipt")
            for label, field in (("primary", "primary_order_id"), ("repeat", "same_body_order_id"), ("changed", "changed_body_order_id")):
                object_id = preparation.get(field)
                if object_id:
                    probe.call(f"retrieve_{label}_order", "GET", "/orders/" + object_id)
            if receipt:
                status, result = probe.call("find_receipt_later", "GET", "/orders?" + urlencode({"receipt": receipt, "count": 100, "skip": 0}))
                findings["receipt_query_later"] = {"http_status": status, "returned_count": result.get("count")}
            refunds = prior["findings"].get("refunds", {})
            if isinstance(refunds, dict):
                for kind in ("partial", "full"):
                    object_id = refunds.get(kind + "_refund_id")
                    if object_id:
                        probe.call("retrieve_later_" + kind + "_refund", "GET", "/refunds/" + object_id)
        for payment_id in findings["initial_extraction"]["payments"]["ids"][:3]:
            probe.call("retrieve_payment_" + payment_id, "GET", "/payments/" + payment_id)
            probe.call("retrieve_payment_refunds_" + payment_id, "GET", f"/payments/{payment_id}/refunds")
        findings["mutation"] = "NONE; unrelated historic test payments were not captured/refunded"
        probe.finish(findings)
        return
    receipt = "p05_" + uuid4().hex[:24]
    shape = {"amount": 6000, "currency": "INR", "receipt": receipt, "partial_payment": False}
    status, first = probe.call("prepare_order", "POST", "/orders", shape)
    if status != 200 or not str(first.get("id", "")).startswith("order_"):
        findings["order_preparation"] = "NOT_ESTABLISHED; no mutating request automatically retried"
        probe.finish(findings)
        return
    order = first["id"]
    probe.call("retrieve_prepared_order", "GET", "/orders/" + order)
    same_status, same = probe.call("repeat_same_receipt_body", "POST", "/orders", shape)
    changed_status, changed = probe.call("repeat_receipt_changed_amount", "POST", "/orders", dict(shape, amount=6100))
    probe.call("find_order_by_receipt", "GET", "/orders?" + urlencode({"receipt": receipt, "count": 100, "skip": 0}))
    probe.call("prepared_order_payments", "GET", f"/orders/{order}/payments")
    findings["order_preparation"] = {
        "primary_order_id": order, "receipt": receipt, "amount": 6000, "currency": "INR",
        "same_body_http_status": same_status, "same_body_order_id": same.get("id"),
        "changed_body_http_status": changed_status, "changed_body_order_id": changed.get("id"),
        "no_checkout_or_capture_invoked": True,
        "retention_guarantee": "NOT_ESTABLISHED by finite immediate repeats",
        "lost_response": "NOT_INJECTED; canonical retrieval demonstrated separately",
    }
    findings["remaining"] = [
        "Customer checkout/authorization is a browser step; no undocumented payment-create endpoint called",
        "Capture/full and partial refund require a dedicated genuine test payment",
        "Canonical account ID and genuine webhook verification require Dashboard setup/delivery",
        "No terminal whole-order zero-effect cancellation proof demonstrated",
    ]
    probe.finish(findings)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Probe stopped safely; failure details suppressed to prevent credential disclosure", flush=True)
        raise SystemExit(1) from None
