"""Isolated Pass 0.5 probes. No product payment or ledger implementation."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/razorpay_testmode/v1"
FIELDS = {
    "id", "entity", "amount", "amount_paid", "amount_due", "amount_refunded",
    "currency", "receipt", "status", "attempts", "created_at", "captured",
    "order_id", "payment_id", "refund_id", "account_id", "method",
    "speed_requested", "speed_processed", "count", "items", "event",
    "contains", "payload", "payment", "order", "refund", "error", "code",
    "description", "source", "step", "reason", "field", "partial_payment",
    "authorized_at", "captured_at", "processed_at", "failed_at",
    "payments", "plink_id", "payment_link_id", "reference_id", "accept_partial",
    "expire_by", "expired_at", "cancelled_at", "updated_at",
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def credentials():
    """Read only ignored local configuration; never display configuration values."""
    path = ROOT / ".env"
    if not path.is_file() or subprocess.run(
        ["git", "check-ignore", "-q", "--", ".env"], cwd=ROOT,
        capture_output=True, check=False,
    ).returncode != 0:
        raise RuntimeError("Ignored local configuration is required")
    names = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
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
    values = tuple(os.environ.get(name) or parsed.get(name, "") for name in names)
    if not all(values) or not values[0].startswith("rzp_test_"):
        raise RuntimeError("All three local test-mode variables are required")
    return values


def sanitized(value, sensitive, error_context=False):
    if isinstance(value, dict):
        return {key: sanitized(item, sensitive, error_context or key == "error")
                for key, item in value.items()
                if key in FIELDS and (key != "description" or error_context)}
    if isinstance(value, list):
        return [sanitized(item, sensitive, error_context) for item in value]
    if isinstance(value, str):
        for token in sorted(sensitive, key=len, reverse=True):
            value = value.replace(token, "[REDACTED]")
        return re.sub(r"rzp_(?:test|live)_[A-Za-z0-9]+", "[REDACTED_CREDENTIAL]", value)[:512]
    if value is None or isinstance(value, (bool, int)):
        return value
    return "[OMITTED]"


def write_json(path, value, sensitive=()):
    """Append a new artifact, fail closed if any credential survived sanitization."""
    data = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if any(token and token in data for token in sensitive):
        raise RuntimeError("Artifact credential exclusion failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Probe:
    def __init__(self):
        key, secret, webhook = credentials()
        encoded = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self.sensitive = (key, secret, webhook, encoded)
        self.auth = "Basic " + encoded
        self.opener = urllib.request.build_opener(NoRedirect())
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:12]
        self.directory = EVIDENCE / self.run_id
        self.directory.mkdir(parents=True)
        self.members = []
        self.started_at = utc()

    def call(self, label, method, path, body=None, headers=None):
        if not path.startswith("/") or "://" in path:
            raise RuntimeError("Only fixed Razorpay API origin is allowed")
        sent = utc()
        request_headers = {"Authorization": self.auth, "Content-Type": "application/json"}
        request_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            "https://api.razorpay.com/v1" + path, data=data,
            headers=request_headers, method=method,
        )
        try:
            response = self.opener.open(request, timeout=25)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            record = self._record(label, method, path, body, headers, sent, None, {})
            record["transport_outcome"] = "UNKNOWN_NO_AUTOMATIC_RETRY"
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            record["transport_diagnostic"] = {
                "class": type(reason).__name__, "errno": getattr(reason, "errno", None),
                "winerror": getattr(reason, "winerror", None),
            }
            record["provenance"] = "LOCAL_TRANSPORT_DIAGNOSTIC_NO_PROVIDER_RESPONSE"
            self._save(label, record)
            print(f"{label}: transport uncertainty; no automatic retry", flush=True)
            return None, {}
        with response:
            raw = response.read(2_000_001)
            status = response.code
            if len(raw) > 2_000_000:
                raise RuntimeError("Response exceeds bounded probe size")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                payload = {}
            record = self._record(label, method, path, body, headers, sent, status, payload)
            record["response_sha256"] = hashlib.sha256(raw).hexdigest()
            record["response_date"] = response.headers.get("Date")
            record["api_version_header"] = response.headers.get("X-Razorpay-Api-Version")
        self._save(label, record)
        print(f"{label}: HTTP {status}", flush=True)
        return status, payload

    def _record(self, label, method, path, body, headers, sent, status, payload):
        return {
            "schema_version": 1, "provenance": "REAL_RAZORPAY_TEST_MODE_EVIDENCE",
            "run_id": self.run_id, "label": label,
            "request": {"method": method, "path": path,
                        "header_names_only": sorted((headers or {}).keys()),
                        "idempotency_key_sha256": hashlib.sha256(headers["X-Refund-Idempotency"].encode()).hexdigest()
                        if headers and "X-Refund-Idempotency" in headers else None,
                        "authentication": "OMITTED", "body": sanitized(body, self.sensitive)},
            "sent_at": sent, "observed_at": utc(), "http_status": status,
            "response": sanitized(payload, self.sensitive),
            "mode_basis": "Locally checked test-key prefix plus human Test Mode attestation",
            "canonical_account_id": None, "api_family": "v1", "sdk": "none; Python stdlib urllib",
            "sanitization": "Allowlist; no raw bodies, credential headers, signatures or customer details",
        }

    def _save(self, label, record):
        filename = f"{len(self.members) + 1:03d}_{label}.json"
        path = self.directory / filename
        write_json(path, record, self.sensitive)
        self.members.append({"file": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def finish(self, findings):
        write_json(self.directory / "manifest.json", {
            "schema_version": 1, "run_id": self.run_id, "started_at": self.started_at,
            "finished_at": utc(), "members": self.members, "findings": findings,
            "api_family": "v1", "exact_api_release": "not exposed by API; not invented",
            "sdk": "none; Python standard library", "test_mode_only": True,
        }, self.sensitive)
        print(f"Sanitized evidence: docs/evidence/razorpay_testmode/v1/{self.run_id}", flush=True)
