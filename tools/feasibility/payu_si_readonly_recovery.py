"""Pass 0.8 recovery: bounded Test transaction extraction only; no mutation."""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

import payu_testmode_probe as common

URL = "https://test.payu.in/merchant/postservice.php?form=2"
COMMAND = "get_Transaction_Details"
EVIDENCE = common.ROOT / "docs/evidence/payu_si_testmode/v1/registration_recovery"
STATUSES = {"success", "captured", "auth", "authorized", "pending", "failed",
            "failure", "in progress", "in-progress", "cancelled", "not found"}


def prepare(command, start, end, key, salt):
    if command != COMMAND:
        raise ValueError("Only documented transaction extraction is allowed")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days <= 3:
        raise ValueError("Recovery date range must be bounded to at most four dates")
    digest = hashlib.sha512("|".join((key, command, start, salt)).encode()).hexdigest()
    fields = {"key": key, "command": command, "var1": start, "var2": end, "hash": digest}
    return urllib.parse.urlencode(fields).encode(), digest


def project(payload):
    if not isinstance(payload, dict):
        return {"json_object": False, "record_set_recognized": False}
    result = {"json_object": True}
    status = payload.get("status")
    if type(status) in (int, bool) or status in ("0", "1", "2"):
        result["business_status"] = status
    messages = [payload.get(name) for name in ("msg", "message", "error", "error_message")]
    text = " ".join(value.lower() for value in messages if isinstance(value, str))
    # Literal error semantics only; no provider free text or customer fields.
    result["recognized_diagnostic_fragments"] = sorted(
        fragment for fragment in common.FRAGMENTS if fragment in text)
    rows = None
    for name in ("transaction_details", "Transaction_details", "transactionDetails"):
        if isinstance(payload.get(name), (list, dict)):
            rows = payload[name]
            break
    result["record_set_recognized"] = rows is not None
    if rows is not None:
        iterable = list(rows.values()) if isinstance(rows, dict) else rows
        result["transaction_record_count"] = len(iterable)
        states = Counter()
        for row in iterable:
            if not isinstance(row, dict):
                states["unclassified"] += 1
                continue
            value = str(row.get("status", "")).lower()
            states[value if value in STATUSES else "unclassified"] += 1
        result["transaction_status_counts"] = dict(states)
    result["registration_inventory_completeness_proven"] = False
    result["provider_consistency_horizon_known"] = False
    return result


def verify():
    prepare(COMMAND, "2026-10-03", "2026-10-04", "OFFLINE_KEY", "OFFLINE_SALT")
    for start, end in (("2026-10-04", "2026-10-03"),
                       ("2026-10-01", "2026-10-05")):
        try:
            prepare(COMMAND, start, end, "OFFLINE_KEY", "OFFLINE_SALT")
        except ValueError:
            pass
        else:
            raise AssertionError("Unbounded or reversed date range accepted")
    for command in ("_payment", "si_transaction", "pre_debit_SI", "verify_payment",
                    "check_mandate_status", "cancel_refund_transaction"):
        try:
            prepare(command, "2026-10-04", "2026-10-04", "OFFLINE_KEY", "OFFLINE_SALT")
        except ValueError:
            pass
        else:
            raise AssertionError("Unapproved command accepted")
    for payload in ({"status": 1, "Transaction_details": []},
                    {"status": 1, "transaction_details": {}}):
        result = project(payload)
        assert result["record_set_recognized"] and result["transaction_record_count"] == 0
        assert not result["registration_inventory_completeness_proven"]
    assert not project({"status": 1})["record_set_recognized"]
    result = project({"status": 1, "Transaction_details": [{"status": "failed",
        "firstname": "PRIVATE_CUSTOMER", "key": "PRIVATE_KEY", "card_no": "PRIVATE_CARD",
        "hash": "PRIVATE_SIGNATURE", "txnid": "PRIVATE_TXN", "id": "PRIVATE_ID"}]})
    assert "PRIVATE" not in json.dumps(result) and result["transaction_status_counts"] == {"failed": 1}
    print("Read-only recovery offline checks PASS: fixed command/date bounds; no mutation or invented IDs; empty is not completeness; customer/credential exclusion")


def collect(start, end):
    key, salt = common.credentials()
    body, digest = prepare(COMMAND, start, end, key, salt)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12]
    directory = EVIDENCE / run
    record = {"schema_version": 1, "command": COMMAND, "sent_at": common.utc(),
        "request": {"method": "POST", "url": URL,
            "body_field_names_only": ["key", "command", "var1", "var2", "hash"],
            "date_from": start, "date_to": end, "credentials_hash_headers": "OMITTED"},
        "read_only": True, "test_only": True, "redirects_followed": False,
        "automatic_retries": 0, "identifiers_fabricated": False}
    opener = urllib.request.build_opener(common.NoRedirect())
    request = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        response = opener.open(request, timeout=25)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        response = None
        record.update(http_status=None, transport_error_class=type(error).__name__,
                      response={"outcome": "UNKNOWN_NO_RETRY"})
    if response is not None:
        with response:
            raw = response.read(4_000_001)
            record.update(http_status=response.code, received_at=common.utc(),
                raw_response_sha256=hashlib.sha256(raw).hexdigest(),
                raw_response_bytes=len(raw), raw_response_complete=len(raw) <= 4_000_000)
            try:
                payload = json.loads(raw) if len(raw) <= 4_000_000 else None
            except (ValueError, UnicodeError):
                payload = None
            record["response"] = project(payload)
    path = directory / "001_get_Transaction_Details.json"
    common.write_json(path, record, [key, salt, digest])
    common.write_json(directory / "manifest.json", {"schema_version": 1,
        "members": [{"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
        "read_only_requests": 1, "mutations": 0, "registrations": 0,
        "notifications": 0, "debits": 0, "raw_payloads_persisted": False}, [key, salt, digest])
    print("Read-only extraction: HTTP " + str(record["http_status"]) +
          "; record count " + str(record["response"].get("transaction_record_count", "UNKNOWN")))
    print("Sanitized evidence: " + str(directory.relative_to(common.ROOT)))


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--verify", action="store_true")
        parser.add_argument("--date-from")
        parser.add_argument("--date-to")
        args = parser.parse_args()
        if args.verify and not args.date_from and not args.date_to:
            verify()
        elif not args.verify and args.date_from and args.date_to:
            collect(args.date_from, args.date_to)
        else:
            raise ValueError("Explicit date range or offline verification required")
    except Exception as error:
        print("Read-only recovery stopped safely: " + type(error).__name__)
        sys.exit(1)
