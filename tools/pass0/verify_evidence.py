"""Independent file-integrity/provenance check. Never prints sensitive material."""

import argparse
import hashlib
import json
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def verify_completion(current):
    completions = [x for x in current if x.get("pass_verdict") == "PASS 0 COMPLETE"]
    assert len(completions) == 1, "Exactly one final completion record required"
    completion = completions[0]
    assert (
        completion["canonical_sha256"]
        == hashlib.sha256((ROOT / "docs/Third_Flagship_Canonical_Build_Spec.md").read_bytes()).hexdigest()
    )
    purchase = completion["purchase"]
    expected = ("p_b5df52981ca94a91856f3165ebd03c6a", "order_TkBhvfDSMGAcXu", "pay_TkCiZWpYMcUwYo")
    assert (purchase["id"], purchase["provider_order"], purchase["payment_id"]) == expected
    assert (purchase["amount"], purchase["currency"], purchase["account"], purchase["mode"]) == (
        3000,
        "INR",
        "acc_TStyPZcYqDkNbI",
        "TEST",
    )
    assert purchase["canonical_status"] == "CAPTURE_VERIFIED" and not purchase["conflict"]
    assert purchase["provider_status"] == "refunded" and purchase["entitlement"] == "ACTIVE"
    assert completion["seller_access"]["after_status"] == 200
    assert completion["seller_access"]["wrong_tenant_status"] == 404
    assert completion["seller_access"]["entry_count"] >= 1
    assert completion["owner_attestation"]["test_mode"] is True
    assert completion["owner_attestation"]["manual_capture"] is False
    assert completion["automatic_mutation_replay"] is False
    assert completion["application_real_purchase_count"] == 1
    assert completion["new_purchase_or_order_this_continuation"] == 0
    assert all(x["passed"] for x in completion["canonical_pass0_criteria"])
    assert completion["automated"]["pytest"] == {"passed": 35, "failed": 0, "skipped": 0}
    assert completion["automated"]["browser"] == {"passed": 3, "failed": 0, "skipped": 0}
    canonical = [x for x in current if x.get("processed_verified") is True]
    assert len(canonical) == 2
    amounts = []
    ids = []
    for record in canonical:
        assert (record["purchase_id"], record["provider_order"], record["payment_id"]) == expected
        assert record["automatic_mutation_replay"] is False and record["mode"] == "TEST"
        mapping = record["id_blind_identification"]
        assert mapping["match_count"] == 1 and mapping["actual_response_loss"] is False
        assert mapping["source"] == "REAL_PROVIDER_PAYMENT_REFUNDS_GET"
        refund = mapping["refund"]
        assert refund["status"] == "processed" and refund["currency"] == "INR"
        assert refund["payment_id"] == expected[2] and refund["amount"] == record["explicit_refund_amount"]
        amounts.append(refund["amount"])
        ids.append(refund["id"])
        last_read = record["read_only_resolution"][-1]["result"]
        assert last_read["resolution"] == "READ_ONLY" and last_read["resource_id"] == refund["id"]
        assert last_read["status"] == "processed"
    assert sorted(amounts) == [1000, 2000] and len(set(ids)) == 2
    operations = purchase["operations"]
    refunds = [x for x in operations if x["kind"] == "REFUND_PROBE"]
    assert len(refunds) == 2 and all(x["knowledge"] == "CONFIRMED_EFFECT" for x in refunds)
    assert {x["resource_id"] for x in refunds} == set(ids)
    accepts = [x for x in operations if x["kind"] == "SELLER_ACCEPT"]
    assert len(accepts) == 1 and accepts[0]["knowledge"] == "CONFIRMED_SELLER_EFFECT"
    assert accepts[0]["resource_id"] == purchase["seller_order"]
    orders = [x for x in operations if x["kind"] == "PROVIDER_ORDER"]
    assert len(orders) == 1 and orders[0]["resource_id"] == expected[1]
    before = next(x for x in current if x.get("owner_checkout") == "WAITING_FOR_OWNER_CHECKOUT")
    assert before["purchase"]["id"] == expected[0] and before["seller_access"]["before_status"] == 403
    remaining = next(x for x in canonical if x["phase"] == "remaining")
    assert remaining["after_payment"]["amount_refunded"] == 3000
    assert remaining["after_payment"]["captured"] is True
    print(
        "PASS: connected real capture/access, two exact processed refunds, ID-blind read correlation, all Pass 0 gates"
    )


def main(require_complete=False):
    secrets = []
    for profile in (".env", ".env.pass0", ".env.razorpay"):
        secrets += [
            value
            for name, value in dotenv_values(ROOT / profile).items()
            if value and len(value) >= 12 and any(x in name for x in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        ]
    files = [
        *list((ROOT / "docs").rglob("*.md")),
        *list((ROOT / "docs/evidence").rglob("*.json")),
        *list((ROOT / "backend").rglob("*.py")),
        *list((ROOT / "web/app").rglob("*.tsx")),
    ]
    for path in files:
        data = path.read_text(encoding="utf-8")
        if any(secret in data for secret in secrets):
            raise RuntimeError("Credential exclusion failed; no content displayed")
    count = 0
    for manifest in (ROOT / "docs/evidence/razorpay_testmode/v1").glob("*/manifest.json"):
        data = json.loads(manifest.read_text())
        for member in data["members"]:
            path = manifest.parent / member["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
            count += 1
    current = [json.loads(p.read_text()) for p in (ROOT / "docs/evidence/pass0").glob("*.json")]
    manifest = json.loads((ROOT / "docs/evidence/pass0/manifest.json").read_text())
    for member in manifest["members"]:
        path = ROOT / "docs/evidence/pass0" / member["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
    real = [
        x
        for x in current
        if x.get("mode") == "TEST" and x.get("purchase", {}).get("provider") == "RAZORPAY_TEST"
    ]
    assert real and all(x["purchase"]["amount"] == 3000 for x in real)
    reference = [x for x in current if x.get("purchase", {}).get("provider") == "REFERENCE_SYNTHETIC"]
    assert reference and all(x["classification"] == "VERIFIED_REFERENCE" for x in reference)
    access = reference[0]["seller_access"]
    assert (
        access["before_status"] == 403
        and access["after_status"] == 200
        and access["wrong_tenant_status"] == 404
    )
    historical = next(x for x in current if x.get("resources"))
    assert historical["new_mutations"] == 0
    assert historical["resources"][1]["resource"]["status"] == "processed"
    assert historical["resources"][2]["resource"]["status"] == "processed"
    if require_complete:
        verify_completion(current)
    print(
        f"PASS: {count} historical manifest members intact; {len(current)} current evidence artifacts; credential exclusion; provenance and seller access checks"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-complete", action="store_true")
    main(parser.parse_args().require_complete)
