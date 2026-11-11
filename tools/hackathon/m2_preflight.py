"""Genuine proposal-to-policy preparation and approved reservation; no PSP sends.

Cannot authenticate as approver or invoke order/capture endpoints. Every command
identity is persisted before I/O; an uncertain result stops for investigation.
"""

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from interlock.paypal_flow import VERIFIED_US_MERCHANT, protected_body

from tools.hackathon.disposable_postgres import DATABASES, PRIVATE, ROOT, connect
from tools.hackathon.m2_demo import PEOPLE, STATE, TENANT, checked_demo


def persist(state):
    temporary = STATE.with_name("m2-state-" + uuid4().hex + ".pending")
    with temporary.open("x", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, STATE)


class DemoClient:
    def __init__(self, state):
        self.state = state
        self.credentials = dotenv_values(PRIVATE / "m2-operator.env")
        self.tokens = {}
        self.http = httpx.Client(timeout=90, trust_env=False, follow_redirects=False,
                                 transport=httpx.HTTPTransport(retries=0))

    def close(self):
        self.http.close()

    def token(self, role):
        if role not in {"issuer", "requester"}:
            raise RuntimeError("HUMAN_APPROVER_LOGIN_REQUIRED")
        if role not in self.tokens:
            result = self.http.post("http://127.0.0.1:18100/session", json={
                "username": self.credentials["DEMO_" + role.upper() + "_USERNAME"],
                "password": self.credentials["DEMO_" + role.upper() + "_PASSWORD"]})
            if result.status_code != 200:
                raise RuntimeError("SCOPED_SESSION_LOGIN_FAILED")
            self.tokens[role] = result.json()["token"]
        return self.tokens[role]

    def get(self, path, role="requester"):
        result = self.http.get("http://127.0.0.1:18101/v1/paypal/" + path,
                               headers={"Authorization": "Bearer " + self.token(role)})
        if result.status_code != 200:
            raise RuntimeError("AUTHENTICATED_PREFLIGHT_READ_FAILED")
        return result.json()

    def post(self, name, path, body, revision=None, role="requester"):
        allowed = {"proposals", "quotes", "quote-renewals", "commands/publish-policy", "commands/create-grant",
                   "commands/activate-grant", "commands/revise-grant", "commands/propose", "commands/admit"}
        if path not in allowed or role not in {"issuer", "requester"}:
            raise RuntimeError("PREFLIGHT_FINANCIAL_ACTION_NOT_AUTHORIZED")
        steps = self.state["purchase_commands"]
        prior = steps.get(name)
        if prior:
            if (prior["path"], prior["body"], prior["revision"], prior["role"]) != (path, body, revision, role):
                raise RuntimeError("PERSISTED_COMMAND_IDENTITY_CONFLICT")
            if "response" in prior:
                return prior["response"]
            if prior.get("sent"):
                raise RuntimeError("UNKNOWN_HTTP_OUTCOME_REQUIRES_ORIGINAL_IDENTITY_REVIEW")
        else:
            prior = steps[name] = {"path": path, "body": body, "revision": revision, "role": role,
                                   "idempotency_key": "m2_" + uuid4().hex}
        headers = {"Authorization": "Bearer " + self.token(role), "Idempotency-Key": prior["idempotency_key"]}
        if revision is not None:
            headers["If-Match"] = str(revision)
        prior["sent"] = True
        persist(self.state)
        result = self.http.post("http://127.0.0.1:18101/v1/paypal/" + path, headers=headers, json=body)
        if result.status_code != 200:
            detail = result.json().get("detail")
            prior["http_status"] = result.status_code
            prior["bounded_error"] = detail if isinstance(detail, str) and re.fullmatch(r"[A-Z_]{3,100}", detail) else "PREFLIGHT_REJECTED"
            prior["diagnostic"] = result.json().get("diagnostic", {})
            persist(self.state)
            raise RuntimeError(prior["bounded_error"])
        prior["response"] = result.json()
        persist(self.state)
        return prior["response"]


def prepare(client):
    state = client.state
    state.setdefault("authority_expires_at", (datetime.now(UTC) + timedelta(hours=2)).isoformat())
    state.setdefault("intent", "Propose one workspace-seat for alice, maximum USD 1.00. Currency USD.")
    persist(state)
    client.post("policy", "commands/publish-policy", {"kind": "PURCHASE", "approval_above": 0,
                "max_amount": 100, "currency": "USD"}, 1, "issuer")
    grant = client.post("grant", "commands/create-grant", {"kind": "PURCHASE", "subject": PEOPLE["requester"],
        "lifetime_bound": 100, "currency": "USD", "beneficiaries": ["alice"],
        "expires_at": state["authority_expires_at"], "resource_id": None}, 0, "issuer")
    client.post("activate", "commands/activate-grant", {"grant_id": grant["grant_id"], "currency": "USD"}, 1, "issuer")
    proposal = client.post("proposal", "proposals", {"grant_id": grant["grant_id"], "intent": state["intent"]})
    if not proposal.get("proposal_id") or proposal["result"]["extraction"]["status"] != "PROPOSED":
        raise RuntimeError("ACTUAL_MODEL_DID_NOT_PROPOSE_SUPPORTED_PURCHASE")
    if proposal["result"]["extraction"]["proposal"] != {"product_id": "workspace-seat", "beneficiary": "alice",
            "quantity": 1, "spending_limit_minor": 100, "currency": "USD"}:
        raise RuntimeError("ACTUAL_MODEL_EXTRACTION_DOES_NOT_MATCH_DEMO_INTENT")
    quote = client.post("quote", "quotes", {"proposal_id": proposal["proposal_id"]})
    action = client.post("action", "commands/propose", {"grant_id": grant["grant_id"],
        "quote_id": quote["quote_id"], "currency": "USD"}, 0)
    state["action_id"] = action["action_id"]
    state["human_approval_required"] = True
    persist(state)
    review = client.get("actions/" + action["action_id"])
    if (not review["current"] or not review["fresh"] or not review["decision"]["allowed"]
            or not review["decision"]["approval_required"] or review["currency"] != "USD"
            or review["amount"] != 100):
        raise RuntimeError("EXACT_DEMO_POLICY_PREFLIGHT_NOT_PERMITTED")
    return {"action_id": action["action_id"], "amount_minor": action["amount"], "currency": "USD",
            "policy_result": review["decision"], "human_approver": PEOPLE["approver"],
            "proposal": proposal["result"], "quote_id": quote["quote_id"], "quote_expires_at": quote["quote"]["expires_at"],
            "provider_order_created": False}


def refresh_quote(client):
    """One explicit renewal, retaining all old identities; never admits funds."""
    state = client.state
    if state.get("operation_id") or "admission" in state["purchase_commands"]:
        raise RuntimeError("RENEWAL_REQUIRES_NO_ADMISSION_ATTEMPT")
    old_id = state.setdefault("expired_action_id", state["action_id"])
    old = client.get("actions/" + old_id)
    approvals = [a for a in client.get("authority")["approvals"] if a["action_id"] == old_id]
    if approvals or old["fresh"]:
        raise RuntimeError("RENEWAL_REQUIRES_EXPIRED_UNAPPROVED_ACTION")
    if "expired_attempt_snapshot" not in state:
        path = PRIVATE / ("m2-expired-attempt-" + uuid4().hex + ".json")
        with path.open("x", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
        state["expired_attempt_snapshot"] = path.name
        persist(state)
    proposal = state["purchase_commands"]["proposal"]["response"]
    quote = client.post("renewal_quote_1", "quote-renewals", {"proposal_id": proposal["proposal_id"]})
    terms = quote["quote"]
    old_terms = old["terms"]["quote"]
    for field in ("amount", "currency", "merchant_id", "provider", "seller", "tenant_id"):
        if terms[field] != old_terms[field]:
            raise RuntimeError("RENEWED_QUOTE_TERMS_MISMATCH")
    if (len(terms["lines"]) != 1 or len(old_terms["lines"]) != 1
            or any(terms["lines"][0][key] != old_terms["lines"][0][key]
                   for key in ("sku", "unit_price", "beneficiary"))
            or terms["id"] == old_terms["id"]):
        raise RuntimeError("RENEWED_QUOTE_TERMS_MISMATCH")
    grant = state["purchase_commands"]["grant"]["response"]
    action = client.post("renewal_action_1", "commands/propose", {
        "grant_id": grant["grant_id"], "quote_id": quote["quote_id"], "currency": "USD"}, 0)
    review = client.get("actions/" + action["action_id"])
    if (not review["current"] or not review["fresh"] or not review["decision"]["allowed"]
            or not review["decision"]["approval_required"] or review["currency"] != "USD"
            or review["amount"] != 100 or action["action_id"] == old_id):
        raise RuntimeError("RENEWED_ACTION_POLICY_NOT_PERMITTED")
    state.update(action_id=action["action_id"], active_quote_command="renewal_quote_1", human_approval_required=True)
    persist(state)
    return {"classification": "FRESH_SIGNED_QUOTE_NO_RESERVATION_OR_PROVIDER_MUTATION",
            "expired_action_id": old_id, "expired_action_review": old, "expired_action_approvals": approvals,
            "action_review": review, "quote_expires_at": terms["expires_at"],
            "model_invoked_again": False, "financial_admission_attempted": False}


def reserve(client):
    state = client.state
    prior = state["purchase_commands"].get("admission", {}).get("response", {})
    if not state.get("operation_id") and prior.get("operation_id"):
        state.update(operation_id=prior["operation_id"], human_approval_required=False)
        persist(state)
    if state.get("operation_id"):
        return client.get("evidence/" + state["operation_id"], "issuer")
    if not state.get("action_id"):
        raise RuntimeError("REAL_PROPOSAL_AND_ACTION_REQUIRED")
    action = client.get("actions/" + state["action_id"])
    if not action["current"] or not action["fresh"]:
        raise RuntimeError("FRESH_EXACT_ACTION_REQUIRED_BEFORE_RESERVATION")
    exact = {"action_id": action["id"], "evidence_id": action["evidence"]["id"], "decision_id": action["decision"]["id"]}
    snapshot = client.get("authority")
    approved = [a for a in snapshot["approvals"] if all(a[k] == exact[k] for k in exact)
                and a["approver"] == PEOPLE["approver"] and a["effective_status"] == "AVAILABLE"]
    exact_available = [a for a in snapshot["approvals"] if all(a[k] == exact[k] for k in exact)
                       and a["effective_status"] == "AVAILABLE"]
    if len(exact_available) > 1:
        raise RuntimeError("EXACT_APPROVAL_AMBIGUOUS")
    if len(approved) != 1 or len(exact_available) != 1:
        raise RuntimeError("INDEPENDENT_EXACT_HUMAN_APPROVAL_REQUIRED")
    admission = client.post("admission", "commands/admit", {**exact, "approval_id": approved[0]["id"], "currency": "USD"},
                            action["head"]["revision"])
    state.update(operation_id=admission["operation_id"], human_approval_required=False)
    persist(state)
    return client.get("evidence/" + state["operation_id"], "issuer")


def provider_read(client):
    if client.state.get("provider_binding_read_attempted"):
        raise RuntimeError("PROVIDER_PREFLIGHT_ALREADY_ATTEMPTED_NO_AUTOMATIC_RETRY")
    config = dotenv_values(PRIVATE / "m2-runtime-api.env")
    client.state["provider_binding_read_attempted"] = True
    persist(client.state)
    result = client.http.post("http://127.0.0.1:18102/v1/internal/provider-preflight",
                             headers={"Authorization": "Bearer " + str(config["PAYPAL_INTERNAL_TOKEN"])})
    evidence = result.json()
    client.state["provider_binding_diagnostic"] = evidence
    persist(client.state)
    if result.status_code != 200 or not evidence.get("verified"):
        raise RuntimeError("PAYPAL_CANONICAL_MERCHANT_BINDING_NOT_VERIFIED")
    return evidence


def report(client):
    state = client.state
    out = {"classification": "PROTECTED_PREFLIGHT_NO_NEW_PAYPAL_ORDER", "recorded_at": datetime.now(UTC).isoformat(),
           "merchant_id": state["merchant_id"], "currency": "USD", "amount_minor": 100, "tenant_id": TENANT,
           "action_id": state.get("action_id"), "operation_id": state.get("operation_id"),
           "human_approval_required": state.get("human_approval_required", True),
           "provider_binding_diagnostic": state.get("provider_binding_diagnostic"),
           "genuine_paypal_order_mutations": 0, "genuine_paypal_captures": 0}
    with connect(DATABASES["control"]) as db:
        gates = db.execute("SELECT enabled FROM paypal_v1.deployment").fetchall()
        mutation = db.execute("SELECT real_mutations_enabled FROM dispatch.integrations WHERE tenant_id=%s", (TENANT,)).fetchall()
    broker = dotenv_values(PRIVATE / "m2-runtime-broker.env")
    web = dotenv_values(PRIVATE / "m2-runtime-web.env")
    if (gates != [(True,)] or mutation != [(False,)]
            or broker.get("PAYPAL_PROVIDER_MUTATIONS_ENABLED") != "false"
            or web.get("PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED") != "false"):
        raise RuntimeError("PREFLIGHT_REQUIRES_AUTHORITY_ENABLED_AND_ALL_PROVIDER_LOCKS_CLOSED")
    out["gates"] = {"authority_enabled": True, "sql_provider_mutations": False,
                    "broker_provider_mutations": False, "browser_payment_actions": False}
    if state.get("action_id"):
        out["action_review"] = client.get("actions/" + state["action_id"])
        out["independent_approvals"] = [a for a in client.get("authority")["approvals"]
                                        if a["action_id"] == state["action_id"]]
        pid = state["purchase_commands"][state.get("active_proposal_command", "proposal")]["response"]["proposal_id"]
        out["recorded_proposal"] = client.get("proposals/" + pid)
        out["signed_quote_terms"] = state["purchase_commands"][state.get("active_quote_command", "quote")]["response"]["quote"]
        with connect(DATABASES["control"]) as db:
            gid = state["purchase_commands"]["grant"]["response"]["grant_id"]
            out["budget_vector_B_C_R_F"] = db.execute("SELECT jsonb_build_array(bound,consumed,reserved,free) FROM authority.grant_heads WHERE grant_id=%s AND tenant_id=%s", (gid, TENANT)).fetchone()[0]
    if state.get("operation_id"):
        op = state["operation_id"]
        out["proof"] = client.get("evidence/" + op, "issuer")
        with connect(DATABASES["control"]) as db:
            binding = db.execute("SELECT create_request_id::text,capture_request_id::text,wire_digest,wire_body FROM paypal_v1.bindings WHERE operation_id=%s", (op,)).fetchone()
            if not binding or UUID(binding[0]) == UUID(binding[1]):
                raise RuntimeError("DISTINCT_DURABLE_PHASE_IDENTITIES_REQUIRED")
            unit = binding[3]["purchase_units"][0]
            if (binding[3] != protected_body(100, unit["reference_id"], VERIFIED_US_MERCHANT)
                    or out["proof"]["merchant_id"] != VERIFIED_US_MERCHANT
                    or out["proof"]["amount_minor"] != 100 or out["proof"]["currency"] != "USD"):
                raise RuntimeError("EXACT_US_MERCHANT_AND_USD_TERMS_REQUIRED")
            out["phase_identities"] = {"create_request_id": binding[0], "capture_request_id": binding[1], "wire_digest": binding[2]}
            out["wire_body"] = binding[3]
            out["provider_phase_commits"] = db.execute("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=%s", (op,)).fetchone()[0]
            out["budget_vector_B_C_R_F"] = db.execute("SELECT jsonb_build_array(bound,consumed,reserved,free) FROM authority.grant_heads WHERE grant_id=(SELECT grant_id FROM dispatch.business_operations WHERE id=%s)", (op,)).fetchone()[0]
            if out["provider_phase_commits"] or out["proof"]["provider_order_id"] or out["budget_vector_B_C_R_F"] != [100, 0, 100, 0]:
                raise RuntimeError("PREFLIGHT_STATE_MUST_BE_RESERVED_WITHOUT_PROVIDER_HANDOFF")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "refresh-quote", "reserve", "provider-read", "report"))
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    client = None
    try:
        checked_demo()
        if (PRIVATE / "m2-legacy-active.json").exists():
            raise RuntimeError("WAIT_FOR_LEGACY_PASSWORD_RESTORATION")
        client = DemoClient(json.loads(STATE.read_text(encoding="utf-8")))
        result = {"prepare": prepare, "refresh-quote": refresh_quote, "reserve": reserve,
                  "provider-read": provider_read, "report": report}[args.command](client)
        if args.export:
            path = ROOT / "docs/hackathon/evidence" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-m2-" + args.command + ".json")
            # Bounded application contracts contain no login/provider secrets.
            path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"evidence": path.relative_to(ROOT).as_posix(), "result": result}, indent=2))
        else:
            print(json.dumps(result, indent=2))
    except Exception as error:
        value = str(error)
        print(json.dumps({"blocked": value if re.fullmatch(r"[A-Z_]{3,100}", value) else "M2_PREFLIGHT_FAILED_PRESERVE_IDENTITIES"}))
        raise SystemExit(1) from None
    finally:
        if client:
            client.close()
