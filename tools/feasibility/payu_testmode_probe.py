"""Pass 0.7 only: fixed, noncharging PayU Test discovery; no payment capability.

Credentials, authentication digests, raw bodies, cards, tokens and customer data
stay in memory. No redirect following or automatic request retries.
"""

from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/payu_testmode/v1"
SERVICE = "https://test.payu.in/merchant/postservice.php?form=2"
VERIFY = "https://test.payu.in/merchant/postservice?form=2"
INSTRUMENT = "https://apitest.payu.in/storecard/instrument/v1"
CLASSIC_PAYMENT = "https://test.payu.in/_payment"
V2_PAYMENT = "https://apitest.payu.in/v2/payments"
READ_COMMANDS = {
    "getNetbankingStatus", "get_checkout_details", "verify_payment",
    "get_user_cards", "get_Transaction_Details", "getAllRefundsFromTxnIds",
}
# Store only these literal diagnostic fragments, never provider free text.
FRAGMENTS = (
    "invalid key", "invalid merchant key", "invalid merchant id", "disabled", "invalid hash", "hash mismatch",
    "merchant not found", "merchant is not active", "merchant not active",
    "merchant is not enabled", "not enabled", "not activated", "access denied",
    "not authorized", "not authorised", "unauthorized", "unauthorised",
    "permission denied", "pci", "kyc", "card not found", "cards not found",
    "no cards", "no card", "cards fetched successfully", "success",
    "transaction not found", "transaction not exists", "transaction does not exist",
    "no transaction", "no refund", "invalid transaction", "invalid parameters",
    "missing", "required", "mandatory", "invalid request", "bad request",
    "unable to authenticate", "authentication failed", "not allowed",
    "not permitted", "forbidden", "bad gateway", "service unavailable",
    "user credentials", "user_credentials", "txnid", "txnId", "referenceId",
    "amount", "paymentmethod", "accountid", "cardtoken", "empty", "test",
)
GATES = {
    "merchant is not active", "merchant not active", "merchant is not enabled",
    "not enabled", "not activated", "access denied", "not authorized",
    "not authorised", "permission denied", "pci", "kyc", "not allowed",
    "not permitted", "disabled", "invalid merchant id",
}
AUTH_ERRORS = {
    "invalid key", "invalid merchant key", "invalid hash", "hash mismatch",
    "merchant not found", "unauthorized", "unauthorised",
    "unable to authenticate", "authentication failed",
}
SAFE_WORDS = set("""a an the is are be was were been not no to for from of in on at
    with and or all this that your user users credentials usercredentials
    user_credentials card cards saved payment payments instrument instruments
    token tokens merchant key account hash invalid empty missing mandatory
    required request response found does exist exists details fetched successfully
    successful success failed failure error errors internal server bad format
    parameters parameter field fields provided send please unable get fetching
    getting authentication authorization authenticate authorised authorized
    unauthorized unauthorised permission denied access allowed permitted enabled
    activated active pci certification compliant compliance kyc transaction
    transactions refund refunds data retrieved amount txnid referenceid accountid
    paymentmethod cardtoken test live environment service unavailable supported
    support feature vault input valid verify completed record records has have
    have any processing fetching usercards technical issue exception null
    requestid id date format value code message status detail wrong mismatch
    body resource forbidden process processing tokenization userdetails
    user_details csc errorcode description errordescription return returned
    exists nonexistent unregistered fetched succesfully amount currency
    merchantkey invalidkey invalidhash authenticated gateway credentials
    unexpected transaction_details something went wrong indicating supported disabled
    useridentifier firstname productinfo email phone""".split())


def utc():
    return datetime.now(timezone.utc).isoformat()


def credentials():
    path = ROOT / ".env"
    if not path.is_file() or subprocess.run(
        ["git", "check-ignore", "-q", "--", ".env"], cwd=ROOT,
        capture_output=True, check=False,
    ).returncode != 0:
        raise RuntimeError("Ignored local configuration is required")
    names = ("PAYU_KEY", "PAYU_SALT", "PAYU_ENV")
    parsed = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match or match[1] not in names:
            continue
        value = match[2]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        parsed[match[1]] = value
    # This task explicitly authorizes the ignored .env, not environment overrides.
    values = tuple(parsed.get(name, "") for name in names)
    if not all(values) or values[2] != "test":
        raise RuntimeError("Explicit Test configuration is required")
    return values[:2]


def write_json(path, value, sensitive=()):
    data = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if any(secret and secret in data for secret in sensitive):
        raise RuntimeError("Evidence exclusion failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def project(payload):
    """Allowlist structural evidence; never copy free text or nested card fields."""
    if not isinstance(payload, dict):
        return {"json_object": False}
    result = {"json_object": True}
    for name in ("status", "code", "errorCode", "statusCode"):
        value = payload.get(name)
        if type(value) in (int, bool):
            result[name] = value
        elif isinstance(value, str) and value.lower() in (
            "0", "1", "2", "success", "failure", "failed", "pending", "error",
            "invalid_key", "invalid_hash", "unauthorized", "forbidden",
        ):
            result[name] = value
    message_fields = ("msg", "message", "error", "error_message", "errorMessage", "errorDescription", "error_description", "description")
    messages = [payload.get(name) for name in message_fields]
    # Only standard shallow diagnostic envelopes; no traversal of business records.
    for name in ("error", "metaData", "result"):
        nested = payload.get(name)
        if isinstance(nested, dict):
            messages.extend(nested.get(field) for field in message_fields)
    strings = [item for item in messages if isinstance(item, str)]
    combined = " ".join(strings).lower()
    result["recognized_message_fragments"] = sorted({phrase.lower() for phrase in FRAGMENTS
                                                       if phrase.lower() in combined})
    result["message_present"] = bool(strings)
    result["message_word_templates"] = [" ".join(
        word if word in SAFE_WORDS else "[omitted]"
        for word in re.findall(r"[a-z_]+|[0-9]+", message.lower())[:60]
    ) for message in strings]
    # Token maps are deliberately counted without copying their keys or values.
    cards = payload.get("user_cards")
    nested = payload.get("result")
    if cards is None and isinstance(nested, dict):
        cards = nested.get("user_cards")
    if isinstance(cards, (dict, list)):
        result["saved_instrument_count"] = len(cards)
    details = payload.get("details")
    if isinstance(details, dict) and isinstance(details.get("paymentOptions"), dict):
        options = details["paymentOptions"]
        result["payment_option_groups_present"] = [name for name in (
            "cc", "dc", "nb", "upi", "emi", "cash", "bnpl", "wallet") if name in options]
        result["payment_option_presence_is_not_token_enablement"] = True
    for name in ("transaction_details", "Transaction_details", "transactionDetails", "transactions"):
        rows = payload.get(name)
        if isinstance(rows, (dict, list)):
            result["transaction_record_count"] = len(rows)
            # Only known statuses/counts; no provider or merchant IDs copied.
            iterable = rows.values() if isinstance(rows, dict) else rows
            result["transaction_failure_record_count"] = sum(
                1 for item in iterable if isinstance(item, dict)
                and str(item.get("status", "")).lower() in ("not found", "not_found", "failure", "failed"))
    gateway_rows = payload.get("result", payload)
    if isinstance(gateway_rows, dict):
        statuses = [str(row.get("up_status")) for row in gateway_rows.values()
                    if isinstance(row, dict) and "up_status" in row]
        if statuses:
            result["gateway_option_count"] = len(statuses)
            result["gateway_up_status_counts"] = {name: statuses.count(name) for name in ("0", "1", "3")}
    return result


class Probe:
    def __init__(self):
        self.key, self.salt = credentials()
        self.sensitive = [self.key, self.salt]
        self.fixture = "pass07_" + uuid4().hex
        self.owner = self.key + ":" + self.fixture
        self.sensitive += [self.owner, urllib.parse.quote(self.owner, safe="")]
        self.day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12]
        self.directory = EVIDENCE / self.run_id
        self.members = []
        self.started_at = utc()
        self.opener = urllib.request.build_opener(NoRedirect())

    def command_fields(self, command):
        if command not in READ_COMMANDS:
            raise RuntimeError("Read-only command allowlist violation")
        fields = {"key": self.key, "command": command}
        if command == "getNetbankingStatus":
            fields["var1"] = "default"
        elif command == "get_checkout_details":
            fields["var1"] = json.dumps({"requestId": self.fixture,
                "transactionDetails": {"amount": 60.00},
                "useCase": {"getExtendedPaymentDetails": True}}, separators=(",", ":"))
        elif command == "get_user_cards":
            fields["var1"] = self.owner
        elif command == "get_Transaction_Details":
            fields.update(var1=self.day, var2=self.day)
        else:
            fields["var1"] = self.fixture
        digest = hashlib.sha512("|".join((self.key, command, fields["var1"], self.salt)).encode()).hexdigest()
        self.sensitive.append(digest)
        fields["hash"] = digest
        return fields

    def prepare(self, label):
        headers = {"Accept": "application/json"}
        if label in READ_COMMANDS:
            fields = self.command_fields(label)
            url = VERIFY if label == "verify_payment" else SERVICE
            data = urllib.parse.urlencode(fields).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            return "POST", url, data, headers, sorted(fields), url
        if label == "classic_payment_incomplete_validation":
            # No txnid, amount, customer, instrument, callback or executable hash.
            fields = {"key": self.key}
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            return "POST", CLASSIC_PAYMENT, urllib.parse.urlencode(fields).encode(), headers, ["key"], CLASSIC_PAYMENT
        if label not in ("v2_saved_instruments", "v2_payment_empty_validation"):
            raise RuntimeError("Fixed Test endpoint allowlist violation")
        method = "GET" if label == "v2_saved_instruments" else "POST"
        data = None if method == "GET" else b"{}"
        url = INSTRUMENT + "?" + urllib.parse.urlencode({"userCredentials": self.owner}) if method == "GET" else V2_PAYMENT
        date = format_datetime(datetime.now(timezone.utc), usegmt=True)
        digest = hashlib.sha512((data or b"") + b"|" + date.encode() + b"|" + self.salt.encode()).hexdigest()
        auth = f'hmac username="{self.key}", algorithm="sha512", headers="date", signature="{digest}"'
        self.sensitive += [digest, auth]
        headers.update({"Date": date, "Authorization": auth, "Content-Type": "application/json"})
        safe_url = INSTRUMENT + "?userCredentials={omitted_synthetic_owner_scope}" if method == "GET" else V2_PAYMENT
        return method, url, data, headers, [], safe_url

    def call(self, label):
        method, url, data, headers, names, safe_url = self.prepare(label)
        if urllib.parse.urlsplit(url).hostname not in ("test.payu.in", "apitest.payu.in"):
            raise RuntimeError("Live endpoint forbidden")
        record = {"schema_version": 1, "label": label, "sent_at": utc(),
            "request": {"method": method, "url": safe_url,
                "body_field_names_only": names, "body_values": "OMITTED",
                "authentication": "OMITTED", "headers": "OMITTED",
                "scope": "read-only command or deliberately nonpayable incomplete validation"},
            "test_origin_only": True, "redirects_followed": False,
            "automatic_retries": 0, "payable_request": False}
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=25)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            record.update(http_status=None, response={}, transport_outcome="UNKNOWN_NO_RETRY",
                transport_error_class=type(reason).__name__)
            self.save(label, record)
            return record
        with response:
            raw = response.read(4_000_001)
            record.update(http_status=response.code, received_at=utc(),
                raw_response_sha256=hashlib.sha256(raw).hexdigest(), raw_response_bytes=len(raw),
                raw_response_complete=len(raw) <= 4_000_000)
            try:
                payload = json.loads(raw) if len(raw) <= 4_000_000 else None
            except (ValueError, UnicodeError):
                payload = None
            record["response"] = project(payload)
            if not record["response"]["json_object"]:
                record["response"]["body_omitted"] = True
                record["response"]["html_document"] = bool(re.search(rb"<html|<!doctype html", raw, re.I))
            # Full account/payment option/customer/refund data is never copied.
        self.save(label, record)
        return record

    def save(self, label, record):
        path = self.directory / f"{len(self.members) + 1:03d}_{label}.json"
        write_json(path, record, self.sensitive)
        self.members.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        print(f"{label}: HTTP {record['http_status']}; sanitized evidence only", flush=True)

    def finish(self, reason):
        write_json(self.directory / "manifest.json", {
            "schema_version": 1, "run_id": self.run_id, "started_at": self.started_at,
            "finished_at": utc(), "members": self.members, "stop_reason": reason,
            "test_configuration_asserted": True, "test_origin_only": True,
            "server_asserted_account_identity": False, "payable_requests_sent": 0,
            "tokens_registered": 0, "raw_cards_sent": 0, "real_customer_data_sent": 0,
            "contract_changes": 0, "live_requests": 0, "support_contacted": False,
            "sdk": "none; Python stdlib urllib", "capability_proven": False,
        }, self.sensitive)
        print("Sanitized evidence: " + str(self.directory.relative_to(ROOT)), flush=True)


def main():
    probe = Probe()
    labels = ["getNetbankingStatus", "get_checkout_details", "verify_payment",
              "get_Transaction_Details", "getAllRefundsFromTxnIds", "get_user_cards",
              "v2_saved_instruments", "classic_payment_incomplete_validation",
              "v2_payment_empty_validation"]
    if sys.argv[1:] == ["--read-only-enrichment"]:
        # Resolve projection uncertainty with fresh reads only, never retry the 500 POST.
        labels = labels[:7]
    elif sys.argv[1:] == ["--saved-instruments-gate-check"]:
        # Single read clarifies the previous diagnostic template; no mutation retry.
        labels = ["get_user_cards"]
    elif sys.argv[1:]:
        raise RuntimeError("Unknown discovery scope")
    for label in labels:
        record = probe.call(label)
        fragments = set(record["response"].get("recognized_message_fragments", []))
        if fragments & (GATES | AUTH_ERRORS):
            probe.finish("Explicit structured authentication/permission/certification refusal; no dependent probes")
            return
        if record["http_status"] is None or record["http_status"] in (401, 403) or record["http_status"] >= 500:
            probe.finish("Transport or HTTP refusal/uncertainty; account gating not inferred from HTTP alone")
            return
    probe.finish("Noncharging discovery complete; stop before owner token registration or payable experiment")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("PayU discovery stopped safely: " + type(error).__name__, flush=True)
        sys.exit(1)
