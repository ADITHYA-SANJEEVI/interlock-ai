"""Exact Pass 0.9 diagnosis: two documented Test reads, never a payment request."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import payu_hosted_si_consent as consent
import payu_si_readonly_recovery as recovery
from verify_payu_hosted_si import Inputs

common = consent.common
TXN = "p09e5623a69c9134aa194ecd3"
DIRECTORY = consent.EVIDENCE / "diagnosis"
SERVICE = recovery.URL
STATES = recovery.STATUSES | {"not found", "error", "initiated", "dropped", "bounced"}


def prepare(command, first, last, key, salt):
    if command == "get_Transaction_Details":
        if (first, last) != ("2026-10-04", "2026-10-05"):
            raise ValueError("Only the exact attempt's UTC/IST dates are allowed")
        return recovery.prepare(command, first, last, key, salt)
    if command != "verify_payment" or first != TXN or last is not None:
        raise ValueError("Only the existing legitimate transaction may be verified")
    digest = hashlib.sha512("|".join((key, command, first, salt)).encode()).hexdigest()
    return urllib.parse.urlencode({"key": key, "command": command, "var1": first,
                                  "hash": digest}).encode(), digest


def diagnostic(value):
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        return "OMITTED_UNRECOGNIZED"
    if re.fullmatch(r"E\d{3,6}", value):
        return value
    words = re.findall(r"[a-z]+", value.lower())
    if words and len(value) <= 250 and all(word in common.SAFE_WORDS for word in words) and not re.search(r"[0-9@/\\]", value):
        return value
    return {"free_text": "OMITTED", "recognized_fragments": sorted(
        fragment for fragment in common.FRAGMENTS if fragment in value.lower())}


def row_projection(row, key):
    result = {"txnid": TXN, "provider_record_field_names": sorted(row),
              "credentials_customer_card_token_fields": "OMITTED"}
    for name in ("status", "unmappedstatus"):
        value = row.get(name)
        if isinstance(value, str) and value.lower() in STATES:
            result[name] = value
    for name in ("mihpayid", "mihpayupid", "id"):
        value = str(row.get(name, ""))
        if re.fullmatch(r"[0-9]{1,30}", value):
            result[name] = value
    for name in ("amount", "amt"):
        value = str(row.get(name, ""))
        if re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value):
            result[name] = value
    for name in ("error", "error_code", "error_message", "error_Message", "field7", "field8", "failure_reason"):
        if name in row:
            result[name] = diagnostic(row[name])
    for name in ("mode", "PG_TYPE", "payment_source"):
        value = row.get(name)
        if value in ("CC", "DC", "CC-PG", "DC-PG", "sist", "payu", "ENACH", "UPI", "NB"):
            result[name] = value
        elif name in row:
            result[name] = "OMITTED_UNRECOGNIZED"
    result["returned_merchant_key_matches"] = row.get("key") == key if "key" in row else None
    return result


def project(payload, command, key):
    result = recovery.project(payload)
    if not isinstance(payload, dict):
        result["canonical_outcome"] = "UNKNOWN"
        return result
    rows = next((payload[name] for name in ("transaction_details", "Transaction_details", "transactionDetails")
                 if isinstance(payload.get(name), (list, dict))), None)
    target = rows.get(TXN) if command == "verify_payment" and isinstance(rows, dict) else None
    matches = [row for row in (rows.values() if isinstance(rows, dict) else rows or [])
               if isinstance(row, dict) and row.get("txnid") == TXN]
    if isinstance(target, dict) and target not in matches:
        matches.append(target)
    result["exact_transaction_match_count"] = len(matches)
    result["exact_transaction_records"] = [row_projection(row, key) for row in matches]
    if isinstance(target, dict) and str(target.get("status", "")).lower() == "not found" and target.get("mihpayid") == "Not Found":
        result["canonical_outcome"] = "NOT_FOUND_IN_VERIFY_PAYMENT"
        result["not_found_placeholder_is_provider_identifier"] = False
    elif matches:
        result["canonical_outcome"] = "RECORD_RETURNED_REQUIRES_STATUS_CLASSIFICATION"
    else:
        result["canonical_outcome"] = "NO_EXACT_RECORD_IN_RETURNED_SET" if rows is not None else "UNKNOWN"
    result["absence_of_all_internal_provider_objects_proven"] = False
    return result


def read_once(command, first, last, key, salt, filename):
    body, digest = prepare(command, first, last, key, salt)
    record = {"schema_version": 1, "command": command, "sent_at": common.utc(),
              "request": {"method": "POST", "url": SERVICE, "var1": first,
                          "body_field_names_only": ["key", "command", "var1", "hash"] + (["var2"] if last else []),
                          "credentials_hash_headers": "OMITTED"},
              "read_only": True, "test_only": True, "automatic_retries": 0, "redirects_followed": False}
    if last:
        record["request"]["var2"] = last
    request = urllib.request.Request(SERVICE, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        response = urllib.request.build_opener(common.NoRedirect()).open(request, timeout=25)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        response = None
        record.update(http_status=None, transport_error_class=type(error).__name__, response={"canonical_outcome": "UNKNOWN"})
    if response is not None:
        with response:
            raw = response.read(4_000_001)
            record.update(http_status=response.code, received_at=common.utc(),
                          raw_response_sha256=hashlib.sha256(raw).hexdigest(), raw_response_bytes=len(raw),
                          raw_response_complete=len(raw) <= 4_000_000)
            try:
                payload = json.loads(raw) if len(raw) <= 4_000_000 else None
            except (ValueError, UnicodeError):
                payload = None
            record["response"] = project(payload, command, key)
    common.write_json(DIRECTORY / filename, record, [key, salt, digest])
    print(command + ": HTTP " + str(record["http_status"]) + "; " + record["response"]["canonical_outcome"])


def audit_request(key, salt):
    plan = json.loads((consent.EVIDENCE / "launch_plan.json").read_text())
    contract = plan["contract"]
    assert contract["txnid"] == TXN
    request = urllib.request.Request(consent.PHONE_SOURCE, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=25) as response:
        documentation = response.read(2_000_000).decode("utf-8")
    sample = re.search(r"phone=(\d{10})(?:&|\s)", documentation)
    if not sample:
        raise ValueError("Original official sample unavailable")
    identity = {"firstname": "Test" + "Owner", "email": TXN + "@example.com", "phone": sample[1]}
    fields = consent.request_fields(contract, key, salt, identity)
    # Independent literal sequence, not the builder's sequence assembly.
    literal = key + "|" + TXN + "|2.00|" + contract["productinfo"] + "|" + identity["firstname"] + "|" + identity["email"] + "|||||||||||" + contract["si_details"] + "|" + salt
    fingerprint = hashlib.sha256(fields["hash"].encode()).hexdigest()
    assert fingerprint == plan["request_hash_sha256"]
    assert fields["hash"] == hashlib.sha512(literal.encode()).hexdigest()
    parser = Inputs()
    parser.feed("".join('<input name="' + escape(name, quote=True) + '" value="' + escape(value, quote=True) + '">' for name, value in fields.items()))
    encoded = urllib.parse.urlencode(parser.values).encode()
    decoded = consent.decode_form(encoded)
    assert decoded == fields and decoded["si_details"].encode() == contract["si_details"].encode()
    required = {"key", "txnid", "amount", "productinfo", "firstname", "email", "phone", "surl", "furl", "api_version", "si", "si_details", "hash"}
    checks = {"all_required_field_names_present": required <= fields.keys(), "txnid_25_alphanumeric": bool(re.fullmatch(r"[A-Za-z0-9]{25}", TXN)),
              "productinfo_characters_and_length": bool(re.fullmatch(r"[A-Za-z0-9_@/ .-]{1,100}", fields["productinfo"])),
              "test_identity_lengths_and_characters_valid": all(re.fullmatch(r"[A-Za-z0-9_@/ .-]+", value) and len(value) <= limit for value, limit in zip(identity.values(), (60, 50, 50))),
              "phone_ten_digits": bool(re.fullmatch(r"\d{10}", identity["phone"])), "independent_exact_si_hash_matches": True,
              "original_prepared_hash_fingerprint_matches": True, "reconstructed_html_form_roundtrip_identical": True,
              "post_si_details_utf8_equals_hash_string": True, "salt_absent_from_browser_fields": salt not in fields.values(),
              "callback_urls_https": all(urllib.parse.urlsplit(contract[name]).scheme == "https" for name in ("surl", "furl")),
              "future_start_before_end": "2026-10-05" < "2026-10-08" < "2026-10-12"}
    assert all(checks.values())
    return {"schema_version": 1, "audited_at": common.utc(), "contract": contract, "submitted_field_names": sorted(fields),
            "checks": checks, "si_details_utf8_sha256": hashlib.sha256(contract["si_details"].encode()).hexdigest(),
            "prepared_request_hash_sha256": fingerprint, "actual_browser_wire_body_preserved": False,
            "audit_scope": "RECONSTRUCTED_EXACT_PREPARED_FIELDS_FINGERPRINT_BOUND_NOT_BROWSER_NETWORK_TRACE",
            "test_identity_only_not_real_customer_metadata": True, "secrets_identity_and_full_hash": "OMITTED",
            "documented_merchant_acceptance_of_pg_enforce_combination_proven": False, "merchant_si_entitlement_proven": False}


def verify():
    prepare("verify_payment", TXN, None, "PRIVATE_KEY", "PRIVATE_SALT")
    for command, first, last in (("_payment", TXN, None), ("si_transaction", TXN, None), ("verify_payment", "invented", None),
                                 ("get_Transaction_Details", "2026-10-03", "2026-10-05")):
        try:
            prepare(command, first, last, "PRIVATE_KEY", "PRIVATE_SALT")
        except ValueError:
            pass
        else:
            raise AssertionError("Unapproved request accepted")
    result = project({"status": 0, "transaction_details": {TXN: {"status": "Not Found", "mihpayid": "Not Found"}}}, "verify_payment", "PRIVATE_KEY")
    assert result["canonical_outcome"] == "NOT_FOUND_IN_VERIFY_PAYMENT" and "mihpayid" not in result["exact_transaction_records"][0]
    result = project({"status": 1, "transaction_details": {TXN: {"status": "failed", "error": "E001", "error_message": "Invalid hash", "field7": "PRIVATE_CARD_123", "firstname": "PRIVATE_NAME", "cardToken": "PRIVATE_TOKEN", "key": "PRIVATE_KEY"}}}, "verify_payment", "PRIVATE_KEY")
    assert "PRIVATE" not in json.dumps(result) and result["exact_transaction_records"][0]["error"] == "E001"
    assert not project({"status": 1, "Transaction_details": []}, "get_Transaction_Details", "PRIVATE_KEY")["absence_of_all_internal_provider_objects_proven"]
    print("Pass 0.9 diagnosis offline checks PASS: exact read allowlist, date/identity guards, privacy, not-found is not internal completeness")


def collect():
    if DIRECTORY.exists():
        raise ValueError("Diagnosis already reserved; no automated read retries")
    marker = json.loads((consent.EVIDENCE / "launch_consumed.json").read_text())
    assert marker["txnid"] == TXN
    instant = datetime.fromisoformat(marker["consumed_at"])
    assert instant.date().isoformat() == "2026-10-04" and instant.astimezone(timezone(timedelta(hours=5, minutes=30))).date().isoformat() == "2026-10-05"
    key, salt = common.credentials()
    audit = audit_request(key, salt)
    DIRECTORY.mkdir()
    common.write_json(DIRECTORY / "request_audit.json", audit, [key, salt])
    read_once("verify_payment", TXN, None, key, salt, "001_verify_payment.json")
    read_once("get_Transaction_Details", "2026-10-04", "2026-10-05", key, salt, "002_get_Transaction_Details.json")
    common.write_json(DIRECTORY / "manifest.json", {"schema_version": 1, "members": [
        {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(DIRECTORY.glob("*.json"))],
        "read_only_requests": 2, "provider_mutations_by_agent": 0, "financial_retries": 0, "new_txnids_generated": 0,
        "raw_sensitive_payloads_persisted": False}, [key, salt])


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--verify", action="store_true")
        parser.add_argument("--collect", action="store_true")
        args = parser.parse_args()
        if args.verify == args.collect:
            raise ValueError("Choose offline verification or explicitly authorized exact read collection")
        verify() if args.verify else collect()
    except Exception as error:
        print("Pass 0.9 diagnosis stopped safely: " + type(error).__name__)
        sys.exit(1)
