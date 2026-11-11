"""Read-only artifact verification; fixed output, no credential disclosure."""

import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

from razorpay_common import EVIDENCE, ROOT, credentials


def main():
    key, secret, webhook = credentials()
    sensitive = (key, secret, webhook, base64.b64encode(f"{key}:{secret}".encode()).decode())
    files = list((ROOT / "tools/feasibility").glob("*.py"))
    files += list((ROOT / "docs").rglob("*.md"))
    files += list(EVIDENCE.rglob("*.json"))
    for path in files:
        data = path.read_text(encoding="utf-8")
        assert not any(token in data for token in sensitive)
        assert not re.search(r"rzp_(?:test|live)_[A-Za-z0-9]{6,}", data)
    members = []
    for path in EVIDENCE.glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        for member in manifest["members"]:
            item = path.parent / member["file"]
            assert hashlib.sha256(item.read_bytes()).hexdigest() == member["sha256"]
            record = json.loads(item.read_text())
            assert record["request"]["authentication"] == "OMITTED"
            assert not any(name.lower() == "authorization" for name in record["request"]["header_names_only"])
            def check(value):
                if isinstance(value, dict):
                    assert not set(value) & {"email", "contact", "card", "card_id", "notes", "vpa", "acquirer_data"}
                    for nested in value.values():
                        check(nested)
                elif isinstance(value, list):
                    for nested in value:
                        check(nested)
            check(record["response"])
            members.append(record)
    responses = [item for item in members if item["http_status"] is not None]
    assert Counter(item["http_status"] for item in responses) == {200: 61, 400: 1, 409: 1}
    failures = [item for item in members if item["http_status"] is None]
    assert len(failures) == 4 and all(not item["response"] for item in failures)
    correction = json.loads((EVIDENCE / "provenance_correction.json").read_text())
    assert correction["genuine_provider_evidence"] is False
    run = EVIDENCE / "20261003T234647Z_faaa59286a58"
    preparation = json.loads((run / "manifest.json").read_text())["findings"]["order_preparation"]
    assert len({preparation[field] for field in ("primary_order_id", "same_body_order_id", "changed_body_order_id")}) == 3
    def response(run_id, name):
        matches = list((EVIDENCE / run_id).glob("*" + name + ".json"))
        assert len(matches) == 1
        return json.loads(matches[0].read_text())
    refund_run = "20261003T235031Z_395272e9bda1"
    partial = response(refund_run, "partial_refund_create")["response"]
    repeated = response(refund_run, "partial_refund_same_key_same_body")["response"]
    assert partial == repeated and partial["amount"] == 1000 and partial["status"] == "pending"
    assert response(refund_run, "partial_refund_same_key_changed_amount")["http_status"] == 409
    full = response(refund_run, "full_refund_create")["response"]
    assert full["amount"] == 89900 and full["status"] == "pending"
    later_run = "20261003T235442Z_6f48bdf352cf"
    for kind, original in (("partial", partial), ("full", full)):
        later = response(later_run, "retrieve_later_" + kind + "_refund")["response"]
        assert later["status"] == "processed"
        for field in ("id", "payment_id", "amount", "currency"):
            assert later[field] == original[field]
    final = json.loads((EVIDENCE / later_run / "manifest.json").read_text())["findings"]["initial_extraction"]
    assert {kind: len(value["ids"]) for kind, value in final.items()} == {"orders": 28, "payments": 13, "refunds": 2}
    assert set(final["refunds"]["ids"]) == {partial["id"], full["id"]}
    assert all(value["complete_for_observed_pages"] and not value["provider_atomic_snapshot"] for value in final.values())
    tests = json.loads((EVIDENCE / "receiver_selftest_a41b02960242.json").read_text())
    assert tests["genuine_webhook_delivery"] is False and len(tests["tests"]) == 13
    assert all(test["passed"] for test in tests["tests"])
    webhook_run = EVIDENCE / "20261004T072911Z_54fdf88ec91b"
    webhook_findings = json.loads((webhook_run / "manifest.json").read_text())["findings"]
    webhook = webhook_findings["webhooks"]
    assert webhook_findings["receipts_before_trigger"] == 0
    assert webhook_findings["public_health_http_status"] == webhook_findings["local_health_http_status"] == 200
    assert webhook["observed_account_binding"] is True
    assert webhook["duplicate_receipts"] == 0 and webhook["locally_signed_fixtures_sent"] is False
    assert webhook["financial_posts"] == 0
    assert {row["event"] for row in webhook["correlations"]} == {"refund.created", "refund.processed"}
    triggered = webhook_findings["trigger"]
    retrieved = response(webhook_run.name, "webhook_refund_canonical")["response"]
    assert retrieved["id"] == triggered["refund_id"] and retrieved["amount"] == 100 and retrieved["status"] == "processed"
    for row in webhook["correlations"]:
        receipt_path = ROOT / row["receipt_file"]
        assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == row["receipt_sha256"]
        receipt = json.loads(receipt_path.read_text())
        canonical_key = hashlib.sha256((receipt["envelope"]["account_id"] + ":" + receipt["event_id"]).encode()).hexdigest()
        canonical_path = receipt_path.parent / ("event_" + canonical_key + ".json")
        assert canonical_path.read_bytes() == receipt_path.read_bytes()
        check(receipt["envelope"])
        assert receipt["signature_valid"] is True and receipt["signature"] == "OMITTED"
        assert receipt["body_sha256"] == row["body_sha256"]
        assert receipt["event_id"] == row["event_id"] and receipt["envelope"]["account_id"] in webhook["signed_account_ids"]
        entity = receipt["envelope"]["payload"]["refund"]["entity"]
        for field in ("id", "payment_id", "amount", "currency", "created_at"):
            assert entity[field] == retrieved[field]
        assert row["provenance"] == "REAL_RAZORPAY_TEST_MODE_WEBHOOK_API_CORRELATED"
        assert row["raw_body_signature_verified_at_ingress"] and row["immutable_refund_fields_match"] and row["linked_payment_fields_match"]
    assert triggered["refund_id"] in webhook_findings["independent_refund_extraction"]["ids"]
    checkout_run = EVIDENCE / "20261004T080949Z_abf837ed961a"
    checkout = json.loads((checkout_run / "manifest.json").read_text())["findings"]
    assert all(checkout["canonical_checks"].values()) and len(checkout["canonical_checks"]) == 12
    checkout_payment = response(checkout_run.name, "checkout_payment")["response"]
    checkout_order = response(checkout_run.name, "checkout_order")["response"]
    checkout_link = response(checkout_run.name, "checkout_payment_link")["response"]
    assert checkout_link["order_id"] == checkout_order["id"] == checkout_payment["order_id"]
    assert checkout_link["amount"] == checkout_order["amount"] == checkout_payment["amount"] == 6000
    assert checkout_link["currency"] == checkout_order["currency"] == checkout_payment["currency"] == "INR"
    assert checkout["capture_execution"]["agent_capture_post_invoked"] is False
    assert checkout["webhooks"]["financial_posts"] == 0 and checkout["webhooks"]["semantic_captures_observed"] == 1
    assert checkout["webhooks"]["observed_arrival_order"] == ["payment.authorized", "payment.captured", "order.paid"]
    assert checkout["webhooks"]["duplicate_receipts"] == 0 and checkout["webhooks"]["observed_account_binding"] is True
    for row in checkout["webhooks"]["correlations"]:
        receipt_path = ROOT / row["receipt_file"]
        event_path = ROOT / row["canonical_event_file"]
        assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == row["receipt_sha256"]
        assert hashlib.sha256(event_path.read_bytes()).hexdigest() == row["canonical_event_sha256"]
        assert receipt_path.read_bytes() == event_path.read_bytes()
        receipt = json.loads(receipt_path.read_text())
        check(receipt["envelope"])
        assert receipt["signature_valid"] is True and receipt["signature"] == "OMITTED"
        assert receipt["body_sha256"] == row["body_sha256"]
        assert row["raw_body_signature_verified_before_parse_at_ingress"]
        assert row["payment_immutable_fields_match"] and row["order_fields_match"] and row["known_account_match"]
        assert row["provenance"] == "REAL_RAZORPAY_TEST_MODE_WEBHOOK_API_CORRELATED"
    lists = checkout["independent_extraction"]
    assert {kind: len(value["ids"]) for kind, value in lists.items()} == {"orders": 29, "payments": 14, "refunds": 3}
    assert checkout_order["id"] in lists["orders"]["ids"] and checkout_payment["id"] in lists["payments"]["ids"]
    architecture = "docs/THIRD_FLAGSHIP_ARCHITECTURE_PRODUCT_FREEZE_V1.md"
    original = subprocess.check_output(["git", "show", "c60a7b1027e41fd2e0abd37365bde1fdfd32c05e:" + architecture], cwd=ROOT)
    assert (ROOT / architecture).read_bytes().replace(b"\r\n", b"\n") == original.replace(b"\r\n", b"\n")
    assert subprocess.check_output(["git", "branch", "--show-current"], text=True, cwd=ROOT).strip() == "pass0/contracts-provider-feasibility"
    assert ".env" not in subprocess.check_output(["git", "ls-files"], text=True, cwd=ROOT).splitlines()
    print(f"PASS: {len(responses)} real HTTP responses; {len(failures)} corrected transport diagnostics; 5 genuine API-correlated webhook receipts; 12 Checkout checks; all manifest/receipt hashes; refund linkage/transitions; independent counts; credential/PII exclusion; synthetic provenance; unchanged architecture/branch; .env untracked and ignored")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Evidence verification failed; details suppressed to protect credentials")
        raise SystemExit(1) from None
