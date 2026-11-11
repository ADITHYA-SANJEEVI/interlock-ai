"""Bounded HTTP proof and read-only real-provider evidence. Never handles owner Checkout."""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "docs/evidence/pass0"


def write_evidence(label, record, secrets):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DIRECTORY / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + label + ".json")
    data = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if any(value and len(value) >= 12 and value in data for value in secrets):
        raise RuntimeError("Evidence credential exclusion failed")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(data)
    print("Evidence: " + str(path.relative_to(ROOT)))
    return path


def checked(response):
    if response.status_code >= 300:
        raise RuntimeError(
            f"HTTP proof stopped at status {response.status_code}; no automatic mutation retry"
        )
    return response.json()


def run_slice(real=False, resume=None):
    profile = dotenv_values(ROOT / ".env.pass0")
    secret_values = [
        v for k, v in profile.items() if any(x in k for x in ("TOKEN", "KEY", "PASSWORD", "SECRET"))
    ]
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=65, follow_redirects=False) as api:
        session = checked(
            api.post("/session", json={"username": "owner-a", "password": profile["OWNER_A_PASSWORD"]})
        )
        api.headers["Authorization"] = "Bearer " + session["token"]
        if resume:
            pid = resume
        else:
            if real and any(x["provider"] == "RAZORPAY_TEST" for x in checked(api.get("/purchases"))):
                raise RuntimeError("Existing real purchase must be resumed explicitly; no automatic replacement")
            result = checked(
                api.post(
                    "/purchases",
                    json={
                        "provider": "RAZORPAY_TEST" if real else "REFERENCE_SYNTHETIC",
                        "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}],
                    },
                )
            )
            pid = result["id"]
        prepared = checked(api.post(f"/purchases/{pid}/prepare", json={}))
        if prepared.get("knowledge") == "UNKNOWN":
            checked(api.post(f"/purchases/{pid}/refresh", json={}))
        evidence = checked(api.get("/purchases/" + pid))
        seat_id = evidence["quote"]["lines"][0]["allocation_id"]
        with httpx.Client(
            base_url="http://127.0.0.1:8002",
            timeout=10,
            headers={"Authorization": "Bearer " + profile["SELLER_A_TOKEN"]},
        ) as seller:
            before = seller.get("/workspace/" + seat_id).status_code
            if not real:
                checked(api.post(f"/purchases/{pid}/synthetic-capture", json={}))
            if not real or resume:
                checked(api.post(f"/purchases/{pid}/refresh", json={}))
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    evidence = checked(api.get("/purchases/" + pid))
                    if evidence["entitlement"] == "ACTIVE":
                        break
                    time.sleep(1)
                if evidence["entitlement"] == "ACTIVE":
                    checked(
                        seller.post(
                            "/workspace/" + seat_id,
                            json={"text": "Pass 0 protected access proof " + uuid4().hex[:8]},
                        )
                    )
                    after = checked(seller.get("/workspace/" + seat_id))
                    with httpx.Client(
                        base_url="http://127.0.0.1:8002",
                        timeout=10,
                        headers={"Authorization": "Bearer " + profile["SELLER_B_TOKEN"]},
                    ) as other:
                        cross_tenant = other.get("/workspace/" + seat_id).status_code
                    access = {
                        "before_status": before,
                        "after_status": 200,
                        "entry_count": len(after["entries"]),
                        "wrong_tenant_status": cross_tenant,
                        "provenance": "ACTUAL_CONTROLLED_SELLER_HTTP",
                    }
                else:
                    access = {
                        "before_status": before,
                        "after_status": "WAITING_FOR_CANONICAL_CAPTURE",
                        "provenance": "ACTUAL_CONTROLLED_SELLER_HTTP",
                    }
            else:
                access = {
                    "before_status": before,
                    "after_status": "WAITING_FOR_OWNER_CHECKOUT",
                    "provenance": "ACTUAL_CONTROLLED_SELLER_HTTP",
                }
        evidence = checked(api.get("/purchases/" + pid))
        record = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "adapter_version": "razorpay-test-pass0-v1",
            "classification": "VERIFIED_REAL" if real else "VERIFIED_REFERENCE",
            "purchase": evidence,
            "seller_access": access,
            "mode": "TEST",
            "owner_checkout": "WAITING_FOR_OWNER_CHECKOUT"
            if real and not evidence["payment_id"]
            else "CANONICAL_READ_VERIFIED",
        }
        write_evidence("real_purchase" if real else "reference_slice", record, secret_values)
        print(
            "Purchase: "
            + pid
            + "; provider: "
            + evidence["provider"]
            + "; canonical: "
            + evidence["canonical_status"]
        )


def read_existing():
    profile = dotenv_values(ROOT / ".env.razorpay")
    for key, value in profile.items():
        if key in {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"}:
            os.environ[key] = value or ""
    from interlock.providers import RazorpayTest, redacted

    adapter = RazorpayTest()
    fixtures = [
        ("ordinary_capture", "payment", "pay_TjkzAehO12GaHg"),
        ("partial_refund", "refund", "rfnd_TjcgGd9KW8nOGm"),
        ("full_refund", "refund", "rfnd_TjcgKAXZQ36kCw"),
    ]
    resources = []
    for label, kind, rid in fixtures:
        result = adapter.get_payment(rid) if kind == "payment" else adapter.get_refund(rid)
        if result.get("id") != rid:
            raise RuntimeError("Historical resource canonical identity mismatch")
        parent = (
            adapter.get_order(result["order_id"])
            if kind == "payment"
            else adapter.get_payment(result["payment_id"])
        )
        resources.append(
            {
                "label": label,
                "resource": redacted(result),
                "parent": redacted(parent),
                "source": "REAL_RAZORPAY_TEST_CANONICAL_READ_EXISTING_HISTORICAL_FIXTURE",
            }
        )
    write_evidence(
        "real_historical_reads",
        {
            "classification": "VERIFIED_REAL",
            "mode": "TEST",
            "mode_basis": "Validated rzp_test credential prefix; fixed account profile; historical signed account correlation",
            "adapter_version": "razorpay-test-pass0-v1",
            "api_family": "v1",
            "transport": "httpx 0.28.1; retries=0",
            "automatic_mutation_replay": False,
            "new_mutations": 0,
            "resources": resources,
            "limit": "Historical provider proof, not new application purchase/capture proof",
        },
        list(profile.values()),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["reference", "prepare-real", "read-existing", "resume-real"])
    parser.add_argument("--purchase")
    args = parser.parse_args()
    try:
        if args.action == "read-existing":
            read_existing()
        else:
            if args.action == "resume-real" and not args.purchase:
                parser.error("resume-real requires --purchase; never create a replacement implicitly")
            run_slice(args.action != "reference", args.purchase)
    except (httpx.HTTPError, RuntimeError, KeyError) as error:
        print("Exercise stopped without retry; diagnostic class: " + type(error).__name__)
        raise SystemExit(1) from None
