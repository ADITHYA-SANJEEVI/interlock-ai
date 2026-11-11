"""Offline Pass 0.9 safety, hashing, callback and immutable-checkout verification."""
import hashlib
from html.parser import HTMLParser
import http.client
import json
from pathlib import Path
import tempfile
import threading
import urllib.parse

import payu_hosted_si_consent as p


class Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            fields = dict(attrs)
            self.values[fields["name"]] = fields.get("value", "")


def self_test():
    txnid = "p09" + "a" * 22
    contract = p.make_contract("2026-10-05", "https://offline-fixture.trycloudflare.com", txnid)
    key, salt = "PRIVATE_KEY_SENTINEL", "PRIVATE_SALT_SENTINEL"
    identity = {"firstname": "PRIVATE_NAME_SENTINEL", "email": "PRIVATE_EMAIL_SENTINEL", "phone": "PRIVATE_PHONE_SENTINEL"}
    fields = p.request_fields(contract, key, salt, identity)
    # An independently written literal sequence catches separator mistakes.
    literal = key + "|" + txnid + "|2.00|Pass09 Direct Card ADHOC Test|" + identity["firstname"] + "|" + identity["email"] + "|||||||||||" + contract["si_details"] + "|" + salt
    assert fields["hash"] == hashlib.sha512(literal.encode()).hexdigest()
    assert not set(fields) & {"ccnum", "ccvv", "ccexpmon", "ccexpyr", "cardToken", "store_card_token", "authpayuid", "txn_s2s_flow"}
    for name, changed in (("url", "https://secure.payu.in/_payment"), ("amount", "60.00"),
                          ("si", "0"), ("free_trial", "0"), ("pg", "UPI"), ("enforce_paymethod", "netbanking")):
        bad = dict(contract, **{name: changed})
        try:
            p.request_fields(bad, key, salt, identity)
        except ValueError:
            pass
        else:
            raise AssertionError("Changed financial contract admitted")
    callback = {name: fields[name] for name in ("key", "txnid", "amount", "productinfo", "firstname", "email", "udf1", "udf2", "udf3", "udf4", "udf5")}
    callback.update(status="success", unmappedstatus="captured", payment_source="sist", mode="CC",
                    mihpayid="123456789", cardToken="PRIVATE_TOKEN_SENTINEL", cardnum="PRIVATE_CARD_SENTINEL")
    reverse = salt + "|success|||||||||||" + identity["email"] + "|" + identity["firstname"] + "|Pass09 Direct Card ADHOC Test|2.00|" + txnid + "|" + key
    callback["hash"] = hashlib.sha512(reverse.encode()).hexdigest()
    assert callback["hash"] == p.reverse_digest(callback, salt)
    raw = urllib.parse.urlencode(callback).encode()
    result = p.callback_projection(raw, fields, salt)
    assert result["integrity_verified"] and result["four_card_si_response_conditions_observed"]
    assert result["canonical_verification_pending"] and result["unsigned_fields_require_api_correlation"]
    assert "PRIVATE" not in json.dumps(result) and callback["hash"] not in json.dumps(result)
    tampered = dict(callback, status="failure")
    assert not p.callback_projection(urllib.parse.urlencode(tampered).encode(), fields, salt)["integrity_verified"]
    unsigned = dict(callback, mihpayid="987654321", cardToken="")
    result = p.callback_projection(urllib.parse.urlencode(unsigned).encode(), fields, salt)
    assert result["integrity_verified"] and result["canonical_verification_pending"]
    assert not result["four_card_si_response_conditions_observed"]
    assert not p.callback_projection(raw + b"&status=success", fields, salt)["integrity_verified"]
    charged = dict(callback, additionalCharges="1.00")
    charged["hash"] = hashlib.sha512(("1.00|" + reverse).encode()).hexdigest()
    assert p.callback_projection(urllib.parse.urlencode(charged).encode(), fields, salt)["unexpected_additional_charges_present"]
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        server = p.ConsentServer(0, directory, contract, fields, salt, synthetic=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = "http://127.0.0.1:" + str(server.server_port)

        def request(method, path, body=None, headers=None, with_headers=False):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            content = response.read().decode()
            status = response.status
            received_headers = dict(response.getheaders())
            connection.close()
            return (status, content, received_headers) if with_headers else (status, content)

        try:
            assert request("GET", "/", headers={"Host": "offline-fixture.trycloudflare.com"})[0] == 403
            assert request("GET", "/", headers={"Cf-Ray": "synthetic"})[0] == 403
            status, page, page_headers = request("GET", "/", with_headers=True)
            assert status == 200 and all(value not in page for value in (key, salt, fields["hash"], *identity.values()))
            # Native form POST retains local Origin under same-origin policy.
            # no-referrer previously produced Origin:null and a false CSRF denial.
            assert page_headers["Referrer-Policy"] == "same-origin"
            parsed = Inputs()
            parsed.feed(page)
            body = urllib.parse.urlencode(parsed.values)
            headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": origin}
            assert request("POST", "/consent/launch", body, {"Content-Type": headers["Content-Type"]})[0] == 403
            for origin_value in ("null", "https://untrusted.invalid"):
                assert request("POST", "/consent/launch", body,
                    {"Content-Type": headers["Content-Type"], "Origin": origin_value})[0] == 403
            assert request("POST", "/consent/launch", "nonce=incorrect", headers)[0] == 403
            assert request("GET", "/consent/launch")[0] == 404
            assert not (directory / "launch_consumed.json").exists()
            audits = [json.loads(path.read_text()) for path in directory.glob("local_launch_*.json")]
            assert any(row["origin_class"] == "NULL" and row["reason"] == "ORIGIN_REJECTED" for row in audits)
            assert all(not row["provider_form_release_possible"] for row in audits)
            status, released, release_headers = request("POST", "/consent/launch", body, headers, with_headers=True)
            assert status == 200 and (directory / "launch_consumed.json").exists()
            assert release_headers["Referrer-Policy"] == "no-referrer"
            outgoing = Inputs()
            outgoing.feed(released)
            assert outgoing.values == fields and salt not in released
            assert request("POST", "/consent/launch", body, headers)[0] == 409
            assert request("GET", "/")[0] == 409
            try:
                p.consume_launch(directory, txnid)  # Survives restart/lost-response ambiguity.
            except FileExistsError:
                pass
            else:
                raise AssertionError("Second launch released")
            path = urllib.parse.urlsplit(contract["surl"]).path
            for _ in range(2):
                assert request("POST", path, raw, {"Content-Type": headers["Content-Type"]})[0] == 200
            receipts = [json.loads(path.read_text()) for path in directory.glob("callback_*.json")]
            assert {row["delivery_observation"] for row in receipts} == {"FIRST", "IDENTICAL_REDELIVERY"}
            assert all(row["provenance"] == "OFFLINE_SYNTHETIC" for row in receipts)
            assert request("POST", path, urllib.parse.urlencode(tampered), {"Content-Type": headers["Content-Type"]})[0] == 401
            saved = "".join(path.read_text() for path in directory.glob("*.json"))
            assert "PRIVATE" not in saved and fields["hash"] not in saved and callback["hash"] not in saved
            assert server.launch_nonce not in saved
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("Pass 0.9 offline harness checks PASS: financial guards; independently checked request/reverse hashing; one durable launch; local-only/CSRF guard; callback tamper/duplicate/token exclusion; unsigned-field limitations")


def evidence_test():
    path = p.EVIDENCE / "boundary_manifest.json"
    if not path.exists():
        return
    boundary = json.loads(path.read_text(encoding="utf-8"))
    for item in boundary["pre_existing_tracked_files"] + boundary["new_artifacts"]:
        assert hashlib.sha256((p.common.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    plan = json.loads((p.EVIDENCE / "launch_plan.json").read_text(encoding="utf-8"))
    p.validate_contract(plan["contract"])
    assert plan["stage"] == "READY_AWAITING_ONE_HUMAN_CONSENT"
    assert plan["provider_consent_requests_by_agent"] == plan["automatic_retries"] == 0
    assert not any(plan[name] for name in ("old_dashboard_link_used", "pre_debit_authorized", "subsequent_debit_authorized"))
    assert plan["old_attempt_classification"] == "C_UNKNOWN" and plan["request_hash_generated"]
    marker = json.loads((p.EVIDENCE / "launch_consumed.json").read_text(encoding="utf-8"))
    assert marker["txnid"] == plan["contract"]["txnid"] and marker["state"] == "OUTBOUND_POSSIBLE_NO_RETRY"
    audits = [json.loads(path.read_text()) for path in p.EVIDENCE.glob("local_launch_*.json")]
    releases = [row for row in audits if row["reason"] == "PREPARED_FORM_RELEASE_AUTHORIZED"]
    assert len(releases) == 1
    assert releases[0]["http_status"] == 200 and releases[0]["method"] == "POST"
    assert releases[0]["nonce_valid"] and releases[0]["origin_class"] == "SAME_ORIGIN"
    assert releases[0]["launch_marker_exists"] and releases[0]["provider_form_release_possible"]
    health = json.loads((p.EVIDENCE / "handoff_health.json").read_text(encoding="utf-8"))
    assert health["checks"]["local_health"]["http_status"] == health["checks"]["public_health"]["http_status"] == 200
    assert health["checks"]["public_launch_denied"]["http_status"] == 403
    assert not health["human_launch_consumed"] and health["provider_payment_calls_by_agent"] == 0
    recovery = json.loads((p.EVIDENCE / "local_launch_403/pre_fix_inspection.json").read_text(encoding="utf-8"))
    assert recovery["classification"] == "LOCAL_PRE_PROVIDER_FAILURE" and recovery["payu_request_escaped"] == "NO"
    assert recovery["same_txnid_reuse_safe"] and not recovery["human_provider_consent_authorization_consumed"]
    assert recovery["txnid"] == plan["contract"]["txnid"]
    snapshot = p.EVIDENCE / "local_launch_403/harness_before_fix.py.txt"
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == recovery["source_sha256_before_fix"]
    mutable = {"tools/feasibility/payu_hosted_si_consent.py", "tools/feasibility/verify_payu_hosted_si.py",
               "docs/PASS_0_9_PAYU_HOSTED_SI_FEASIBILITY.md"}
    for item in recovery["previous_artifact_hashes"]:
        if item["file"] not in mutable:
            assert hashlib.sha256((p.common.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    fixed = json.loads((p.EVIDENCE / "local_launch_403/fixed_handoff.json").read_text(encoding="utf-8"))
    assert fixed["payu_request_escaped"] == "NO" and fixed["contract"] == plan["contract"]
    assert fixed["same_txnid_retained"] == plan["contract"]["txnid"]
    assert fixed["checks"]["initial_referrer_policy"] == "same-origin"
    assert fixed["checks"]["same_launch_contract"] and fixed["checks"]["initial_provider_form_absent"]
    assert fixed["checks"]["local_health"]["http_status"] == fixed["checks"]["public_health"]["http_status"] == 200
    assert fixed["checks"]["public_launch_denied"]["http_status"] == 403
    assert not fixed["consumed_marker_exists"] and fixed["callback_count"] == 0
    assert not fixed["one_consent_authorization_consumed"] and not fixed["tunnel_changed"]
    diagnosis = json.loads((p.EVIDENCE / "diagnosis/assessment.json").read_text())
    assert diagnosis["diagnosis_category"] == "UNKNOWN_PROVIDER_REJECTION"
    assert diagnosis["canonical_classification"] == "B_FAILED_TRANSACTION_RECORD"
    assert diagnosis["provider_request_escaped"] == "YES" and not diagnosis["old_txnid_reuse_allowed"]
    assert diagnosis["callback_observations"]["receipt_count"] == len(list(p.EVIDENCE.glob("callback_*.json"))) == 0
    assert not diagnosis["merchant_feature_gate_proven"] and not diagnosis["root_cause_established"]
    assert not diagnosis["new_experiment_ready"] and diagnosis["new_txnids_generated"] == 0
    directory = p.EVIDENCE / "diagnosis"
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["read_only_requests"] == 2 and manifest["provider_mutations_by_agent"] == 0
    for item in manifest["members"]:
        assert hashlib.sha256((directory / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    verify = json.loads((directory / "001_verify_payment.json").read_text())
    extraction = json.loads((directory / "002_get_Transaction_Details.json").read_text())
    for record, command in ((verify, "verify_payment"), (extraction, "get_Transaction_Details")):
        assert record["command"] == command and record["read_only"] and record["test_only"]
        assert record["request"]["url"] == "https://test.payu.in/merchant/postservice.php?form=2"
        assert record["http_status"] == 200 and record["response"]["business_status"] == 1
        assert not record["automatic_retries"] and not record["redirects_followed"]
        assert record["response"]["exact_transaction_match_count"] == 1
    assert verify["request"]["var1"] == plan["contract"]["txnid"]
    assert (extraction["request"]["var1"], extraction["request"]["var2"]) == ("2026-10-04", "2026-10-05")
    first, second = (record["response"]["exact_transaction_records"][0] for record in (verify, extraction))
    assert first["txnid"] == second["txnid"] == plan["contract"]["txnid"]
    assert first["mihpayid"] == second["id"] == "613345778913332436"
    assert first["status"] == "failure" and first["unmappedstatus"] == second["status"] == "failed"
    assert first["error_code"] == second["error_code"] == "E501"
    assert first["amt"] == second["amount"] == "2.00" and second["returned_merchant_key_matches"]
    audit = json.loads((directory / "request_audit.json").read_text())
    assert all(audit["checks"].values()) and audit["contract"] == plan["contract"]
    assert audit["prepared_request_hash_sha256"] == plan["request_hash_sha256"]
    assert not audit["actual_browser_wire_body_preserved"]
    key, salt = p.common.credentials()
    files = [p.common.ROOT / item["file"] for item in boundary["new_artifacts"]]
    for path in files:
        content = path.read_text(encoding="utf-8")
        assert key not in content and salt not in content
        if path.suffix == ".json":
            value = json.loads(content)

            def check(data):
                if isinstance(data, dict):
                    assert not set(data) & {"key", "salt", "hash", "firstname", "email", "phone", "cardToken", "card_token", "ccnum", "ccvv", "otp"}
                    for child in data.values():
                        check(child)
                elif isinstance(data, list):
                    for child in data:
                        check(child)
            check(value)
    print("Pass 0.9 diagnosis evidence PASS: one human release; two Test reads; correlated failed E501 record; unknown root cause; old UNKNOWN untouched; original checkout hashes; secret/customer exclusion")


if __name__ == "__main__":
    self_test()
    evidence_test()
