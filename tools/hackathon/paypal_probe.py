"""One durable sandbox feasibility attempt; not the financial product API.

Never delete an uncertain run to retry it. Status/inspect are recovery paths;
capture requires a canonical APPROVED order and an explicit CLI invocation.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from interlock.paypal_sandbox import (
    PayPalFailure,
    PayPalSandbox,
    SandboxCredentials,
    order_body,
    resource,
    usd_minor,
    verify_order,
)

from tools.hackathon.paypal_profiles import (
    PRIVATE as PROFILE_DIRECTORY,
)
from tools.hackathon.paypal_profiles import (
    ProfileBlocked,
    fingerprint,
    load_profile,
    previous_bindings,
    require_run_profile,
    require_verified_us,
    validate_us_profile,
    verify_us_profile,
)

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/hackathon-private/paypal.env"
EVIDENCE = ROOT / "tmp/hackathon-paypal"
CHECKOUT_MESSAGES = {
    "SELLER_CURRENCY_NOT_ACCEPTED": "This seller doesn’t accept payments in your currency.",
}


class ProbeBlocked(ValueError):
    """Server-owned machine codes, safe to display without raw exceptions."""


def now():
    return datetime.now(UTC).isoformat()


class Run:
    def __init__(self, root: Path, name: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", name):
            raise ProbeBlocked("INVALID_RUN_NAME")
        self.directory = root / name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"

    @contextmanager
    def lock(self):
        path = self.directory / "mutation.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ProbeBlocked("RUN_LOCKED_PRESERVE_EVIDENCE") from None
        try:
            os.close(fd)
            yield
        finally:
            path.unlink()

    def read(self):
        if not self.path.exists():
            raise ProbeBlocked("RUN_NOT_FOUND")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state):
        """Flush before send, atomically replace, and retain the original identities."""
        temporary = self.directory / (uuid4().hex + ".pending")
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def event(self, state, kind, evidence):
        state.setdefault("events", []).append({"time": now(), "kind": kind, "evidence": evidence})
        self.save(state)


def inspect(run, client, state):
    if not state.get("order_id"):
        raise ProbeBlocked("CREATE_UNKNOWN_NO_ORDER_ID")
    result = client.get_order(state["order_id"])
    try:
        verified = verify_order(result, state["body"], state["order_id"])
        # Pin the authenticated seller identity once; later reads must match it.
        actual = verified["purchase_units"][0]["merchant_id"]
        if state.get("merchant_id") and state["merchant_id"] != actual:
            raise ValueError("Canonical merchant binding changed")
        if state.get("capture_id") and (not verified["capture_confirmed"] or
                verified["purchase_units"][0]["captures"][0]["id"] != state["capture_id"]):
            raise ValueError("Canonical captured effect changed")
    except (ValueError, KeyError, TypeError):
        state["blocked"] = True
        state["knowledge"] = "EVIDENCE_CONFLICT"
        run.event(state, "CANONICAL_BINDING_REJECTED", {"name": "EXACT_BINDING_REQUIRED"})
        raise ProbeBlocked("CANONICAL_BINDING_CONFLICT") from None
    state["merchant_id"] = actual
    if verified["capture_confirmed"]:
        state["capture_id"] = verified["purchase_units"][0]["captures"][0]["id"]
    state["latest"] = verified
    state["knowledge"] = ("EVIDENCE_CONFLICT" if state.get("blocked") else
                          "CAPTURE_CONFIRMED" if verified["capture_confirmed"] else
                          "CAPTURE_OUTCOME_UNKNOWN" if state["capture_attempted"] else "ORDER_OBSERVED")
    run.event(state, "CANONICAL_GET", verified)
    return verified


def record_buyer_rejection(run, reason_code=None):
    """Human report is an operational stop, never provider zero-effect proof."""
    with run.lock():
        state = run.read()
        if reason_code is not None and reason_code not in CHECKOUT_MESSAGES:
            raise ProbeBlocked("UNREVIEWED_CHECKOUT_MESSAGE")
        if state.get("buyer_rejection_reported") and (
            reason_code is None or reason_code == state.get("buyer_rejection_reason_code")
        ):
            return state
        state["buyer_rejection_reported"] = True
        if reason_code:
            state["buyer_rejection_reason_code"] = reason_code
        run.event(state, "BUYER_REJECTION_REPORTED", {
            "source": "HUMAN_REPORT", "exact_provider_reason": CHECKOUT_MESSAGES.get(reason_code, "NOT_PROVIDED"),
            "local_reason_code": reason_code, "provider_api_error_code": None,
            "provider_zero_effect_proven": False,
        })
        return state


def create(run, client, amount, profile):
    with run.lock():
        if run.path.exists():
            raise ProbeBlocked("CREATE_ALREADY_ATTEMPTED_INSPECT_ONLY")
        client.oauth()  # No create commitment if credentials/authentication are unavailable.
        operation = "probe_" + uuid4().hex
        body = order_body(amount, operation, profile.get("PAYPAL_MERCHANT_ID") or None)
        state = {
            "classification": "PAYPAL_SANDBOX_FEASIBILITY_ONLY",
            "profile_id": profile.get("PAYPAL_PROFILE_ID", "india"),
            "created_at": now(), "operation": operation, "body": body,
            "body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
            "create_request_id": str(uuid4()), "capture_request_id": str(uuid4()),
            "create_attempted": True, "capture_attempted": False,
            "seller_country_configured": profile.get("PAYPAL_SELLER_COUNTRY", "UNKNOWN"),
            "buyer_country_configured": profile.get("PAYPAL_BUYER_COUNTRY", "UNKNOWN"),
            "knowledge": "CREATE_OUTCOME_UNKNOWN", "events": [],
        }
        if state["profile_id"] == "us":
            state["profile_configuration_sha256"] = fingerprint(profile)
        run.save(state)
        try:
            result = client.create_order(body, state["create_request_id"])
            state["order_id"] = resource(result.get("id", ""))
            run.save(state)  # Preserve the ID even if the response lacks canonical fields.
            inspect(run, client, state)
            # The canonical read does not always retain the approval link.
            from interlock.paypal_sandbox import approval_url

            link = approval_url(result)
            if link:
                state["approval_url"] = link
            run.event(state, "ORDER_CREATED", {"order_id": state["order_id"], "approval_url": link})
        except PayPalFailure as error:
            run.event(state, "CREATE_OR_READ_ERROR", error.evidence)
            raise
        except (ValueError, KeyError, TypeError):
            run.event(state, "CREATE_OR_READ_BINDING_REJECTED", {"name": "EXACT_BINDING_REQUIRED"})
            raise ProbeBlocked("CREATE_BINDING_INCOMPLETE_INSPECT_ONLY") from None
        return state


def capture(run, client):
    with run.lock():
        state = run.read()
        if state.get("buyer_rejection_reported"):
            raise ProbeBlocked("BUYER_REJECTION_REPORTED_CAPTURE_BLOCKED")
        verified = inspect(run, client, state)
        if state.get("blocked"):
            raise ProbeBlocked("EVIDENCE_CONFLICT_CAPTURE_BLOCKED")
        if verified["capture_confirmed"]:
            return state
        if state["capture_attempted"]:
            raise ProbeBlocked("CAPTURE_ALREADY_ATTEMPTED_READ_ONLY")
        if verified["status"] != "APPROVED":
            raise ProbeBlocked("BUYER_APPROVAL_REQUIRED")
        state["capture_attempted"] = True
        state["knowledge"] = "CAPTURE_OUTCOME_UNKNOWN"
        run.event(state, "CAPTURE_COMMITTED", {"order_id": state["order_id"]})
        try:
            result = client.capture_order(state["order_id"], state["capture_request_id"])
            # Capture representations can omit order-level amount/custom/payee.
            # Only a subsequent canonical GET may establish exact captured truth.
            if resource(result.get("id", "")) != state["order_id"]:
                raise ValueError("Capture response order mismatch")
            run.event(state, "CAPTURE_RESPONSE_RECEIVED", {"order_id": state["order_id"],
                "response_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()})
            inspect(run, client, state)
        except PayPalFailure as error:
            run.event(state, "CAPTURE_OR_READ_ERROR", error.evidence)
            raise
        except (ValueError, KeyError, TypeError):
            state["blocked"] = True
            state["knowledge"] = "EVIDENCE_CONFLICT"
            run.event(state, "CAPTURE_OR_READ_BINDING_REJECTED", {"name": "EXACT_BINDING_REQUIRED"})
            raise ProbeBlocked("CAPTURE_BINDING_CONFLICT") from None
        return state


def summary(state):
    return {key: state[key] for key in ("classification", "profile_id", "operation", "order_id", "knowledge",
            "approval_url", "merchant_id", "capture_id", "blocked", "latest", "seller_country_configured", "buyer_country_configured",
            "create_attempted", "capture_attempted", "buyer_rejection_reported", "buyer_rejection_reason_code", "events") if key in state}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("oauth", "verify-config", "create", "inspect", "capture", "status"))
    parser.add_argument("--profile", choices=("india", "us"), default="india")
    parser.add_argument("--run", default="india-01")
    parser.add_argument("--amount", default="1.00", help="USD; creation only")
    parser.add_argument("--buyer-country", choices=("IN", "US"),
                        help="Explicit sandbox buyer metadata; does not create/change an account")
    parser.add_argument("--merchant-id", help="Optional explicit sandbox merchant pin; creation only")
    args = parser.parse_args()
    client = None
    try:
        if args.command != "create" and (args.buyer_country or args.merchant_id):
            raise ProbeBlocked("ACCOUNT_CONFIGURATION_FLAGS_ARE_CREATE_ONLY")
        if args.command == "verify-config":
            if args.profile != "us":
                raise ProfileBlocked("EXPLICIT_US_PROFILE_REQUIRED")
            profile = load_profile("us")
            old_client_id, prior = previous_bindings(EVIDENCE)
            validate_us_profile(profile, india_client_id=old_client_id, prior_merchants=prior)
            client = PayPalSandbox(SandboxCredentials(profile["PAYPAL_CLIENT_ID"], profile["PAYPAL_CLIENT_SECRET"]))
            print(json.dumps(verify_us_profile(profile, client, india_client_id=old_client_id,
                                              prior_merchants=prior)))
            return 0
        run = Run(EVIDENCE, args.run)
        if args.command == "status":
            print(json.dumps(summary(run.read()), indent=2))
            return 0
        profile = load_profile(args.profile)
        if args.command in {"inspect", "capture"}:
            require_run_profile(run.read(), profile)
        if args.command == "create":
            if args.profile != "us":
                raise ProfileBlocked("NEW_ORDERS_REQUIRE_VERIFIED_US_PROFILE")
            if ((args.buyer_country and args.buyer_country != profile.get("PAYPAL_BUYER_COUNTRY"))
                    or (args.merchant_id and args.merchant_id != profile.get("PAYPAL_MERCHANT_ID"))):
                raise ProfileBlocked("VERIFIED_CONFIGURATION_OVERRIDE_FORBIDDEN")
            require_verified_us(profile, directory=PROFILE_DIRECTORY)
        client = PayPalSandbox(SandboxCredentials(profile.get("PAYPAL_CLIENT_ID") or "",
                                                 profile.get("PAYPAL_CLIENT_SECRET") or ""))
        if args.command == "oauth":
            print(json.dumps(client.oauth()))
            return 0
        if args.command == "create":
            creation_profile = dict(profile)
            if args.buyer_country:
                creation_profile["PAYPAL_BUYER_COUNTRY"] = args.buyer_country
            if args.merchant_id:
                creation_profile["PAYPAL_MERCHANT_ID"] = resource(args.merchant_id)
            state = create(run, client, usd_minor(args.amount), creation_profile)
        elif args.command == "capture":
            state = capture(run, client)
        else:
            with run.lock():
                state = run.read()
                try:
                    inspect(run, client, state)
                except PayPalFailure as error:
                    run.event(state, "CANONICAL_READ_ERROR", error.evidence)
                    raise
        print(json.dumps(summary(state), indent=2))
        return 0
    except PayPalFailure as error:
        print(json.dumps({"provider_error": error.evidence,
                          "action": "Preserve run; inspect only. No account country was changed."}))
        return 2
    except (ProbeBlocked, ProfileBlocked) as error:
        print(json.dumps({"blocked": True, "code": str(error),
                          "action": "Preserve evidence; do not create a replacement or replay capture."}))
        return 2
    except (ValueError, OSError):
        # Never include arbitrary exception text, which could contain a local secret or path.
        print(json.dumps({"blocked": True, "reason": "Check private credentials, run state and exact consent/bindings.",
                          "action": "Preserve evidence; no replacement create or capture is authorized."}))
        return 2
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
