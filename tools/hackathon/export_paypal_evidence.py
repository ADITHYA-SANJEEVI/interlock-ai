"""Export an allowlisted feasibility snapshot; never copy profiles or approval URLs."""

import argparse
import hashlib
import json
from datetime import UTC, datetime

from interlock.paypal_sandbox import CODE, resource

from tools.hackathon.paypal_probe import CHECKOUT_MESSAGES, EVIDENCE, ROOT, Run


def snapshot(state):
    latest = state.get("latest", {})
    result = {
        "classification": "GENUINE_PAYPAL_SANDBOX_FEASIBILITY",
        "merchant_profile": state.get("profile_id", "india"),
        "integrated_financial_authority_flow": False,
        "order_id": resource(state["order_id"]) if state.get("order_id") else None,
        "merchant_id": resource(state["merchant_id"]) if state.get("merchant_id") else None,
        "capture_id": resource(state["capture_id"]) if state.get("capture_id") else None,
        "capture_attempted": state.get("capture_attempted") is True,
        "buyer_rejection_reported": state.get("buyer_rejection_reported") is True,
        "buyer_rejection_source": "HUMAN_REPORT" if state.get("buyer_rejection_reported") else None,
        "human_reported_checkout_message": CHECKOUT_MESSAGES.get(state.get("buyer_rejection_reason_code")),
        "capture_confirmed": state.get("knowledge") == "CAPTURE_CONFIRMED" and latest.get("capture_confirmed") is True,
        "knowledge": state.get("knowledge") if CODE.fullmatch(str(state.get("knowledge", ""))) else "UNKNOWN",
        "provider_order_status": latest.get("status") if CODE.fullmatch(str(latest.get("status", ""))) else "UNKNOWN",
        "currency": "USD", "amount_minor": None,
        "configured_account_countries": {"seller": state.get("seller_country_configured"),
                                         "buyer": state.get("buyer_country_configured")},
        "provider_errors": [],
        "local_validator_correction_recorded": any(
            e.get("kind") == "VALIDATOR_CORRECTION_CANONICAL_RECHECK" for e in state.get("events", [])),
    }
    amount = state["body"]["purchase_units"][0]["amount"]
    from interlock.paypal_sandbox import usd_minor

    if amount["currency_code"] != "USD":
        raise ValueError("USD evidence required")
    result["amount_minor"] = usd_minor(amount["value"])
    for event in state.get("events", []):
        evidence = event.get("evidence", {})
        if "http_status" not in evidence:
            continue
        result["provider_errors"].append({k: evidence[k] for k in (
            "http_status", "name", "issues", "debug_id", "response_sha256") if k in evidence})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="india-01")
    args = parser.parse_args()
    run = Run(EVIDENCE, args.run)
    with run.lock():
        raw = run.path.read_bytes()
        value = snapshot(json.loads(raw))
    value["source_state_sha256"] = hashlib.sha256(raw).hexdigest()
    value["exported_at"] = datetime.now(UTC).isoformat()
    directory = ROOT / "docs/hackathon/evidence"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-paypal.json")
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"exported": destination.relative_to(ROOT).as_posix(),
                      "capture_confirmed": value["capture_confirmed"]}))


if __name__ == "__main__":
    main()
