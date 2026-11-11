"""Pass 0.6 capability discovery only. Never sends a payable request or a token."""

import base64
import hashlib
import json
import re
import sys

import razorpay_common as common

EVIDENCE = common.ROOT / "docs/evidence/razorpay_tokenized_s2s/v1"
PAYMENT = "pay_TjkzAehO12GaHg"


class CapabilityProbe(common.Probe):
    def __init__(self):
        # Separate evidence namespace; existing Pass 0.5 artifacts stay untouched.
        common.EVIDENCE = EVIDENCE
        super().__init__()
        key, _, _ = common.credentials()
        key_only = base64.b64encode((key + ":").encode()).decode()
        self.key_only_auth = "Basic " + key_only
        self.sensitive += (key_only,)
        self.customer_id = None

    def call(self, label, method, path, body=None, headers=None):
        permitted = {
            ("GET", "/methods"), ("GET", "/payments/" + PAYMENT),
            ("POST", "/customers"), ("POST", "/tokens/fetch"),
            ("POST", "/payments/create/recurring"),
            ("POST", "/payments/create/json"),
        }
        if self.customer_id:
            permitted.update({("GET", "/customers/" + self.customer_id),
                              ("GET", "/customers/" + self.customer_id + "/tokens")})
        if (method, path) not in permitted or headers:
            raise RuntimeError("Outside the fixed capability discovery allowlist")
        if method == "POST":
            expected = {"name": "Pass 0.6 capability fixture"} if path == "/customers" else {}
            if body != expected:
                raise RuntimeError("Only isolated customer creation or empty validation bodies allowed")
        elif body is not None:
            raise RuntimeError("GET body forbidden")
        old_auth = self.auth
        try:
            if path == "/methods":
                # Official Methods API requires the key ID only, not its secret.
                self.auth = self.key_only_auth
            return super().call(label, method, path, body, headers)
        finally:
            self.auth = old_auth

    def _record(self, label, method, path, body, headers, sent, status, payload):
        safe_path = re.sub(r"cust_[A-Za-z0-9]+", "{isolated_customer_id}", path)
        if "error" in payload:
            safe = {"error": payload["error"]}
        elif path == "/methods":
            safe = {key: payload[key] for key in ("entity",) if key in payload}
        elif "/customers" in path:
            # Never persist customer identity/details, token IDs/values or nested cards.
            safe = {key: payload[key] for key in ("entity", "count", "created_at") if key in payload}
        else:
            safe = {key: payload[key] for key in (
                "id", "entity", "status", "amount", "currency", "order_id", "captured"
            ) if key in payload and not str(payload[key]).startswith("token_")}
        record = super()._record(label, method, safe_path, None, headers, sent, status, safe)
        record["request"]["body"] = {} if method == "POST" and path != "/customers" else None
        record["request"]["body_field_names_only"] = sorted((body or {}).keys())
        record["request"]["body_values_omitted"] = path == "/customers"
        record["sanitization"] = "Endpoint-specific allowlist; customer identity, tokens, cards, PII, credentials omitted"
        record["probe_scope"] = "Noncharging capability discovery; payment POSTs have empty JSON only"
        if path == "/methods" and "error" not in payload:
            record["enabled_method_summary"] = {
                key: payload[key] for key in ("card", "debit_card", "credit_card", "prepaid_card", "upi")
                if isinstance(payload.get(key), bool)
            }
            recurring = payload.get("recurring", {})
            record["recurring_method_summary"] = {
                key: value for key, value in recurring.items()
                if key in ("upi", "nach", "emandate") and isinstance(value, bool)
            } if isinstance(recurring, dict) else None
            cards = recurring.get("card", {}) if isinstance(recurring, dict) else {}
            record["recurring_card_networks"] = {
                key: [network for network in value if network in ("Visa", "MasterCard", "Mastercard", "RuPay", "Rupay", "Amex")]
                for key, value in cards.items()
                if key in ("credit", "debit") and isinstance(value, list)
            } if isinstance(cards, dict) else {}
        if path == "/payments/" + PAYMENT and "error" not in payload:
            record["token_reference_present"] = bool(payload.get("token_id"))
            record["customer_reference_present"] = bool(payload.get("customer_id"))
        if path == "/customers" and "error" not in payload:
            record["customer_reference_sha256"] = hashlib.sha256(str(payload.get("id", "")).encode()).hexdigest()
        if path.endswith("/tokens") and "error" not in payload:
            record["returned_token_count"] = len(payload.get("items", []))
            record["token_authority_values_persisted"] = False
        return record

    def finish(self, findings):
        common.write_json(self.directory / "manifest.json", {
            "schema_version": 1, "run_id": self.run_id, "started_at": self.started_at,
            "finished_at": common.utc(), "members": self.members, "findings": findings,
            "api_family": "v1", "exact_api_release": "not exposed; not invented",
            "sdk": "none; Python stdlib urllib", "test_mode_only": True,
        }, self.sensitive)
        print("Sanitized evidence: " + str(self.directory.relative_to(common.ROOT)), flush=True)


def main():
    probe = CapabilityProbe()
    findings = {"payable_requests_sent": 0, "token_registration_attempted": False,
                "product_contract_amended": False, "capability_proven": False}
    status, _ = probe.call("methods", "GET", "/methods")
    if status is None or status >= 500:
        probe.finish({**findings, "stop_reason": "read-only transport/provider uncertainty"})
        return
    probe.call("existing_checkout_payment", "GET", "/payments/" + PAYMENT)
    status, customer = probe.call("isolated_customer_create", "POST", "/customers",
                                  {"name": "Pass 0.6 capability fixture"})
    if status == 200 and re.fullmatch(r"cust_[A-Za-z0-9]+", str(customer.get("id", ""))):
        probe.customer_id = customer["id"]
        probe.sensitive += (probe.customer_id,)
        probe.call("isolated_customer_fetch", "GET", "/customers/" + probe.customer_id)
        probe.call("isolated_customer_tokens", "GET", "/customers/" + probe.customer_id + "/tokens")
    # Documented read-only TokenHQ fetch with no ID: no fabricated token.
    status, token_fetch = probe.call("tokenhq_fetch_empty_validation", "POST", "/tokens/fetch", {})
    message = str(token_fetch.get("error", {}).get("description", "")).lower()
    if status in (401, 403) or any(term in message for term in (
        "not enabled", "not allowed", "access denied", "not permitted", "not activated"
    )):
        probe.finish({**findings, "stop_reason": "TokenHQ read-only fetch explicitly refused; no payment probes",
                      "tokenhq_fetch_error": common.sanitized(token_fetch.get("error", {}), probe.sensitive, True)})
        return
    # Deliberately incomplete requests cannot identify a payable amount/order/instrument.
    # A validation error proves only that validation ran; never enablement or usability.
    status, recurring = probe.call("recurring_empty_validation", "POST", "/payments/create/recurring", {})
    status, s2s = probe.call("s2s_json_empty_validation", "POST", "/payments/create/json", {})
    findings["stop_reason"] = "No token registration or payable request authorized by this probe"
    findings["s2s_error"] = common.sanitized(s2s.get("error", {}), probe.sensitive, True)
    findings["recurring_error"] = common.sanitized(recurring.get("error", {}), probe.sensitive, True)
    findings["tokenhq_fetch_error"] = common.sanitized(token_fetch.get("error", {}), probe.sensitive, True)
    probe.finish(findings)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not expose exception text, HTTP bodies, customer data or request headers.
        print("Capability probe stopped safely: " + type(error).__name__, flush=True)
        sys.exit(1)
