"""One explicitly authorized NEW sandbox test; never approves on a human's behalf.

Uses the unchanged v1 APIs and distinct persisted local command identities.
No migration, release, continuation or fresh-identity provider retry is exposed.
"""

import argparse
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tools.hackathon.disposable_postgres import DATABASES, PRIVATE, ROOT, connect
from tools.hackathon.m2_demo import PEOPLE, TENANT, checked_demo, write_private
from tools.hackathon.m2_preflight import DemoClient

RUN = PRIVATE / "m2015-purchase.json"
OLD_OPERATION = "op_ad9410eb8f604b28bc6c31f74836e2e9"
OLD_ORDER = "5NV48695TN707743S"
MERCHANT = "JB7TR6VHB4GTQ"
EXPECTED = {"product_id": "workspace-seat", "beneficiary": "alice", "quantity": 1,
            "spending_limit_minor": 100, "currency": "USD"}


def save(state):
    temporary = PRIVATE / ("m2015-state-" + uuid4().hex + ".pending")
    write_private(temporary, json.dumps(state, indent=2) + "\n")
    os.replace(temporary, RUN)


def load():
    state = json.loads(RUN.read_text())
    if state["scope"] != EXPECTED or state["merchant_id"] != MERCHANT or state["preserved_operation_id"] != OLD_OPERATION:
        raise RuntimeError("M2015_EXACT_SCOPE_REQUIRED")
    return state


@contextmanager
def exclusive():
    path = PRIVATE / "m2015-execution.lock"
    with path.open("x", encoding="utf8") as file:
        file.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def export(stage, value):
    path = ROOT / "docs/hackathon/evidence" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-m2015-" + stage + ".json")
    with path.open("x", encoding="utf8") as file:
        json.dump(value, file, indent=2)
        file.write("\n")
    return path.relative_to(ROOT).as_posix()


class Client(DemoClient):
    def post(self, name, path, body, revision=None, role="requester"):
        allowed = {"commands/create-grant", "commands/activate-grant", "proposals", "quotes", "commands/propose",
                   "commands/admit", "create", "consent", "observe", "capture-requests", "reconciliation/runs"}
        if (path == "quote-renewals" and self.state.get("retry_authorization") == "DIRECTIVE_015B"
                and self.state.get("expired_action_id") == "action_92320a20-9d71-409b-a5f0-b776a59d67b5"
                and self.state.get("operator_ready_at")
                and not self.state.get("operation_id")):
            allowed.add("quote-renewals")
        if path not in allowed or role not in {"issuer", "requester"}:
            raise RuntimeError("M2015_COMMAND_NOT_EXPOSED")
        if path in {"create", "consent", "observe", "capture-requests"}:
            require_operation(self.state, body.get("operation_id"))
            if body != {"operation_id": self.state["operation_id"], "currency": "USD"}:
                raise RuntimeError("M2015_EXACT_OPERATION_BODY_REQUIRED")
        prior = self.state["commands"].get(name)
        if prior:
            if (prior["path"], prior["body"], prior["revision"], prior["role"]) != (path, body, revision, role):
                raise RuntimeError("M2015_PERSISTED_COMMAND_CONFLICT")
            if "response" in prior:
                return prior["response"]
            if prior.get("sent"):
                raise RuntimeError("M2015_UNKNOWN_COMMAND_NO_AUTOMATIC_RETRY")
        else:
            prior = self.state["commands"][name] = {"path": path, "body": body, "revision": revision,
                        "role": role, "idempotency_key": "m2015_" + uuid4().hex}
        headers = {"Authorization": "Bearer " + self.token(role), "Idempotency-Key": prior["idempotency_key"]}
        if revision is not None:
            headers["If-Match"] = str(revision)
        prior.update(sent=True, sent_at=datetime.now(UTC).isoformat())
        save(self.state)  # Durable BEFORE any request, including model and grant.
        response = self.http.post("http://127.0.0.1:18101/v1/paypal/" + path, json=body, headers=headers)
        prior["http_status"] = response.status_code
        value = response.json()
        if response.status_code != 200:
            code = value.get("detail", "M2015_COMMAND_REJECTED")
            prior["error"] = code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{3,100}", code) else "M2015_COMMAND_REJECTED"
            prior["diagnostic"] = value.get("diagnostic", {})
            save(self.state)
            raise RuntimeError(prior["error"])
        prior["response"] = value
        save(self.state)
        return value


def require_operation(state, opid):
    if not opid or opid != state.get("operation_id") or opid == OLD_OPERATION:
        raise RuntimeError("M2015_NEW_OPERATION_ONLY")


def old_fingerprint():
    with connect(DATABASES["control"]) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = db.execute("""SELECT jsonb_build_object('operation',to_jsonb(o),'binding',to_jsonb(b),
          'runtime',to_jsonb(r),'grant',to_jsonb(g),'grant_head',to_jsonb(gh),'grant_version',to_jsonb(gv),
          'reservation',to_jsonb(res),'reservation_head',to_jsonb(rh),'approval',to_jsonb(ap),'approval_head',to_jsonb(ah),
          'quote',to_jsonb(q),'phases',(SELECT jsonb_agg(to_jsonb(p) ORDER BY phase) FROM paypal_v1.phase_commits p WHERE operation_id=o.id),
          'captures',(SELECT jsonb_agg(to_jsonb(c)) FROM paypal_v1.capture_requests c WHERE operation_id=o.id))
          FROM dispatch.business_operations o JOIN paypal_v1.bindings b ON b.operation_id=o.id JOIN paypal_v1.runtime r ON r.operation_id=o.id
          JOIN authority.grants g ON g.id=o.grant_id JOIN authority.grant_heads gh ON gh.grant_id=g.id
          JOIN authority.grant_versions gv ON gv.id=o.grant_version_id
          JOIN authority.reservations res ON res.id=o.reservation_id JOIN authority.reservation_heads rh ON rh.reservation_id=res.id
          JOIN authority.approvals ap ON ap.id=o.approval_id JOIN authority.approval_heads ah ON ah.approval_id=ap.id
          JOIN authority.evidence_revisions e ON e.id=o.evidence_id JOIN authority.seller_quotes q ON q.id=e.quote_id
          WHERE o.id=%s""", (OLD_OPERATION,)).fetchone()[0]
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()


def assert_preserved(state):
    if old_fingerprint() != state["old_fingerprint"]:
        raise RuntimeError("M2015_OLD_FINANCIAL_CASE_CHANGED_STOP")


def freshness(state, minimum_seconds=0):
    for key in ("grant_expires_at", "service_expires_at"):
        if datetime.fromisoformat(state[key]) <= datetime.now(UTC) + timedelta(seconds=minimum_seconds):
            raise RuntimeError("M2015_AUTHORITY_EXPIRED_OR_TOO_SHORT")
    if state.get("quote_expires_at") and datetime.fromisoformat(state["quote_expires_at"]) <= datetime.now(UTC):
        raise RuntimeError("M2015_QUOTE_EXPIRED_STOP")


def setup_grant(client):
    state = client.state
    if state.get("retry_authorization"):
        raise RuntimeError("M2015B_REUSE_EXISTING_GRANT_ONLY")
    assert_preserved(state)
    if not state.get("grant_expires_at"):
        with connect(DATABASES["control"]) as db:
            db.execute("SET TRANSACTION READ ONLY")
            usable = db.execute("""SELECT count(*) FROM authority.grant_heads h JOIN authority.grant_versions v ON v.id=h.version_id
              JOIN authority.grants g ON g.id=h.grant_id WHERE g.tenant_id=%s AND g.subject=%s
              AND h.status IN ('ACTIVE','DRAFT') AND v.expires_at>clock_timestamp()""", (TENANT, PEOPLE["requester"])).fetchone()[0]
        if usable:
            raise RuntimeError("M2015_EXISTING_USABLE_AUTHORITY_REQUIRES_INSPECTION")
        state["grant_expires_at"] = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        save(state)
    grant = client.post("grant", "commands/create-grant", {"kind": "PURCHASE", "subject": PEOPLE["requester"],
        "lifetime_bound": 100, "currency": "USD", "beneficiaries": ["alice"],
        "expires_at": state["grant_expires_at"], "resource_id": None}, 0, "issuer")
    state["grant_id"] = grant["grant_id"]
    save(state)
    client.post("activate", "commands/activate-grant", {"grant_id": state["grant_id"], "currency": "USD"}, 1, "issuer")
    assert_preserved(state)
    return {"grant_id": state["grant_id"], "expires_at": state["grant_expires_at"], "currency": "USD", "bound_minor": 100}


def prepare(client):
    state = client.state
    if not state.get("operator_ready_at") or state.get("operation_id"):
        raise RuntimeError("M2015_READY_OPERATOR_AND_NO_PRIOR_OPERATION_REQUIRED")
    if datetime.now(UTC)-datetime.fromisoformat(state["operator_ready_at"]) > timedelta(minutes=5):
        raise RuntimeError("M2015_OPERATOR_READINESS_TOO_OLD")
    freshness(state, 1800)
    assert_preserved(state)
    proposal = client.post("proposal", "proposals", {"grant_id": state["grant_id"],
        "intent": "Propose one workspace-seat for alice, maximum USD 1.00. Currency USD."})
    result = proposal["result"]
    if (not proposal.get("proposal_id") or result["financial_authority"] is not False
            or result["extraction"]["proposal"] != EXPECTED or result["provenance"]["actual_model_invocation"] is not True
            or result["provenance"]["http_status"] != 200 or result["provenance"]["schema_validation"] != "PASSED"):
        raise RuntimeError("M2015_REAL_MODEL_EXACT_PROPOSAL_REQUIRED")
    retry = state.get("retry_authorization") == "DIRECTIVE_015B"
    if retry:
        # Refreshing seller evidence is an explicit operator instruction. The
        # existing renewal API does not transfer any approval or reservation.
        with connect(DATABASES["control"]) as db:
            db.execute("SET TRANSACTION READ ONLY")
            count = db.execute("""SELECT (SELECT count(*) FROM authority.approvals WHERE action_id=%s)
              +(SELECT count(*) FROM authority.reservations WHERE action_id=%s)
              +(SELECT count(*) FROM dispatch.business_operations WHERE action_id=%s)""",
              (state["expired_action_id"],)*3).fetchone()[0]
        if count:
            raise RuntimeError("M2015B_EXPIRED_ATTEMPT_HAS_AUTHORITY_EFFECTS_STOP")
    quote = client.post("quote", "quote-renewals" if retry else "quotes", {"proposal_id": proposal["proposal_id"]})
    terms = quote["quote"]
    if (terms["currency"] != "USD" or terms["amount"] != 100 or terms["merchant_id"] != MERCHANT
            or terms["provider"] != "PAYPAL_SANDBOX" or len(terms["lines"]) != 1
            or any(terms["lines"][0][k] != v for k, v in {"sku": "workspace-seat", "beneficiary": "alice", "unit_price": 100}.items())):
        raise RuntimeError("M2015_EXACT_SIGNED_SELLER_TERMS_REQUIRED")
    state.update(quote_id=quote["quote_id"], quote_expires_at=terms["expires_at"], allocation_id=terms["lines"][0]["allocation_id"])
    if retry and (state["quote_id"] == state["expired_quote_id"] or state["allocation_id"] == state["expired_allocation_id"]):
        raise RuntimeError("M2015B_DISTINCT_NEW_QUOTE_AND_ALLOCATION_REQUIRED")
    save(state)
    with connect(DATABASES["seller"]) as db:
        db.execute("SET TRANSACTION READ ONLY")
        existing = db.execute("SELECT count(*) FROM seller_truth.entitlements WHERE id=%s", (state["allocation_id"],)).fetchone()[0]
        duplicate = db.execute("SELECT count(*) FROM public.quotes WHERE id<>%s AND body->'lines'->0->>'allocation_id'=%s",
                               (state["quote_id"], state["allocation_id"])).fetchone()[0]
    if existing or duplicate:
        raise RuntimeError("M2015_DISTINCT_UNFULFILLED_ALLOCATION_REQUIRED")
    action = client.post("action", "commands/propose", {"grant_id": state["grant_id"], "quote_id": state["quote_id"], "currency": "USD"}, 0)
    state["action_id"] = action["action_id"]
    save(state)
    review = client.get("actions/" + state["action_id"])
    if not (review["current"] and review["fresh"] and review["decision"]["allowed"] and review["decision"]["approval_required"]):
        raise RuntimeError("M2015_CURRENT_EXACT_POLICY_REQUIRED")
    payload = {"classification": "GENUINE_MODEL_SIGNED_QUOTE_POLICY_AWAITING_HUMAN_APPROVAL", "recorded_at": datetime.now(UTC).isoformat(),
        "action": review, "proposal": proposal, "quote": quote, "grant_id": state["grant_id"],
        "quote_expires_at": state["quote_expires_at"], "reservation_created": False, "provider_order_created": False}
    payload.update(model_invoked_for_this_attempt=not retry, prior_genuine_model_proposal_reused=retry,
                   expired_action_preserved=state.get("expired_action_id"))
    state["preparation_evidence"] = export("fresh-preparation", payload)
    save(state)
    return {"action_id": state["action_id"], "quote_expires_at": state["quote_expires_at"],
            "allocation_id": state["allocation_id"], "evidence": state["preparation_evidence"]}


def binding(state):
    require_operation(state, state.get("operation_id"))
    with connect(DATABASES["control"]) as db:
        db.execute("SET TRANSACTION READ ONLY")
        row = db.execute("""SELECT o.grant_id,o.action_id,o.reservation_id,b.create_request_id::text,b.capture_request_id::text,b.wire_digest,b.wire_body,
          o.account_id,o.amount,o.currency FROM dispatch.business_operations o JOIN paypal_v1.bindings b ON b.operation_id=o.id
          WHERE o.id=%s AND o.tenant_id=%s""", (state["operation_id"], TENANT)).fetchone()
    if not row or (row[0], row[1], row[7], row[8], row[9]) != (state["grant_id"], state["action_id"], MERCHANT, 100, "USD"):
        raise RuntimeError("M2015_CANONICAL_OPERATION_BINDING_CONFLICT")
    value = dict(zip(("grant_id", "action_id", "reservation_id", "create_request_id", "capture_request_id", "wire_digest", "wire_body"), row[:7], strict=True))
    if row[3] == row[4] or row[3] == "60ad3d9a-912a-442b-87d7-c51e78049994" or row[4] == "2911cf1b-7d1b-400d-9dab-72c662a443a6":
        raise RuntimeError("M2015_DISTINCT_PROVIDER_IDENTITIES_REQUIRED")
    if state.get("binding") and state["binding"] != value:
        raise RuntimeError("M2015_IMMUTABLE_PROVIDER_BINDING_CHANGED")
    state["binding"] = value
    save(state)
    return value


@contextmanager
def mutation_window(state, phase):
    freshness(state)
    binding(state)
    assert_preserved(state)
    if phase == "CAPTURE" and not state.get("human_capture_authorization"):
        raise RuntimeError("M2015_SEPARATE_HUMAN_CAPTURE_AUTHORIZATION_REQUIRED")
    with connect(DATABASES["control"]) as db:
        if db.execute("SELECT real_mutations_enabled FROM dispatch.integrations WHERE tenant_id=%s", (TENANT,)).fetchall() != [(False,)]:
            raise RuntimeError("M2015_PROVIDER_GATE_WAS_NOT_CLOSED")
        db.execute("UPDATE dispatch.integrations SET real_mutations_enabled=true WHERE tenant_id=%s", (TENANT,))
    try:
        yield
    finally:
        with connect(DATABASES["control"]) as db:
            db.execute("UPDATE dispatch.integrations SET real_mutations_enabled=false WHERE tenant_id=%s", (TENANT,))


def watch(client):
    state = client.state
    if state.get("operation_id") or state.get("order_id"):
        raise RuntimeError("M2015_NO_SECOND_ADMISSION_OR_ORDER")
    if state["commands"].get("admission", {}).get("sent"):
        raise RuntimeError("M2015_ADMISSION_ALREADY_ATTEMPTED_INSPECT_CANONICALLY")
    while True:
        freshness(state)
        action = client.get("actions/" + state["action_id"])
        if not action["current"] or not action["fresh"]:
            raise RuntimeError("M2015_APPROVAL_ACTION_EXPIRED_OR_STALE")
        exact = {"action_id": action["id"], "evidence_id": action["evidence"]["id"], "decision_id": action["decision"]["id"]}
        matches = [ap for ap in client.get("authority")["approvals"]
                   if all(ap[k] == v for k, v in exact.items()) and ap["effective_status"] == "AVAILABLE"]
        if len(matches) > 1:
            raise RuntimeError("M2015_AMBIGUOUS_APPROVAL_STOP")
        if matches:
            if matches[0]["approver"] != PEOPLE["approver"]:
                raise RuntimeError("M2015_INDEPENDENT_APPROVER_MISMATCH")
            state["approval"] = matches[0]
            save(state)
            admitted = client.post("admission", "commands/admit", {**exact, "approval_id": matches[0]["id"], "currency": "USD"}, action["head"]["revision"])
            state["operation_id"] = admitted["operation_id"]
            save(state)
            break
        time.sleep(1)
    binding(state)
    export("admission", {"classification": "GENUINE_HUMAN_APPROVAL_PROTECTED_ADMISSION", "approval": state["approval"],
                          "binding": state["binding"], "proof": client.get("evidence/" + state["operation_id"], "issuer")})
    body = {"operation_id": state["operation_id"], "currency": "USD"}
    with mutation_window(state, "CREATE"):
        result = client.post("protected_create", "create", body)
    proof = client.get("evidence/" + state["operation_id"], "issuer")
    if proof["blocked"] or not proof["provider_order_id"] or result.get("knowledge") == "UNKNOWN":
        raise RuntimeError("M2015_CREATE_UNKNOWN_OR_BLOCKED_NO_RETRY")
    state["order_id"] = proof["provider_order_id"]
    save(state)
    consent = client.post("checkout_disclosure", "consent", body, proof["dispatch_revision"], "issuer")
    if consent["approval_url"] != "https://www.sandbox.paypal.com/checkoutnow?token=" + state["order_id"]:
        raise RuntimeError("M2015_CHECKOUT_URL_BINDING_CONFLICT")
    state["approval_url"] = consent["approval_url"]
    save(state)
    sanitized_consent = {k: v for k, v in consent.items() if k != "approval_url"}
    evidence = export("protected-order", {"classification": "GENUINE_PROTECTED_ORDER_AWAITING_BUYER_CONSENT",
                      "operation_id": state["operation_id"], "proof": proof, "binding": state["binding"], "consent": sanitized_consent})
    assert_preserved(state)
    return {"operation_id": state["operation_id"], "order_id": state["order_id"], "approval_url": state["approval_url"],
            "quote_expires_at": state["quote_expires_at"], "capture_authorized": False, "evidence": evidence}


def consent_read(client):
    state = client.state
    binding(state)
    result = client.post("after_buyer_consent", "observe", {"operation_id": state["operation_id"], "currency": "USD"}, role="issuer")
    proof = client.get("evidence/" + state["operation_id"], "issuer")
    if (proof["provider_order_id"] != state["order_id"] or proof["provider_order_status"] != "APPROVED"
            or proof["capture_confirmed"] or proof["blocked"] or result.get("knowledge") == "UNKNOWN"):
        raise RuntimeError("M2015_FRESH_CANONICAL_APPROVED_ORDER_REQUIRED")
    state["consent_evidence"] = export("buyer-consent", {"classification": "GENUINE_CANONICAL_BUYER_APPROVAL_NO_CAPTURE", "proof": proof, "binding": state["binding"]})
    save(state)
    freshness(state)  # Even late consent is recorded truthfully, never captured.
    return {"operation_id": state["operation_id"], "order_id": state["order_id"], "merchant_id": MERCHANT,
            "amount_minor": 100, "currency": "USD", "capture_request_id": state["binding"]["capture_request_id"],
            "quote_expires_at": state["quote_expires_at"], "evidence": state["consent_evidence"]}


def capture_once(client):
    state = client.state
    freshness(state)
    binding(state)
    expected = {"operation_id": state["operation_id"], "order_id": state["order_id"], "merchant_id": MERCHANT,
                "amount_minor": 100, "currency": "USD", "capture_request_id": state["binding"]["capture_request_id"]}
    authorization = state.get("human_capture_authorization", {})
    if any(authorization.get(k) != v for k, v in expected.items()) or not authorization.get("confirmed_at"):
        raise RuntimeError("M2015_EXACT_HUMAN_CAPTURE_DECISION_REQUIRED")
    # Independent read immediately before issuer authorization, then the driver
    # reads AGAIN and the SQL send cutoff repeats the financial checks.
    client.post("pre_capture_canonical_read", "observe", {"operation_id": state["operation_id"], "currency": "USD"}, role="issuer")
    proof = client.get("evidence/" + state["operation_id"], "issuer")
    if proof["provider_order_id"] != state["order_id"] or proof["provider_order_status"] != "APPROVED" or proof["blocked"] or proof["capture_confirmed"]:
        raise RuntimeError("M2015_PRE_CAPTURE_PROVIDER_STATE_INVALID")
    with mutation_window(state, "CAPTURE"):
        result = client.post("protected_capture", "capture-requests", {"operation_id": state["operation_id"], "currency": "USD"}, proof["revision"], "issuer")
    if result.get("capture_confirmed") is not True:
        raise RuntimeError("M2015_CAPTURE_UNKNOWN_NO_REPLAY_HOLD_RETAINED")
    client.post("independent_post_capture_read", "observe", {"operation_id": state["operation_id"], "currency": "USD"}, role="issuer")
    until = time.monotonic()+45
    while True:
        proof = client.get("evidence/" + state["operation_id"], "issuer")
        if proof["capture_confirmed"] is not True or proof["provider_order_status"] != "COMPLETED" or proof["blocked"]:
            raise RuntimeError("M2015_INDEPENDENT_COMPLETED_CAPTURE_NOT_VERIFIED")
        if proof["seller_fulfilled"] and len(proof["journals"]) == 3:
            break
        if time.monotonic() >= until:
            raise RuntimeError("M2015_CAPTURE_VERIFIED_ACCOUNTING_OR_FULFILLMENT_INCOMPLETE")
        time.sleep(1)
    for journal in proof["journals"]:
        lines = journal["lines"]
        if any(line["currency"] != "USD" for line in lines) or sum(line["amount_minor"] for line in lines if line["side"] == "DEBIT") != sum(line["amount_minor"] for line in lines if line["side"] == "CREDIT"):
            raise RuntimeError("M2015_JOURNAL_BALANCE_OR_CURRENCY_MISMATCH")
    reconciliation = client.post("reconciliation", "reconciliation/runs", {"request_id": state["reconciliation_id"], "batch_size": 50}, role="issuer")
    assert_preserved(state)
    result = {"classification": "GENUINE_PROTECTED_NEW_PAYPAL_SANDBOX_PURCHASE", "recorded_at": datetime.now(UTC).isoformat(),
              "operation_id": state["operation_id"], "model": state["commands"]["proposal"]["response"],
              "quote": state["commands"]["quote"]["response"], "action": state["commands"]["action"]["response"],
              "approval": state["approval"], "binding": state["binding"], "human_capture_authorization": authorization,
              "proof": proof, "reconciliation": reconciliation, "old_case_fingerprint_preserved": True}
    state["completion_evidence"] = export("completed-purchase", result)
    state["completed"] = True
    save(state)
    return {"operation_id": state["operation_id"], "order_id": state["order_id"], "capture_id": proof["capture_id"],
            "provider_status": proof["provider_order_status"], "seller_fulfilled": proof["seller_fulfilled"],
            "evidence": state["completion_evidence"]}


def main():
    global RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("setup-grant", "prepare", "watch", "consent-read", "capture"))
    parser.add_argument("--attempt", choices=("m2015", "m2015b"), default="m2015")
    args = parser.parse_args()
    RUN = PRIVATE / (args.attempt + "-purchase.json")
    client = None
    try:
        checked_demo()
        with exclusive():
            client = Client(load())
            value = {"setup-grant": setup_grant, "prepare": prepare, "watch": watch,
                     "consent-read": consent_read, "capture": capture_once}[args.command](client)
            print(json.dumps(value), flush=True)
    except Exception as error:
        code = str(error)
        print(json.dumps({"blocked": code if re.fullmatch(r"[A-Z_0-9]{3,120}", code) else "M2015_EXECUTION_STOPPED_PRESERVE_STATE",
                          "exception_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
