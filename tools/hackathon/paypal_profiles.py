"""Explicit local merchant profiles. No fallback, provider mutation or secret output."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from interlock.paypal_sandbox import resource

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "tmp/hackathon-private"
PROFILE_FILES = {"india": "paypal.env", "us": "paypal-us.env"}


class ProfileBlocked(ValueError):
    """Bounded server-owned errors; never raw profile content."""


def load_profile(name: str, directory: Path = PRIVATE) -> dict[str, str]:
    if name not in PROFILE_FILES:
        raise ProfileBlocked("UNKNOWN_MERCHANT_PROFILE")
    path = directory / PROFILE_FILES[name]
    if not path.is_file():
        raise ProfileBlocked("MERCHANT_PROFILE_MISSING")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    values["PAYPAL_PROFILE_ID"] = name
    if not values.get("PAYPAL_CLIENT_ID") or not values.get("PAYPAL_CLIENT_SECRET"):
        raise ProfileBlocked("PAYPAL_CREDENTIALS_REQUIRED")
    return values


def previous_bindings(evidence_root: Path, *, directory: Path = PRIVATE):
    """Optional comparison only; a fresh clone need not contain private India state."""
    old_path = directory / PROFILE_FILES["india"]
    old_client_id = dotenv_values(old_path).get("PAYPAL_CLIENT_ID") if old_path.is_file() else None
    merchants = set()
    for name in ("india-01", "us-in-01"):
        path = evidence_root / name / "state.json"
        if path.is_file():
            merchant = json.loads(path.read_text(encoding="utf-8")).get("merchant_id")
            if merchant:
                merchants.add(resource(merchant))
    return old_client_id, merchants


def fingerprint(values: dict[str, str]) -> str:
    # Persist only inside ignored private state. Credential changes invalidate the gate.
    fields = ("PAYPAL_PROFILE_ID", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_MERCHANT_ID",
              "PAYPAL_SELLER_COUNTRY", "PAYPAL_BUYER_COUNTRY", "PAYPAL_CURRENCY", "PAYPAL_ACCOUNT_TYPE",
              "PAYPAL_APP_TYPE", "PAYPAL_APP_BINDING_CONFIRMED")
    raw = json.dumps({key: values.get(key) for key in fields}, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_us_profile(values: dict[str, str], *, india_client_id: str | None = None,
                        prior_merchants: set[str] | None = None) -> None:
    required = {"PAYPAL_PROFILE_ID": "us", "PAYPAL_SELLER_COUNTRY": "US", "PAYPAL_BUYER_COUNTRY": "US",
                "PAYPAL_CURRENCY": "USD", "PAYPAL_ACCOUNT_TYPE": "BUSINESS", "PAYPAL_APP_TYPE": "MERCHANT",
                "PAYPAL_APP_BINDING_CONFIRMED": "true"}
    if any(values.get(key) != expected for key, expected in required.items()):
        raise ProfileBlocked("US_MERCHANT_DASHBOARD_BINDING_REQUIRED")
    if not values.get("PAYPAL_CLIENT_ID") or not values.get("PAYPAL_CLIENT_SECRET"):
        raise ProfileBlocked("PAYPAL_CREDENTIALS_REQUIRED")
    try:
        merchant = resource(values.get("PAYPAL_MERCHANT_ID", ""))
    except ValueError:
        raise ProfileBlocked("NEW_US_MERCHANT_ID_REQUIRED") from None
    if merchant in (prior_merchants or set()):
        raise ProfileBlocked("INDIA_MERCHANT_ID_REUSE_FORBIDDEN")
    if india_client_id and values["PAYPAL_CLIENT_ID"] == india_client_id:
        raise ProfileBlocked("INDIA_APP_CREDENTIAL_REUSE_FORBIDDEN")


def verify_us_profile(values, client, *, directory=PRIVATE, india_client_id=None, prior_merchants=None):
    validate_us_profile(values, india_client_id=india_client_id, prior_merchants=prior_merchants)
    authenticated = client.oauth()
    if authenticated.get("authenticated") is not True or authenticated.get("environment") != "PAYPAL_SANDBOX":
        raise ProfileBlocked("SANDBOX_OAUTH_NOT_VERIFIED")
    receipt = {
        "profile_id": "us", "configuration_sha256": fingerprint(values),
        "verified_at": datetime.now(UTC).isoformat(), "oauth_verified": True,
        "expected_merchant_id": values["PAYPAL_MERCHANT_ID"], "seller_country": "US", "buyer_country": "US",
        "currency": "USD", "binding_basis": "HUMAN_CONFIRMED_SANDBOX_APP_DASHBOARD",
        "provider_merchant_binding_verified": False, "payment_acceptance_verified": False,
        "provider_order_mutations": 0,
    }
    directory.mkdir(parents=True, exist_ok=True)
    history = directory / ("paypal-us-verification-" + uuid4().hex + ".json")
    raw = json.dumps(receipt, indent=2) + "\n"
    with history.open("x", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    temporary = directory / (uuid4().hex + ".pending")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, directory / "paypal-us.verified.json")
    return {key: value for key, value in receipt.items() if key != "configuration_sha256"}


def require_verified_us(values, *, directory=PRIVATE):
    validate_us_profile(values)
    path = directory / "paypal-us.verified.json"
    if not path.is_file():
        raise ProfileBlocked("US_CONFIGURATION_VERIFICATION_REQUIRED")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (receipt.get("configuration_sha256") != fingerprint(values) or receipt.get("oauth_verified") is not True
            or receipt.get("profile_id") != "us" or receipt.get("expected_merchant_id") != values["PAYPAL_MERCHANT_ID"]):
        raise ProfileBlocked("US_CONFIGURATION_CHANGED_REVERIFY")


def require_run_profile(state, profile):
    selected = profile["PAYPAL_PROFILE_ID"]
    if state.get("profile_id", "india") != selected:
        raise ProfileBlocked("RUN_MERCHANT_PROFILE_MISMATCH")
    if state.get("profile_configuration_sha256") and state["profile_configuration_sha256"] != fingerprint(profile):
        raise ProfileBlocked("RUN_CREDENTIAL_CONFIGURATION_CHANGED")
