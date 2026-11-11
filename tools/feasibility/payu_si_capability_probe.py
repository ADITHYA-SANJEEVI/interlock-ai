"""Pass 0.8 timeboxed SI discovery: empty inputs only; never a valid debit.

Reuses ignored-Test credentials and atomic sanitized writes, not payment tools.
"""
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

import payu_testmode_probe as common

EVIDENCE = common.ROOT / "docs/evidence/payu_si_testmode/v1"
URL = "https://test.payu.in/merchant/postservice.php?form=2"
COMMANDS = ("si_transaction", "check_mandate_status", "pre_debit_SI")
COLLECTION_CLOSED = True
WORDS = common.SAFE_WORDS | set("""recurring subscriptions subscription si autopay
    enach mandate mandates authpayuid consent mandated use payu api enabled
    disabled inactive inactivated deactivated blacklisted blocked enable
    activate activation register registered registration unsupported request
    processed notified invoice invoiceid empty billing rule cycle adhoc max
    transactionid authpayuid auth_payuid auth end start payer bank notification
    notifications predebit debit is not does exists found active paused revoked
    cancelled unsuccessful successfully error internal mandatory required
    activity allowed authorised authorize authorised merchant_key invalid
    authorised authorized transaction enabled consent date no only none failed""".split())


def prepare(command, key, salt, fixture=None):
    empty_fields = {"authpayuid": "", "amount": "", "txnid": "", "invoiceDisplayNumber": ""}
    readonly_fixture = command == "check_mandate_status" and isinstance(fixture, dict) and \
        set(fixture) == {"authPayuId", "requestId"} and fixture["authPayuId"] == "" and \
        re.fullmatch(r"pass08_[0-9a-f]{32}", str(fixture["requestId"]))
    if command not in COMMANDS or fixture not in (None, {}) and not (
            command == "si_transaction" and fixture == empty_fields or readonly_fixture):
        raise RuntimeError("Only documented commands with no executable fields allowed")
    # No usable mandate reference, amount, txnid, invoice, customer or debit date.
    var1 = json.dumps(fixture or {}, separators=(",", ":"))
    digest = hashlib.sha512("|".join((key, command, var1, salt)).encode()).hexdigest()
    return urllib.parse.urlencode({"key": key, "command": command, "var1": var1,
                                  "hash": digest}).encode(), digest


def project(payload, sensitive):
    if not isinstance(payload, dict):
        return {"json_object": False, "category": "D_INCONCLUSIVE"}
    result = {"json_object": True}
    for name in ("status", "code", "errorCode", "error_code"):
        value = payload.get(name)
        if type(value) in (int, bool) or isinstance(value, str) and re.fullmatch(
                r"(?:[0-9]{1,3}|ER[0-9]{4}|E[0-9]{4}|QC|active|failed|error)", value):
            result[name] = value
    message_fields = ("msg", "message", "errorMessage", "error", "error_description", "field9")
    messages = [payload.get(name) for name in message_fields]
    # An outer status=1 can wrap a failed debit; copy diagnostics, never map IDs.
    details = payload.get("details")
    inner_statuses = []
    if isinstance(details, dict):
        for detail in details.values():
            if isinstance(detail, dict):
                messages.extend(detail.get(name) for name in message_fields)
                status = detail.get("status")
                if status in ("failed", "failure", "captured", "pending", "in progress"):
                    inner_statuses.append(status)
    if inner_statuses:
        result["inner_statuses"] = inner_statuses
    messages = [value for value in messages if isinstance(value, str)]
    safe_messages = []
    for message in messages:
        value = message.lower()
        for secret in sensitive:
            value = value.replace(secret.lower(), "[omitted]")
        words = re.findall(r"[a-z_]+|[0-9]+", value)[:80]
        safe_messages.append(" ".join(word if word in WORDS else "[omitted]" for word in words))
    result["message_templates"] = safe_messages
    text = " ".join(messages).lower()
    if ("merchant" in text or "recurring" in text or "subscription" in text) and any(
        phrase in text for phrase in ("not enabled", "not authorized", "not authorised",
            "not allowed", "not activated", "disabled", "inactive", "not active",
            "deactivated", "blacklisted", "blocked", "invalid merchant")):
        category = "A_MERCHANT_OR_FEATURE_GATED"
    elif any(phrase in text for phrase in ("invalid hash", "invalid key", "mandatory",
            "missing", "invalid parameter", "invalid data", "empty", "parameter error")):
        category = "C_MALFORMED_OR_AUTHENTICATION_FAILURE"
    elif any(phrase in text for phrase in ("mandate entry not found", "si is not valid",
            "mandate is not active", "consent is not mandated", "transaction not exists")):
        category = "B_MANDATE_MISSING_OR_INACTIVE_ENTITLEMENT_NOT_PROVEN"
    else:
        category = "D_INCONCLUSIVE"
    result["category"] = category
    return result


def verify():
    """Offline verification only; never opens a network connection."""
    key, salt = common.credentials()
    records = []
    for path in EVIDENCE.glob("*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["payable_requests"] == manifest["registrations"] == 0
        for member in manifest["members"]:
            artifact = path.parent / member["file"]
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == member["sha256"]
            record = json.loads(artifact.read_text(encoding="utf-8"))
            assert record["http_status"] == 200 and record["non_executable"]
            assert not record["redirects_followed"] and record["automatic_retries"] == 0
            assert record["request"]["url"] == URL
            fixture = record["request"]["var1"]
            prepare(record["command"], "OFFLINE_KEY", "OFFLINE_SALT", fixture)
            assert record["request"]["credentials_authentication_headers"] == "OMITTED"
            records.append(record)
    assert len(records) == 6
    # Verdict correction must not rewrite the original HTTP evidence or imply
    # that validation/UI existence establishes subsequent debit entitlement.
    correction = json.loads((EVIDENCE / "master_correction.json").read_text(encoding="utf-8"))
    assessment = json.loads((EVIDENCE / "assessment.json").read_text(encoding="utf-8"))
    readiness = json.loads((EVIDENCE / "adhoc_registration_readiness.json").read_text(encoding="utf-8"))
    authorization = json.loads((EVIDENCE / "registration_authorization.json").read_text(encoding="utf-8"))
    assert not correction["previous_conclusion_supported"]
    assert correction["historical_assessment"]["provider_merchant_gating_observed"] is False
    assert readiness["previous_assessment"]["verdict"] == correction["corrected_conclusion"] == (
        "CONDITIONAL — SELF-SERVICE REGISTRATION EXISTS; SUBSEQUENT-DEBIT ENTITLEMENT NOT YET PROVED")
    recovery = json.loads((EVIDENCE / "registration_recovery_assessment.json").read_text(encoding="utf-8"))
    assert recovery["previous_assessment"]["verdict"] == readiness["verdict"] == "READY FOR ONE TEST MANDATE REGISTRATION"
    assert recovery["classification"] == "C" and assessment["verdict"] == recovery["verdict"] == "C \u2014 UNKNOWN"
    assert correction["additional_provider_calls_this_continuation"] == 0
    assert not assessment["mandate_path_closed_for_current_account"]
    assert assessment["self_service_registration_ui_exists"]
    assert not assessment["registration_ready"] and not assessment["account_entitlement_proven"]
    assert assessment["registration_form_inspected"] and assessment["dashboard_adhoc_observed"]
    assert not assessment["registration_submission_authorized"] and not assessment["registration_retry_authorized"]
    assert assessment["registration_stage"] == "UNKNOWN_NO_RETRY"
    assert recovery["registration_operation_escaped"] == assessment["registration_operation_escaped"] == "UNKNOWN"
    assert recovery["human_link_count"] == recovery["human_registration_initiation_clicks"] == 1
    assert not recovery["link_active_is_mandate_active"] and recovery["mandate_status"] == "UNKNOWN"
    assert not any(recovery[name] for name in ("card_or_afa_reached", "account_gating_observed",
        "account_entitlement_proven", "registration_retry_authorized", "registration_retries",
        "new_mutation_requests", "agent_registration_submissions", "agent_browser_actions",
        "notifications", "debits", "live_requests", "raw_payloads_persisted", "pass_0j_started", "pass_1_started"))
    assert not recovery["canonical_records_found"]
    assert [row["step"] for row in recovery["chronology"]] == list(range(1, 11))
    assert recovery["attempt_time"]["approximate_utc"] == "2026-10-04T18:05:00Z"
    assert recovery["hosted_consent"]["start_date"] == "2026-10-07"
    assert not recovery["hosted_consent"]["registered_validity_proven"]
    assert recovery["read_only_requests"] == recovery["additional_provider_calls_this_recovery"] == 1
    query = recovery["read_only_queries"][0]
    receipt_path = common.ROOT / query["evidence_file"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    run_manifest = json.loads((receipt_path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert len(run_manifest["members"]) == 1 and run_manifest["read_only_requests"] == 1
    member = run_manifest["members"][0]
    assert member["file"] == receipt_path.name
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == member["sha256"]
    assert not any(run_manifest[name] for name in ("mutations", "registrations", "notifications", "debits", "raw_payloads_persisted"))
    assert receipt["command"] == "get_Transaction_Details" and receipt["request"]["url"] == URL
    assert receipt["read_only"] and receipt["test_only"] and not receipt["redirects_followed"]
    assert receipt["automatic_retries"] == 0 and not receipt["identifiers_fabricated"]
    assert receipt["http_status"] == 200 and receipt["response"]["business_status"] == 1
    assert receipt["request"]["date_from"] == query["date_from"] == "2026-10-03"
    assert receipt["request"]["date_to"] == query["date_to"] == "2026-10-04"
    assert receipt["response"]["record_set_recognized"] and receipt["response"]["transaction_record_count"] == 0
    assert not receipt["response"]["registration_inventory_completeness_proven"]
    assert not receipt["response"]["provider_consistency_horizon_known"]
    assert authorization["max_subscription_links"] == authorization["max_registrations"] == 1
    assert authorization["registration_submission_authorized"]
    assert authorization["browser_actor"] == "HUMAN_ONLY"
    assert authorization["agent_submission_count"] == authorization["additional_provider_calls"] == 0
    assert authorization["expected_registration_amount_inr"] == "2.00"
    assert authorization["registered_ceiling_inr"] == "60.00"
    assert authorization["read_only_retrieval_authorized_after_human_registration"]
    assert not any(authorization[name] for name in ("valid_predebit_authorized",
        "debit_authorized", "modification_cancellation_refund_authorized",
        "second_link_or_registration_authorized", "ambiguous_registration_retry_authorized",
        "commit_created_this_stage"))
    assert [row["item"] for row in correction["evidence_matrix"]] == list(range(1, 10))
    assert correction["evidence_matrix"][5]["state"] == "not_observed"
    assert not any(correction[name] for name in (
        "registration_authorized", "valid_predebit_authorized", "debit_authorized"))
    mutable = {"docs/PASS_0_8_PAYU_SI_FEASIBILITY.md",
        "docs/evidence/payu_si_testmode/v1/assessment.json",
        "tools/feasibility/payu_si_capability_probe.py"}
    for item in correction["historical_artifact_hashes"]:
        if item["file"] not in mutable:
            assert hashlib.sha256((common.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    for item in readiness["previous_artifact_hashes"]:
        if item["file"] not in mutable:
            assert hashlib.sha256((common.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    for item in authorization["previous_artifact_hashes"]:
        if item["file"] not in mutable:
            assert hashlib.sha256((common.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    for item in recovery["previous_artifact_hashes"]:
        if item["file"] not in mutable:
            assert hashlib.sha256((common.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    assert readiness["additional_provider_calls_this_continuation"] == 0
    assert readiness["submitted_links"] == readiness["registered_mandates"] == 0
    assert not any(readiness[name] for name in ("registration_submission_authorized",
        "valid_predebit_authorized", "debit_authorized", "account_debit_entitlement_proven"))
    ui = readiness["dashboard_evidence"]
    assert "ADHOC" in ui["billing_cycles"] and "Cards" in ui["payment_methods"]
    assert not any(ui[name] for name in ("link_created", "mandate_registered",
        "customer_or_payment_data_entered", "financial_mutation_performed"))
    design = {item["field"]: item["value"] for item in readiness["design"]}
    assert design["Billing Type"] == "Recurring" and design["Billing cycle"] == "ADHOC"
    assert design["Billing Amount in INR"] == readiness["registered_ceiling_inr"] == "60.00"
    assert readiness["expected_registration_amount_inr"] == "2.00"
    assert design["No expiration"] == "Off" and readiness["chosen_rail"] == "CARD_STANDING_INSTRUCTION"
    start = datetime.strptime(design["Duration start date"], "%Y-%m-%d")
    end = datetime.strptime(design["Duration end date"], "%Y-%m-%d")
    assert (end - start).days == 7
    consumed = json.loads((EVIDENCE / "final_authorized_probe_consumed.json").read_text(encoding="utf-8"))
    assert COLLECTION_CLOSED and consumed["reexecution_prohibited"]
    blank = [record for record in records if record["command"] == "si_transaction"
             and record["request"]["var1"].get("authpayuid") == ""]
    assert len(blank) == 2 and blank[0]["raw_response_sha256"] == blank[1]["raw_response_sha256"]
    assert any(record["response"].get("inner_statuses") == ["failed"] for record in blank)
    for command in COMMANDS:
        body, _ = prepare(command, "OFFLINE_KEY", "OFFLINE_SALT")
        assert urllib.parse.parse_qs(body.decode())["var1"] == ["{}"]
    for fixture in ({"authpayuid": "forbidden"}, {"amount": "60"}, {"txnid": "forbidden"}):
        try:
            prepare("si_transaction", "OFFLINE_KEY", "OFFLINE_SALT", fixture)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Executable fields accepted")
    result = project({"status": 1, "message": "Transaction Processed successfully",
        "details": {"PRIVATE_REFERENCE": {"status": "failed", "field9":
            "Merchant is not enabled for recurring transactions", "cardToken": "PRIVATE_TOKEN"}}}, [])
    assert result["category"] == "A_MERCHANT_OR_FEATURE_GATED" and result["inner_statuses"] == ["failed"]
    assert "PRIVATE" not in json.dumps(result)
    boundary = json.loads((EVIDENCE / "boundary_manifest.json").read_text(encoding="utf-8"))
    for item in boundary["pre_existing_tracked_files"] + boundary["new_artifacts"]:
        path = common.ROOT / item["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    for path in list(EVIDENCE.rglob("*.json")) + [common.ROOT / "docs/PASS_0_8_PAYU_SI_FEASIBILITY.md", __file__]:
        from pathlib import Path
        content = Path(path).read_text(encoding="utf-8")
        assert key not in content and salt not in content
    print("Pass 0.8 offline verification PASS: 6 immutable HTTP records; preserved historical correction/readiness; one human initiation click; C UNKNOWN/no retry; one read-only empty extraction with completeness limits; separate debit/notification prohibitions; consumed malformed probe; credential exclusion; frozen-file hashes")


def main():
    if sys.argv[1:] == ["--verify"]:
        verify()
        return
    if COLLECTION_CLOSED:
        raise RuntimeError("Pass 0.8 provider collection is closed; offline verification only")
    key, salt = common.credentials()
    started = common.utc()
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12]
    directory = EVIDENCE / run
    members = []
    opener = urllib.request.build_opener(common.NoRedirect())
    reason = "Only non-executable validation observed; no registration readiness inferred"
    sensitive = [key, salt]
    fixture = {}
    commands = COMMANDS
    if sys.argv[1:] == ["--blank-required-fields"]:
        # One controlled nonempty validation fixture; every payable field stays blank.
        fixture = {"authpayuid": "", "amount": "", "txnid": "", "invoiceDisplayNumber": ""}
        commands = ("si_transaction",)
    elif sys.argv[1:] == ["--status-missing-reference"]:
        fixture = {"authPayuId": "", "requestId": "pass08_" + uuid4().hex}
        commands = ("check_mandate_status",)
    elif sys.argv[1:]:
        raise RuntimeError("Unknown validation scope")
    for command in commands:
        data, digest = prepare(command, key, salt, fixture)
        sensitive.append(digest)
        record = {"schema_version": 1, "command": command, "sent_at": common.utc(),
            "request": {"method": "POST", "url": URL, "body_field_names_only":
                ["command", "hash", "key", "var1"], "var1": fixture,
                "credentials_authentication_headers": "OMITTED"},
            "test_only": True, "non_executable": True, "redirects_followed": False,
            "automatic_retries": 0}
        request = urllib.request.Request(URL, data=data, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            record.update(http_status=None, response={"category": "D_INCONCLUSIVE"},
                          transport_error_class=type(error).__name__)
            response = None
        if response is not None:
            with response:
                raw = response.read(1_000_001)
                record.update(http_status=response.code, received_at=common.utc(),
                    raw_response_sha256=hashlib.sha256(raw).hexdigest(),
                    raw_response_bytes=len(raw), raw_response_complete=len(raw) <= 1_000_000)
                try:
                    payload = json.loads(raw) if len(raw) <= 1_000_000 else None
                except (ValueError, UnicodeError):
                    payload = None
                record["response"] = project(payload, sensitive)
        path = directory / f"{len(members)+1:03d}_{command}.json"
        common.write_json(path, record, sensitive)
        members.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        print(f"{command}: HTTP {record['http_status']}; {record['response']['category']}", flush=True)
        if record["response"]["category"] == "A_MERCHANT_OR_FEATURE_GATED":
            reason = "Definitive merchant/feature refusal; stop immediately; no other mode or provider"
            break
        if record["http_status"] is None or record["http_status"] in (401, 403) or record["http_status"] >= 500:
            reason = "HTTP/transport uncertainty; no automatic retry"
            break
    common.write_json(directory / "manifest.json", {"schema_version": 1,
        "started_at": started, "finished_at": common.utc(), "members": members,
        "stop_reason": reason, "payable_requests": 0, "registrations": 0,
        "authpayuid_supplied": False, "raw_payment_data_supplied": False,
        "real_customer_data_supplied": False, "test_only": True,
        "support_contacted": False, "registration_ready": False}, sensitive)
    print("Sanitized evidence: " + str(directory.relative_to(common.ROOT)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("SI discovery stopped safely: " + type(error).__name__, flush=True)
        sys.exit(1)
