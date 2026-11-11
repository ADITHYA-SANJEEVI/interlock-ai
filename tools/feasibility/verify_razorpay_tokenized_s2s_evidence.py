"""Offline Pass 0.6 evidence, redaction and noncharging-boundary verification."""

import base64
from collections import Counter
import hashlib
import json
import re
import subprocess
from unittest.mock import patch

import razorpay_common as common
from razorpay_tokenized_s2s_probe import CapabilityProbe, EVIDENCE

RUN = "20261004T092935Z_5aea551be642"


def main():
    key, secret, webhook = common.credentials()
    sensitive = (key, secret, webhook,
                 base64.b64encode(f"{key}:{secret}".encode()).decode(),
                 base64.b64encode((key + ":").encode()).decode())
    paths = list(EVIDENCE.rglob("*.json"))
    paths += list((common.ROOT / "docs").rglob("*.md"))
    paths += list((common.ROOT / "tools/feasibility").glob("*.py"))
    for path in paths:
        data = path.read_text(encoding="utf-8")
        assert not any(value in data for value in sensitive), "Secret exclusion failed"
        assert not re.search(r"rzp_(?:test|live)_[A-Za-z0-9]{6,}", data), "Credential-shaped value found"
    manifest = json.loads((EVIDENCE / RUN / "manifest.json").read_text())
    records = []
    forbidden = {"email", "contact", "card", "card_id", "notes", "vpa", "acquirer_data",
                 "token", "token_id", "customer_id", "name", "last4", "cryptogram_value"}
    def check(value):
        if isinstance(value, dict):
            assert not set(value) & forbidden, "Sensitive response field survived"
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    for member in manifest["members"]:
        path = EVIDENCE / RUN / member["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
        record = json.loads(path.read_text())
        check(record["response"])
        assert record["request"]["authentication"] == "OMITTED"
        assert not re.search(r':\s*"(?:cust|token)_[A-Za-z0-9]+"', path.read_text())
        if record["request"]["method"] == "POST" and record["request"]["path"] != "/customers":
            assert record["request"]["body"] == {}
            assert record["request"]["body_field_names_only"] == []
        records.append(record)
    assert Counter(record["http_status"] for record in records) == {200: 5, 400: 2, 500: 1}
    by_label = {record["label"]: record for record in records}
    assert by_label["isolated_customer_tokens"]["returned_token_count"] == 0
    assert by_label["existing_checkout_payment"]["token_reference_present"] is False
    assert manifest["findings"]["payable_requests_sent"] == 0
    assert manifest["findings"]["token_registration_attempted"] is False
    for label in ("recurring_empty_validation", "s2s_json_empty_validation"):
        error = by_label[label]["response"]["error"]
        assert error["code"] == "BAD_REQUEST_ERROR"
        assert error["source"] == "internal"
        assert error["description"] == "The requested URL was not found on the server."
    # Exercise hard safety guards with the HTTP transport mocked; no provider requests.
    probe = CapabilityProbe.__new__(CapabilityProbe)
    probe.customer_id = None
    probe.auth = "OMITTED"
    probe.key_only_auth = "OMITTED"
    probe.sensitive = ()
    probe.run_id = "OFFLINE_GUARD_CHECK"
    rejected = 0
    with patch.object(common.Probe, "call", return_value=(400, {})) as transport:
        for method, endpoint, body in (
            ("POST", "/payments/create/json", {"amount": 1}),
            ("POST", "/payments/create/recurring", {"recurring": True}),
            ("POST", "/tokens/fetch", {"id": ""}),
            ("POST", "/orders", {}),
            ("GET", "/methods", {}),
            ("POST", "/customers", {"name": "different fixture"}),
        ):
            try:
                probe.call("offline", method, endpoint, body)
            except RuntimeError:
                rejected += 1
        assert rejected == 6 and transport.call_count == 0
    marker = "SHOULD_NEVER_BE_PERSISTED"
    record = probe._record("offline", "GET", "/customers/{isolated_customer_id}/tokens",
                           None, None, "OFFLINE", 200,
                           {"entity": "collection", "count": 1, "items": [
                               {"id": marker, "token": marker, "card": {"name": marker}}
                           ]})
    assert marker not in json.dumps(record)
    assert record["returned_token_count"] == 1
    boundary = json.loads((EVIDENCE / "boundary_manifest_v2.json").read_text())
    for item in boundary["pre_existing_markdown_files"]:
        path = common.ROOT / item["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], "Frozen document changed"
    for item in boundary["new_artifacts"]:
        path = common.ROOT / item["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    assert subprocess.check_output(["git", "branch", "--show-current"], cwd=common.ROOT, text=True).strip() == "pass0/contracts-provider-feasibility"
    assert subprocess.run(["git", "check-ignore", "-q", "--", ".env"], cwd=common.ROOT).returncode == 0
    assert subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=common.ROOT, capture_output=True).returncode != 0
    print("Pass 0.6 verification passed: 8 HTTP records and hashes; secret/PII exclusion; 6 noncharging guards; token redaction; frozen-document hashes; branch/.env boundary")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Pass 0.6 verification failed: " + type(error).__name__)
        raise SystemExit(1)
