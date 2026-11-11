"""Offline Pass 0.7 evidence, privacy and noncharging-boundary checks."""

from collections import Counter
import hashlib
import json
import re
import subprocess
import sys
from unittest.mock import patch
import urllib.parse

import payu_testmode_probe as p

RUNS = (
    "20261004T104845Z_557cd483850a",
    "20261004T105129Z_bc6aef4b56d6",
    "20261004T105230Z_3e31dbe6656a",
)


def main():
    key, salt = p.credentials()
    records = []
    for run in RUNS:
        manifest = json.loads((p.EVIDENCE / run / "manifest.json").read_text())
        for field in ("payable_requests_sent", "tokens_registered", "raw_cards_sent",
                      "real_customer_data_sent", "contract_changes", "live_requests"):
            assert manifest[field] == 0
        assert manifest["capability_proven"] is False
        for member in manifest["members"]:
            path = p.EVIDENCE / run / member["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
            record = json.loads(path.read_text())
            assert record["payable_request"] is False
            assert record["automatic_retries"] == 0 and not record["redirects_followed"]
            request = record["request"]
            assert request["authentication"] == request["headers"] == request["body_values"] == "OMITTED"
            assert urllib.parse.urlsplit(request["url"]).hostname in ("test.payu.in", "apitest.payu.in")
            assert record["raw_response_complete"] is True
            assert re.fullmatch(r"[0-9a-f]{64}", record["raw_response_sha256"])
            assert set(record["response"]) <= {
                "json_object", "status", "code", "errorCode", "statusCode",
                "recognized_message_fragments", "message_present", "message_word_templates",
                "saved_instrument_count", "payment_option_groups_present",
                "payment_option_presence_is_not_token_enablement", "transaction_record_count",
                "transaction_failure_record_count", "gateway_option_count",
                "gateway_up_status_counts", "body_omitted", "html_document",
            }
            if record["label"] == "classic_payment_incomplete_validation":
                assert request["body_field_names_only"] == ["key"]
            elif record["label"] == "v2_payment_empty_validation":
                assert request["body_field_names_only"] == []
            records.append(record)
    assert Counter(record["http_status"] for record in records) == {200: 14, 400: 2, 500: 1}
    final = records[-1]
    assert final["label"] == "get_user_cards" and final["response"]["status"] == 0
    assert "invalid merchant id" in final["response"]["recognized_message_fragments"]
    # Refusal bodies agree across classic and v2 reads despite different HTTP status.
    token_reads = [record for record in records if record["label"] in ("get_user_cards", "v2_saved_instruments")]
    assert len({record["raw_response_sha256"] for record in token_reads}) == 2
    # v2 uses a different message envelope; the generic diagnostic templates agree.
    enriched = [record for record in token_reads if "message_word_templates" in record["response"]]
    assert len({tuple(record["response"]["message_word_templates"]) for record in enriched}) == 1
    with patch.object(p, "credentials", return_value=("OFFLINE_MERCHANT", "OFFLINE_SECRET")):
        probe = p.Probe()
        for label in p.READ_COMMANDS | {"v2_saved_instruments", "classic_payment_incomplete_validation", "v2_payment_empty_validation"}:
            method, url, data, headers, names, safe = probe.prepare(label)
            assert urllib.parse.urlsplit(url).hostname in ("test.payu.in", "apitest.payu.in")
            if label == "classic_payment_incomplete_validation":
                assert urllib.parse.parse_qs(data.decode()) == {"key": ["OFFLINE_MERCHANT"]}
            elif label == "v2_payment_empty_validation":
                assert data == b"{}"
            elif label in p.READ_COMMANDS:
                fields = urllib.parse.parse_qs(data.decode())
                assert fields["command"] == [label]
                assert not set(fields) & {"txnid", "amount", "cardToken", "store_card_token", "ccnum"}
                if label == "get_checkout_details":
                    quote = json.loads(fields["var1"][0])
                    assert set(quote) == {"requestId", "transactionDetails", "useCase"}
                    assert quote["transactionDetails"] == {"amount": 60.0}
        for label in ("save_payment_instrument", "si_transaction", "cancel_refund_transaction",
                      "capture_transaction", "delete_user_card", "https://secure.payu.in/_payment"):
            try:
                probe.prepare(label)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Mutation or unknown endpoint admitted")
    marker = "SENSITIVE_VALUE_MUST_BE_OMITTED"
    projection = p.project({"status": 0, "msg": "Invalid Merchant Id or Merchant is " + marker,
        "key": marker, "salt": marker, "user_cards": {marker: {"cardToken": marker,
        "ccnum": marker, "email": marker}}, "details": {"customer": marker}})
    assert marker not in json.dumps(projection) and projection["saved_instrument_count"] == 1
    assert p.NoRedirect().redirect_request(None, None, 302, "", None, "https://secure.payu.in/_payment") is None
    # Recognized refusal stops the sequence before either payment validation POST.
    calls = []
    class RefusedProbe:
        def call(self, label):
            calls.append(label)
            return {"http_status": 200, "response": {"recognized_message_fragments":
                ["invalid merchant id"] if label == "get_user_cards" else []}}
        def finish(self, reason):
            assert "refusal" in reason
    with patch.object(p, "Probe", RefusedProbe), patch.object(sys, "argv", ["offline"]):
        p.main()
    assert calls == ["getNetbankingStatus", "get_checkout_details", "verify_payment",
                     "get_Transaction_Details", "getAllRefundsFromTxnIds", "get_user_cards"]
    boundary = json.loads((p.EVIDENCE / "boundary_manifest.json").read_text())
    for item in boundary["pre_existing_tracked_files"] + boundary["new_artifacts"]:
        path = p.ROOT / item["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    # Compare in memory only. Values and failed-content excerpts are never printed.
    names = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
    sensitive = [key, salt]
    for line in (p.ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if match and match[1] in names:
            sensitive.append(match[2].strip("\"'"))
    files = list((p.ROOT / "docs").rglob("*.md"))
    files += list((p.ROOT / "docs/evidence").rglob("*.json"))
    files += list((p.ROOT / "tools/feasibility").glob("*.py"))
    for path in files:
        content = path.read_text(encoding="utf-8")
        assert not any(value and value in content for value in sensitive), "Credential exclusion failed"
    assert subprocess.check_output(["git", "branch", "--show-current"], cwd=p.ROOT, text=True).strip() == "pass0/contracts-provider-feasibility"
    assert subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=p.ROOT, capture_output=True).returncode != 0
    print("Pass 0.7 verification PASS: 17 HTTP records/hashes; noncharging/redirect/refusal guards; credential/data exclusion; all pre-existing tracked files unchanged; branch/.env boundary")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Pass 0.7 verification failed: " + type(error).__name__)
        raise SystemExit(1)
