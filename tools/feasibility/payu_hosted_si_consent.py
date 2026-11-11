"""Pass 0.9 only: one human-launched Test consent, sanitized callback reception.

No provider payment HTTP client, card entry, debit, notification or retry exists.
The initial local page contains no merchant credentials or customer fields.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import secrets
import sys
import urllib.parse
import urllib.request
from uuid import uuid4

import payu_testmode_probe as common

EVIDENCE = common.ROOT / "docs/evidence/payu_hosted_si/v1"
PAYMENT = "https://test.payu.in/_payment"
PHONE_SOURCE = "https://docs.payu.in/docs/payu-hosted-integration-subscriptions"
PORT = 8770
LIMIT = 262144


def local_day():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


def make_contract(client_date, origin, txnid):
    if not re.fullmatch(r"p09[a-f0-9]{22}", txnid):
        raise ValueError("Fresh Pass 0.9 merchant identity required")
    if not re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", origin):
        raise ValueError("Explicit temporary HTTPS origin required")
    day = date.fromisoformat(client_date)
    details = {"billingAmount": "60.00", "billingCurrency": "INR", "billingCycle": "ADHOC",
               "billingInterval": 1, "paymentStartDate": str(day + timedelta(days=3)),
               "paymentEndDate": str(day + timedelta(days=7))}
    return {"method": "POST", "url": PAYMENT, "txnid": txnid, "amount": "2.00",
        "productinfo": "Pass09 Direct Card ADHOC Test", "api_version": "7", "si": "1",
        "free_trial": "1", "pg": "CC", "enforce_paymethod": "creditcard",
        "surl": origin + "/callbacks/payu/pass09/" + txnid + "/success",
        "furl": origin + "/callbacks/payu/pass09/" + txnid + "/failure",
        "si_details": json.dumps(details, separators=(",", ":"))}


def validate_contract(contract):
    details = json.loads(contract["si_details"])
    start = date.fromisoformat(details["paymentStartDate"])
    expected = make_contract(str(start - timedelta(days=3)),
        contract["surl"].split("/callbacks/")[0], contract["txnid"])
    if contract != expected:
        raise ValueError("Consent contract differs from the one authorized experiment")


def request_fields(contract, key, salt, identity):
    validate_contract(contract)
    fields = {name: value for name, value in contract.items() if name not in ("method", "url")}
    fields.update(key=key, **identity)
    for number in range(1, 6):
        fields["udf" + str(number)] = ""
    # Exact Hosted Subscription formula: five UDFs and five reserved empty slots.
    sequence = [fields[name] for name in ("key", "txnid", "amount", "productinfo", "firstname", "email")]
    sequence += [fields["udf" + str(number)] for number in range(1, 6)] + [""] * 5
    sequence += [fields["si_details"], salt]
    fields["hash"] = hashlib.sha512("|".join(sequence).encode()).hexdigest()
    return fields


def reverse_digest(fields, salt):
    # PayU's documented field hash, not an HMAC of the exact raw body.
    sequence = [salt, fields["status"]]
    if fields.get("splitInfo"):
        sequence += [fields["splitInfo"]]
    sequence += [""] * 5 + [fields.get("udf" + str(i), "") for i in range(5, 0, -1)]
    sequence += [fields[name] for name in ("email", "firstname", "productinfo", "amount", "txnid", "key")]
    extras = [fields[name] for name in ("additionalCharges", "additional_charges") if name in fields]
    if len(extras) == 2 and extras[0] != extras[1]:
        raise ValueError("Conflicting additional charge fields")
    if extras:
        sequence.insert(0, extras[0])
    return hashlib.sha512("|".join(sequence).encode()).hexdigest()


def decode_form(raw):
    pairs = urllib.parse.parse_qsl(raw.decode("utf-8"), keep_blank_values=True,
                                  strict_parsing=True, max_num_fields=150)
    if len({name for name, _ in pairs}) != len(pairs):
        raise ValueError("Duplicate form fields")
    return dict(pairs)


def callback_projection(raw, expected, salt):
    result = {"received_at": common.utc(), "raw_body_sha256": hashlib.sha256(raw).hexdigest(),
              "raw_body_bytes": len(raw), "raw_body_persisted": False,
              "integrity_verified": False, "canonical_verification_pending": True,
              "unsigned_fields_require_api_correlation": True}
    try:
        fields = decode_form(raw)
        digest = reverse_digest(fields, salt)
        supplied = fields.get("hash", "")
        valid = bool(re.fullmatch(r"[a-fA-F0-9]{128}", supplied)) and hmac.compare_digest(digest, supplied.lower())
        bound = all(hmac.compare_digest(fields.get(name, ""), expected[name]) for name in
                    ("key", "txnid", "amount", "productinfo", "firstname", "email", "udf1", "udf2", "udf3", "udf4", "udf5"))
        if not valid or not bound:
            return result
        result["integrity_verified"] = True
        result["merchant_request_binding_verified"] = True
        for name, allowed in {"status": {"success", "failure", "failed", "pending"},
            "unmappedstatus": {"captured", "auth", "failed", "pending", "dropped", "bounced", "usercancelled"},
            "mode": {"CC", "DC", "NB", "UPI"}, "payment_source": {"sist", "payu"}}.items():
            if fields.get(name) in allowed:
                result[name] = fields[name]
        if re.fullmatch(r"[0-9]{1,40}", fields.get("mihpayid", "")):
            result["mihpayid"] = fields["mihpayid"]  # Received reference; not yet canonical.
        result["txnid"] = expected["txnid"]
        result["amount"] = fields["amount"]
        result["card_token_present"] = bool(fields.get("cardToken"))
        result["unexpected_additional_charges_present"] = any(
            fields.get(name) not in (None, "", "0", "0.00") for name in ("additionalCharges", "additional_charges"))
        diagnostic = " ".join(fields.get(name, "").lower() for name in ("error", "error_Message", "error_message", "field9"))
        result["diagnostic_fragments"] = [item for item in common.FRAGMENTS if item.lower() in diagnostic]
        result["four_card_si_response_conditions_observed"] = (fields.get("status") == "success"
            and fields.get("payment_source") == "sist" and result["card_token_present"] and "mihpayid" in result)
    except (ValueError, UnicodeError, KeyError, TypeError):
        pass
    return result


def consume_launch(directory, txnid):
    # Exclusive creation and fsync precede releasing the provider form.
    # A lost response is UNKNOWN, not permission to issue the form again.
    common.write_json(directory / "launch_consumed.json", {"txnid": txnid,
        "consumed_at": common.utc(), "state": "OUTBOUND_POSSIBLE_NO_RETRY",
        "provider_submission_observed": False, "automatic_retries": 0})


class ConsentServer(HTTPServer):
    def __init__(self, port, directory, contract, fields, salt, synthetic=False):
        self.directory, self.contract, self.fields, self.salt = directory, contract, fields, salt
        self.synthetic = synthetic
        self.launch_nonce = secrets.token_hex(32)
        self.sensitive = [fields["key"], salt, fields["hash"], fields["firstname"], fields["email"], fields["phone"], self.launch_nonce]
        super().__init__(("127.0.0.1", port), Handler)

    def handle_error(self, request, client_address):
        pass  # Never log callback bytes or exception values.


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, code, content="", kind="text/html; charset=utf-8", csp=None, referrer_policy="no-referrer"):
        data = content.encode()
        self.send_response(code)
        for name, value in (("Content-Type", kind), ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"), ("Referrer-Policy", referrer_policy),
            ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", csp or "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def local_only(self):
        return self.headers.get("Host") == "127.0.0.1:" + str(self.server.server_port) and not any(
            self.headers.get(name) for name in ("Cf-Ray", "Cf-Connecting-Ip", "X-Forwarded-For", "X-Forwarded-Proto", "Forwarded"))

    def launch_audit(self, status, reason, nonce_valid=None):
        origin = self.headers.get("Origin")
        expected = "http://127.0.0.1:" + str(self.server.server_port)
        event = {"schema_version": 1, "observed_at": common.utc(),
            "provenance": "OFFLINE_SYNTHETIC" if self.server.synthetic else "LOCAL_LAUNCH_HANDLER",
            "method": self.command, "route": "/consent/launch", "http_status": status,
            "reason": reason, "local_host_and_proxy_check_passed": self.local_only(),
            "origin_class": "ABSENT" if origin is None else (
                "NULL" if origin == "null" else "SAME_ORIGIN" if origin == expected else "OTHER"),
            "nonce_valid": nonce_valid,
            "launch_marker_exists": (self.server.directory / "launch_consumed.json").exists(),
            "provider_form_release_possible": (self.server.directory / "launch_consumed.json").exists(),
            "raw_request_headers_body_nonce": "OMITTED"}
        common.write_json(self.server.directory / ("local_launch_" + uuid4().hex + ".json"),
                          event, self.server.sensitive)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, "Pass 0.9 callback receiver ready", "text/plain")
            return
        if self.path == "/consent/launch":
            self.launch_audit(404, "GET_DOES_NOT_LAUNCH")
            self.reply(404, "Open the launch page and use its button. This GET did not launch consent.")
            return
        if self.path not in ("/", "/summary"):
            self.reply(404)
            return
        if not self.local_only():
            self.reply(403)
            return
        if self.path == "/summary":
            self.reply(200, json.dumps(self.server.contract), "application/json")
            return
        if (self.server.directory / "launch_consumed.json").exists():
            self.reply(409, "Consent launch already consumed. Do not retry. Await read-only recovery.")
            return
        rows = "".join("<li>" + escape(name) + ": " + escape(str(value)) + "</li>"
                       for name, value in self.server.contract.items())
        self.reply(200, "<!doctype html><meta charset=utf-8><title>Pass 0.9 PayU Test consent</title>"
            "<h1>One PayU Test Card consent</h1><ul>" + rows + "</ul>"
            "<p>Expected registration INR 2; mandate maximum INR 60. Hash generated server-side."
            " If PayU changes the amount, currency, Card rail or ADHOC terms, stop."
            " Do not reload, go back and submit again, or reuse the Dashboard link.</p>"
            "<form method=post action=/consent/launch><input type=hidden name=nonce value=\"" + self.server.launch_nonce
            + "\"><button type=submit>Continue to PayU Test</button></form>", referrer_policy="same-origin")

    def read_form(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            raise ValueError("Invalid framing")
        length = int(lengths[0])
        if not 0 < length <= LIMIT or self.headers.get_content_type() != "application/x-www-form-urlencoded":
            raise ValueError("Invalid body bounds or type")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Incomplete body")
        return raw

    def do_POST(self):
        try:
            self.receive()
        except (ValueError, UnicodeError, KeyError, TypeError):
            if self.path == "/consent/launch":
                self.launch_audit(400, "INVALID_LOCAL_FORM")
            self.reply(400, "Request rejected; no retry.")
        except OSError:
            if self.path == "/consent/launch":
                self.launch_audit(503, "LOCAL_IO_ERROR_OUTCOME_REQUIRES_MARKER_CHECK")
            self.reply(503, "Outcome unknown; do not retry.")

    def receive(self):
        if self.path == "/consent/launch":
            if not self.local_only():
                self.launch_audit(403, "LOCAL_HOST_OR_PROXY_REJECTED")
                self.reply(403)
                return
            if self.headers.get("Origin") != "http://127.0.0.1:" + str(self.server.server_port):
                self.launch_audit(403, "ORIGIN_REJECTED")
                self.reply(403)
                return
            posted = decode_form(self.read_form())
            if set(posted) != {"nonce"} or not hmac.compare_digest(posted["nonce"], self.server.launch_nonce):
                self.launch_audit(403, "NONCE_REJECTED", False)
                self.reply(403)
                return
            day = date.fromisoformat(json.loads(self.server.contract["si_details"])["paymentStartDate"]) - timedelta(days=3)
            if not self.server.synthetic and local_day() != day:
                self.launch_audit(409, "STALE_CLIENT_DATE", True)
                self.reply(409, "Prepared date is stale. Stop; no new transaction is prepared automatically.")
                return
            try:
                consume_launch(self.server.directory, self.server.contract["txnid"])
            except FileExistsError:
                self.launch_audit(409, "LAUNCH_ALREADY_CONSUMED", True)
                self.reply(409, "Launch consumed. Do not retry.")
                return
            nonce = secrets.token_hex(24)
            hidden = "".join('<input type="hidden" name="' + escape(name, quote=True) + '" value="'
                + escape(value, quote=True) + '">' for name, value in self.server.fields.items())
            self.launch_audit(200, "PREPARED_FORM_RELEASE_AUTHORIZED", True)
            self.reply(200, '<!doctype html><meta charset=utf-8><title>Continue to PayU Test</title>'
                '<p>Opening PayU Test. Do not reload or resubmit.</p><form id="payu" method="post" action="'
                + PAYMENT + '">' + hidden + '</form><script nonce="' + nonce
                + '">document.getElementById("payu").submit();</script>', csp="default-src 'none'; script-src 'nonce-"
                + nonce + "'; form-action https://test.payu.in; frame-ancestors 'none'; base-uri 'none'")
            return
        paths = {urllib.parse.urlsplit(self.server.contract[name]).path: name for name in ("surl", "furl")}
        if self.path not in paths:
            self.reply(404)
            return
        raw = self.read_form()
        projection = callback_projection(raw, self.server.fields, self.server.salt)
        projection.update(callback_route=paths[self.path], schema_version=1,
            provenance="OFFLINE_SYNTHETIC" if self.server.synthetic else "INBOUND_PAYU_FIELD_HASH_CHECKED_API_CORRELATION_PENDING")
        if not (self.server.directory / "launch_consumed.json").exists():
            projection["integrity_verified"] = False
            projection["launch_not_consumed"] = True
        previous = list(self.server.directory.glob("callback_*.json"))
        hashes = [json.loads(path.read_text(encoding="utf-8"))["raw_body_sha256"] for path in previous]
        projection["delivery_observation"] = "FIRST" if not previous else (
            "IDENTICAL_REDELIVERY" if projection["raw_body_sha256"] in hashes else "DIFFERENT_BODY_REQUIRES_CORRELATION")
        common.write_json(self.server.directory / ("callback_" + uuid4().hex + ".json"), projection, self.server.sensitive)
        self.reply(200 if projection["integrity_verified"] else 401,
            "Callback received; canonical verification pending. Do not repeat consent. No debit is authorized.")


def serve(origin, client_date):
    if date.fromisoformat(client_date) != local_day():
        raise ValueError("Explicit client date must be current in Asia/Calcutta")
    key, salt = common.credentials()
    path = EVIDENCE / "launch_plan.json"
    if path.exists():
        plan = json.loads(path.read_text(encoding="utf-8"))
        contract = plan["contract"]
        if contract["surl"].split("/callbacks/")[0] != origin:
            raise ValueError("Existing experiment origin differs; do not regenerate identity")
    else:
        contract = make_contract(client_date, origin, "p09" + uuid4().hex[:22])
    # Retrieve the currently published sample phone in memory; never embed its value in tracked files.
    request = urllib.request.Request(PHONE_SOURCE, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=25) as response:
        documentation = response.read(2_000_000).decode("utf-8")
    phone = re.search(r"phone=(\d{10})(?:&|\s)", documentation)
    if not phone:
        raise ValueError("Official Hosted Test phone sample unavailable")
    identity = {"firstname": "Test" + "Owner", "email": contract["txnid"] + "@example.com", "phone": phone[1]}
    fields = request_fields(contract, key, salt, identity)
    hash_fingerprint = hashlib.sha256(fields["hash"].encode()).hexdigest()
    if path.exists() and plan["request_hash_sha256"] != hash_fingerprint:
        raise ValueError("Prepared account or consent binding changed; no replacement permitted")
    if not path.exists():
        common.write_json(path, {"schema_version": 1, "prepared_at": common.utc(), "client_date": client_date,
            "timezone": "Asia/Calcutta", "stage": "READY_AWAITING_ONE_HUMAN_CONSENT",
            "contract": contract, "request_hash_generated": True,
            "request_hash_sha256": hash_fingerprint,
            "key_salt_customer_fields": "OMITTED", "test_identity_phone_source": PHONE_SOURCE,
            "provider_consent_requests_by_agent": 0, "automatic_retries": 0,
            "old_dashboard_link_used": False, "old_attempt_classification": "C_UNKNOWN",
            "pre_debit_authorized": False, "subsequent_debit_authorized": False},
            [key, salt, fields["hash"], *identity.values()])
    with ConsentServer(PORT, EVIDENCE, contract, fields, salt) as server:
        print("Pass 0.9 receiver ready on loopback port " + str(PORT) + "; one human launch only", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--serve", action="store_true")
        parser.add_argument("--public-origin", required=True)
        parser.add_argument("--client-date", required=True)
        args = parser.parse_args()
        if not args.serve:
            raise ValueError("Explicit server mode required")
        serve(args.public_origin, args.client_date)
    except Exception as error:
        print("Pass 0.9 stopped safely: " + type(error).__name__, flush=True)
        sys.exit(1)
