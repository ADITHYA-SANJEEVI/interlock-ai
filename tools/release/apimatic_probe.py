"""One bounded APIMatic ContextMatic access check. No project or PayPal secrets."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

ENDPOINT = "https://chatbotapi.apimatic.io/mcp/plugins"
ROOT = Path(__file__).resolve().parents[2]


def run():
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "interlock-release-review", "version": "019"},
        },
    }
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        response = client.post(
            ENDPOINT,
            headers={"Accept": "application/json, text/event-stream"},
            json=request,
        )
    try:
        body = response.json()
        safe_error = body.get("detail") or body.get("error") or body.get("message")
        # Only known authentication error words are released; never server tokens.
        safe_error = safe_error if safe_error in ("Unauthorized", "Unauthorized.", "Not authenticated") else None
    except (ValueError, AttributeError):
        safe_error = "Unauthorized" if response.text.strip().lower() == "unauthorized" else None
    report = {
        "classification": "ACTUAL_APIMATIC_CONTEXT_PROTOCOL_ACCESS_CHECK",
        "checked_at": datetime.now(UTC).isoformat(),
        "endpoint": ENDPOINT,
        "method": "initialize",
        "protocol_version": request["params"]["protocolVersion"],
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "sanitized_response": safe_error,
        "response_bytes": len(response.content),
        "credentials_sent": False,
        "project_or_payment_data_sent": False,
        "plugin_tool_calls_succeeded": False,
        "documented_configuration": "Official context-matic .mcp.json HTTP endpoint, no authentication header configured",
        "outcome": "ACCESS_DENIED_BEFORE_TOOL_DISCOVERY" if response.status_code == 401 else "REQUIRES_PROTOCOL_RESPONSE_REVIEW",
        "next_action": "Ask APIMatic to confirm hackathon entitlement/endpoint access; do not supply merchant credentials.",
        "certainty": "The official documented endpoint was used. HTTP 401 alone cannot distinguish service configuration from entitlement.",
        "no_automatic_retry": True,
    }
    directory = ROOT / "tmp/hackathon-019-sponsors"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-apimatic.json")
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if response.status_code == 200:
        print("INITIALIZATION_RETURNED_200_REVIEW_BEFORE_TOOL_DISCOVERY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    run()
