"""Two bounded TEST-only probes on the single owner-completed Pass 0 payment.

Each invocation admits at most one initial mutation. Existing operations are read only.
ID-blind mapping uses genuine provider GETs; no lost response is fabricated.
"""

import argparse
import hashlib
import os
import time
from datetime import datetime, timezone

import httpx
from dotenv import dotenv_values
from exercise import ROOT, checked, write_evidence

PURCHASE = "p_b5df52981ca94a91856f3165ebd03c6a"
ORDER = "order_TkBhvfDSMGAcXu"


def main(phase):
    profile = dotenv_values(ROOT / ".env.pass0")
    credentials = dotenv_values(ROOT / ".env.razorpay")
    for key in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        os.environ[key] = credentials.get(key) or ""
    from interlock.providers import RazorpayTest, redacted

    adapter = RazorpayTest()  # Rejects absent or live-mode configuration before any mutation.
    secrets = [
        v
        for k, v in {**profile, **credentials}.items()
        if v and any(x in k for x in ("KEY", "PASSWORD", "SECRET", "TOKEN"))
    ]
    amount = 1000 if phase == "partial" else 2000
    key = "p0-" + phase + "-" + hashlib.sha256(PURCHASE.encode()).hexdigest()[:20]
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=65, follow_redirects=False) as api:
        session = checked(
            api.post("/session", json={"username": "merchant-a", "password": profile["MERCHANT_PASSWORD"]})
        )
        api.headers["Authorization"] = "Bearer " + session["token"]
        evidence = checked(api.get("/purchases/" + PURCHASE))
        assert (
            evidence["id"],
            evidence["provider"],
            evidence["mode"],
            evidence["provider_order"],
            evidence["amount"],
            evidence["currency"],
            evidence["canonical_status"],
            evidence["conflict"],
        ) == (PURCHASE, "RAZORPAY_TEST", "TEST", ORDER, 3000, "INR", "CAPTURE_VERIFIED", False)
        payment_id = evidence["payment_id"]
        payment = adapter.get_payment(payment_id)
        assert (
            payment["id"],
            payment["order_id"],
            payment["amount"],
            payment["currency"],
            payment["captured"],
        ) == (payment_id, ORDER, 3000, "INR", True)
        existing = [x for x in evidence["operations"] if x["kind"] == "REFUND_PROBE"]
        same_phase = [x for x in existing if x["amount"] == amount]
        if phase == "remaining":
            partial = [x for x in existing if x["amount"] == 1000]
            assert len(partial) == 1 and partial[0]["knowledge"] == "CONFIRMED_EFFECT"
        preflight = {
            "classification": "VERIFIED_REAL",
            "provenance": "REAL_RAZORPAY_TEST",
            "mode": "TEST",
            "mode_basis": "Validated Test-key prefix plus owner Test-mode attestation",
            "purchase_id": PURCHASE,
            "provider_order": ORDER,
            "payment_id": payment_id,
            "phase": phase,
            "explicit_refund_amount": amount,
            "currency": "INR",
            "stable_key_sha256": hashlib.sha256(key.encode()).hexdigest(),
            "before_payment": redacted(payment),
            "automatic_mutation_replay": False,
            "seller_return_scope": "Contract probe only; no commercial return or seat revocation",
        }
        write_evidence("refund_" + phase + "_preflight", preflight, secrets)
        if same_phase:
            assert len(same_phase) == 1
            operation_id = same_phase[0]["id"]
            initial = {"operation_id": operation_id, "mutation": "NOT_SENT_EXISTING_OPERATION_READ_ONLY"}
        else:
            assert payment["amount_refunded"] == (0 if phase == "partial" else 1000)
            initial = checked(
                api.post("/refund-probes", json={"purchase_id": PURCHASE, "amount": amount, "probe_key": key})
            )
            operation_id = initial["operation_id"]
        # Preserve initial response before attempting any resolution reads.
        write_evidence("refund_" + phase + "_initial", {**preflight, "initial_response": initial}, secrets)
        reads = []
        for index in range(5):
            result = checked(
                api.post(f"/purchases/{PURCHASE}/refund-read", json={"operation_id": operation_id})
            )
            reads.append({"observed_at": datetime.now(timezone.utc).isoformat(), "result": result})
            if result.get("status") == "processed":
                break
            if index < 4:
                time.sleep(5)
        # Ignore the stored refund ID when identifying from provider collection.
        collection = adapter.payment_refunds(payment_id)
        items = collection.get("items", [])
        matches = [
            x
            for x in items
            if isinstance(x.get("notes"), dict)
            and x["notes"].get("interlock_operation") == key
            and x.get("payment_id") == payment_id
            and type(x.get("amount")) is int
            and x["amount"] == amount
            and x.get("currency") == "INR"
        ]
        assert len(items) < 100 and len(matches) == 1
        refund = adapter.get_refund(matches[0]["id"])
        assert (refund["id"], refund["payment_id"], refund["amount"], refund["currency"]) == (
            matches[0]["id"],
            payment_id,
            amount,
            "INR",
        )
        assert refund["notes"]["interlock_operation"] == key
        assert reads[-1]["result"].get("resource_id") == refund["id"]
        after = adapter.get_payment(payment_id)
        final_evidence = checked(api.get("/purchases/" + PURCHASE))
        write_evidence(
            "refund_" + phase + "_canonical",
            {
                **preflight,
                "operation_id": operation_id,
                "initial_response": initial,
                "read_only_resolution": reads,
                "id_blind_identification": {
                    "source": "REAL_PROVIDER_PAYMENT_REFUNDS_GET",
                    "matching_method": "Unique echoed operation token + exact payment/amount/INR",
                    "match_count": 1,
                    "actual_response_loss": False,
                    "refund": redacted(refund),
                },
                "after_payment": redacted(after),
                "application_purchase": final_evidence,
                "processed_verified": refund["status"] == "processed",
            },
            secrets,
        )
        assert refund["status"] == "processed"
        assert after["amount_refunded"] == (1000 if phase == "partial" else 3000)
        print(
            f"VERIFIED_REAL {phase}: {amount} INR paise; payment {payment_id}; refund {refund['id']}; processed; exact read-only mapping"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["partial", "remaining"])
    args = parser.parse_args()
    try:
        main(args.phase)
    except (AssertionError, httpx.HTTPError, RuntimeError, KeyError):
        print("Probe stopped; no mutation replay/re-key. Inspect stored operation and canonical reads.")
        raise SystemExit(1) from None
