"""Local Pass 0.5 evidence receiver only; no provider calls or product workflow."""

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from razorpay_common import EVIDENCE, credentials, sanitized, utc, write_json

EVENTS = frozenset(("payment.authorized", "payment.captured", "payment.failed",
                    "order.paid", "refund.created", "refund.processed", "refund.failed"))
PATH = "/webhooks/razorpay"
LIMIT = 262144


class Receiver(HTTPServer):
    def __init__(self, port, secret, sensitive, directory, expected_account=None, synthetic=False):
        self.secret = secret.encode()
        self.sensitive = sensitive
        self.directory = directory
        self.expected_account = expected_account
        self.synthetic = synthetic
        super().__init__(("127.0.0.1", port), Handler)

    def handle_error(self, request, client_address):
        # Never emit request bytes, headers, exception strings or tracebacks.
        pass


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass

    def reply(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        self.reply(200 if self.path == "/health" else 404)

    def do_POST(self):
        try:
            self.receive()
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            self.close_connection = True
            self.reply(503)

    def receive(self):
        if self.path != PATH:
            self.reply(404)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            self.reply(400)
            return
        length = int(lengths[0])
        if not 0 < length <= LIMIT:
            self.reply(413)
            return
        if self.headers.get_content_type() != "application/json":
            self.reply(415)
            return
        signatures = self.headers.get_all("X-Razorpay-Signature", [])
        event_ids = self.headers.get_all("X-Razorpay-Event-Id", [])
        if len(signatures) != 1 or len(event_ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", event_ids[0]):
            self.reply(400)
            return
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.reply(400)
            return
        digest = hmac.new(self.server.secret, raw, hashlib.sha256).hexdigest()
        if not re.fullmatch(r"[a-fA-F0-9]{64}", signatures[0]) or not hmac.compare_digest(digest, signatures[0].lower()):
            self.reply(401)
            return
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self.reply(400)
            return
        if not isinstance(payload, dict) or payload.get("event") not in EVENTS or not isinstance(payload.get("payload"), dict):
            self.reply(422)
            return
        account = payload.get("account_id")
        if not isinstance(account, str) or not re.fullmatch(r"acc_[A-Za-z0-9]{1,80}", account):
            self.reply(422)
            return
        if payload.get("livemode") is True or (self.server.expected_account and account != self.server.expected_account):
            self.reply(403)
            return
        body_hash = hashlib.sha256(raw).hexdigest()
        # Event header is not covered by the body MAC. Dedupe is a delivery aid,
        # never proof of economic effect identity or event freshness.
        key = hashlib.sha256((account + ":" + event_ids[0]).encode()).hexdigest()
        canonical = self.server.directory / ("event_" + key + ".json")
        previous = json.loads(canonical.read_text()) if canonical.exists() else None
        disposition = "NEW" if previous is None else (
            "DUPLICATE" if previous["body_sha256"] == body_hash else "EVENT_ID_BODY_CONFLICT")
        receipt = {
            "schema_version": 1,
            "provenance": "LOCAL_SYNTHETIC_SIGNATURE_CHECK" if self.server.synthetic else "INBOUND_SIGNATURE_VERIFIED_PROVIDER_ORIGIN_PENDING_CORROBORATION",
            "observed_at": utc(), "event_id": event_ids[0], "body_sha256": body_hash,
            "signature_valid": True, "signature": "OMITTED", "disposition": disposition,
            "scope_validation": "EXPECTED_ACCOUNT_MATCH" if self.server.expected_account else "ACCOUNT_UNBOUND_PENDING_API_CORROBORATION",
            "mode_basis": "Test-key prefix checked at startup; Dashboard Test Mode setup required; no authenticated body mode inferred",
            "freshness": "Body MAC has no separate signed delivery timestamp; no freshness guarantee",
            "envelope": sanitized(payload, self.server.sensitive),
            "financial_effect": "NONE; evidence only; no normalization or ledger",
        }
        if previous is None:
            write_json(canonical, receipt, self.server.sensitive)
        write_json(self.server.directory / ("receipt_" + uuid4().hex + ".json"), receipt, self.server.sensitive)
        # Both durable sanitized records exist before acknowledgment. Conflicts
        # preserve evidence and fail closed; arrival order never rewrites state.
        self.reply(409 if disposition == "EVENT_ID_BODY_CONFLICT" else 200)


def self_test():
    secret = "synthetic-only-fixture-key"
    tests = []
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        server = Receiver(0, secret, (secret,), directory, "acc_Synthetic", True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}{PATH}"
        def send(name, raw, event_id, expected, signature=None, extra=None):
            headers = {"Content-Type": "application/json", "X-Razorpay-Event-Id": event_id,
                       "X-Razorpay-Signature": signature or hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}
            headers.update(extra or {})
            try:
                with urllib.request.urlopen(urllib.request.Request(url, raw, headers), timeout=5) as response:
                    actual = response.status
            except urllib.error.HTTPError as error:
                actual = error.code
                error.close()
            assert actual == expected, name
            tests.append({"name": name, "expected_http_status": expected, "passed": True})
        def body(event="payment.captured", account="acc_Synthetic", amount=6000):
            return json.dumps({"event": event, "account_id": account, "payload": {"payment": {"entity": {
                "id": "pay_Synthetic", "amount": amount, "currency": "INR", "status": event.split(".")[1],
                "email": "must-not-persist@example.invalid", "notes": {"secret": secret}}}}}).encode()
        try:
            captured = body()
            send("valid_raw_body", captured, "evt_1", 200)
            send("identical_redelivery", captured, "evt_1", 200)
            send("different_event_same_payment", captured, "evt_2", 200)
            send("older_authorized_after_capture", body("payment.authorized"), "evt_3", 200)
            send("same_event_changed_body", body(amount=6100), "evt_1", 409)
            send("forged_signature", captured, "evt_4", 401, "0" * 64)
            signature = hmac.new(secret.encode(), captured, hashlib.sha256).hexdigest()
            send("raw_byte_tamper", captured + b" ", "evt_5", 401, signature)
            send("signed_malformed_json", b"{", "evt_6", 400)
            send("wrong_signed_account", body(account="acc_Other"), "evt_7", 403)
            send("unselected_event", body("payment.unsupported"), "evt_8", 422)
            send("body_limit", b"x" * (LIMIT + 1), "evt_9", 413)
            send("wrong_media_type", captured, "evt_10", 415, extra={"Content-Type": "text/plain"})
            events = list(directory.glob("event_*.json"))
            receipts = list(directory.glob("receipt_*.json"))
            assert len(events) == 3 and len(receipts) == 5
            text = "".join(path.read_text() for path in receipts + events)
            assert secret not in text and "must-not-persist" not in text and '"notes"' not in text
            assert json.loads((directory / ("event_" + hashlib.sha256(b"acc_Synthetic:evt_1").hexdigest() + ".json")).read_text())["envelope"]["event"] == "payment.captured"
            tests.append({"name": "durable_sanitized_history_preserves_capture_and_distinct_deliveries", "passed": True})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    result = {"schema_version": 1, "provenance": "LOCAL_SYNTHETIC_SIGNATURE_CHECK",
              "observed_at": utc(), "tests": tests, "genuine_webhook_delivery": False,
              "limitations": "Local HTTP/signature/dedupe checks only; no provider or simulator fault claims"}
    path = EVIDENCE / ("receiver_selftest_" + uuid4().hex[:12] + ".json")
    write_json(path, result)
    print(f"PASS: {len(tests)} synthetic receiver checks; sanitized result: {path.relative_to(EVIDENCE.parent.parent.parent)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--expected-account-id")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    key, secret, webhook = credentials()
    directory = EVIDENCE / ("webhook_session_" + uuid4().hex[:12])
    server = Receiver(args.port, webhook, (key, secret, webhook), directory, args.expected_account_id)
    print(f"Local feasibility receiver: 127.0.0.1:{args.port}{PATH}; no HTTP request logging", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("Receiver stopped safely; diagnostic details suppressed", flush=True)
        raise SystemExit(1) from None
