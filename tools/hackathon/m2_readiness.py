"""Directive 009: bounded real preflight, then one fresh quote; no admission/PSP send."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from dotenv import dotenv_values
from interlock.paypal_flow import protected_body
from sqlalchemy import create_engine, text

from tools.hackathon.disposable_postgres import DATABASES, PRIVATE, ROOT, connect
from tools.hackathon.m2_acceptance import EXPECTED_TESTS, SUITES, source_hashes
from tools.hackathon.m2_demo import PEOPLE, STATE, TENANT, checked_demo, write_private
from tools.hackathon.m2_preflight import DemoClient, persist
from tools.hackathon.m2_services import PROCESS_STATE, runtime_environment

EXPECTED = {"product_id": "workspace-seat", "beneficiary": "alice", "quantity": 1,
            "spending_limit_minor": 100, "currency": "USD"}
MERCHANT = "JB7TR6VHB4GTQ"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(kind, result):
    path = ROOT / "docs/hackathon/evidence" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-m2009-" + kind + ".json")
    with path.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    return path


def historical_hashes(db, identifiers):
    records = {}
    for identifier in identifiers:
        value = db.execute("""SELECT jsonb_build_object('action',to_jsonb(a),'intent_head',to_jsonb(h),
          'evidence',to_jsonb(e),'decision',to_jsonb(d),'quote',to_jsonb(q),
          'approvals',(SELECT jsonb_agg(to_jsonb(ap)||jsonb_build_object('head',to_jsonb(ah)) ORDER BY ap.id)
          FROM authority.approvals ap JOIN authority.approval_heads ah ON ah.approval_id=ap.id WHERE ap.action_id=a.id))
          FROM authority.action_revisions a JOIN authority.intent_heads h ON h.intent_id=a.intent_id
          JOIN authority.evidence_revisions e ON e.action_id=a.id JOIN authority.policy_decisions d ON d.action_id=a.id
          JOIN authority.seller_quotes q ON q.id=e.quote_id WHERE a.id=%s AND a.tenant_id=%s""", (identifier, TENANT)).fetchone()
        if not value:
            raise RuntimeError("HISTORICAL_ACTION_NOT_FOUND")
        records[identifier] = hashlib.sha256(json.dumps(value[0], sort_keys=True).encode()).hexdigest()
    return records


def model(client):
    gid = client.state["purchase_commands"]["grant"]["response"]["grant_id"]
    result = client.post("m2009_model", "proposals", {"grant_id": gid, "intent": client.state["intent"]})
    extraction, provenance = result["result"]["extraction"], result["result"]["provenance"]
    if (extraction.get("proposal") != EXPECTED or extraction.get("status") != "PROPOSED"
            or provenance.get("actual_model_invocation") is not True or provenance.get("http_status") != 200
            or provenance.get("schema_validation") != "PASSED" or result["result"]["financial_authority"] is not False):
        raise RuntimeError("REAL_MODEL_PREFLIGHT_NOT_PASSED")
    client.state["active_proposal_command"] = "m2009_model"
    persist(client.state)
    return {"classification": "ACTUAL_MODEL_PROPOSAL_NO_QUOTE_OR_PAYMENT", "proposal_id": result["proposal_id"],
            "result": result["result"]}


def check(client):
    cluster, state = checked_demo(), client.state
    certificate_path = ROOT / state["acceptance_evidence"]
    certificate = json.loads(certificate_path.read_text())
    if (digest(certificate_path) != state["acceptance_sha256"] or certificate["source_sha256"] != source_hashes()
            or certificate["tests_run"] != EXPECTED_TESTS or certificate["suites"] != list(SUITES)
            or any(certificate[k] for k in ("errors", "failures", "skipped"))):
        raise RuntimeError("CURRENT_COMPLETE_ACCEPTANCE_REQUIRED")
    services, fingerprints = {}, {}
    process_state = json.loads(PROCESS_STATE.read_text())
    for role, entry in process_state["services"].items():
        path = PRIVATE / ("m2-runtime-" + role + ".env")
        fingerprints[role] = digest(path)
        if entry["profile_sha256"] != fingerprints[role]:
            raise RuntimeError("RUNTIME_PROFILE_DRIFT")
        env = runtime_environment(role)
        if role == "economic":
            heartbeat = json.loads((PRIVATE / "m2-economic-heartbeat.json").read_text())
            if (heartbeat["status"] != "CYCLE_COMPLETE" or heartbeat["pid"] != entry["actual_pid"]
                    or abs((datetime.now(UTC) - datetime.fromisoformat(heartbeat["at"])).total_seconds()) > 10):
                raise RuntimeError("ECONOMIC_WORKER_NOT_CURRENT")
        else:
            response = client.http.get(entry["endpoint"])
            if response.status_code != 200 or (role != "web" and response.json()["pid"] != entry["actual_pid"]):
                raise RuntimeError("ACTUAL_RUNTIME_SERVICE_UNAVAILABLE")
        metadata = {"pid": entry["actual_pid"], "endpoint": entry["endpoint"], "profile_sha256": fingerprints[role]}
        if role != "web":
            url = env["CONTROL_DATABASE_URL" if role == "session" else "PAYPAL_" + role.upper() + "_DATABASE_URL"]
            engine = create_engine(url, hide_parameters=True)
            try:
                with engine.connect() as db:
                    identity = db.execute(text("SELECT session_user,current_database(),current_setting('port')")).one()
                    flags = db.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=session_user")).one()
                    if (identity[0] != "interlock_" + ("api" if role == "session" else role)
                            or identity[1] != DATABASES["seller" if role == "seller" else "control"]
                            or identity[2] != "55438" or any(flags)):
                        raise RuntimeError("RUNTIME_DATABASE_ROLE_MISMATCH")
                    metadata.update(role=identity[0], database=identity[1], role_flags=list(flags))
            finally:
                engine.dispose()
        services[role] = metadata
    if len({s["pid"] for s in services.values()}) != 6:
        raise RuntimeError("DISTINCT_RUNTIME_PROCESSES_REQUIRED")
    gid = state["purchase_commands"]["grant"]["response"]["grant_id"]
    snapshot = client.get("authority", "issuer")
    grant = next(g for g in snapshot["grants"] if g["id"] == gid)
    if (grant["effective_status"] != "ACTIVE" or grant["version"]["beneficiaries"] != ["alice"]
            or datetime.fromisoformat(grant["version"]["expires_at"]) < datetime.now(UTC) + timedelta(minutes=45)
            or [grant["head"][k] for k in ("bound", "consumed", "reserved", "free")] != [100, 0, 0, 100]):
        raise RuntimeError("FRESH_EXACT_GRANT_AND_UNRESERVED_BUDGET_REQUIRED")
    history = state.setdefault("m2009_preserved_action_ids", [state["expired_action_id"], state["action_id"]])
    with connect(DATABASES["control"]) as db:
        db.execute("SET TRANSACTION READ ONLY")
        if (db.execute("SELECT enabled,disposable_acceptance_sha256 FROM paypal_v1.deployment").fetchall()
                != [(True, state["acceptance_sha256"])]
                or db.execute("SELECT real_mutations_enabled FROM dispatch.integrations WHERE tenant_id=%s", (TENANT,)).fetchall() != [(False,)]):
            raise RuntimeError("AUTHORITY_CERTIFICATE_AND_PROVIDER_LOCK_REQUIRED")
        if (db.execute("SELECT count(*) FROM authority.reservations WHERE tenant_id=%s", (TENANT,)).fetchone()[0]
                or db.execute("SELECT count(*) FROM dispatch.business_operations WHERE tenant_id=%s", (TENANT,)).fetchone()[0]):
            raise RuntimeError("DEMO_MUST_HAVE_NO_PRIOR_RESERVATION_OR_OPERATION")
        members = db.execute("SELECT principal,roles,revision,active FROM authority.memberships WHERE tenant_id=%s ORDER BY principal", (TENANT,)).fetchall()
        expected_members = {PEOPLE[k]: ["PURCHASE_" + k.upper()] for k in PEOPLE}
        if (len(members) != 3 or any(expected_members.get(m[0]) != m[1] or not m[3] for m in members)):
            raise RuntimeError("INDEPENDENT_EXACT_RUNTIME_ROLES_REQUIRED")
        credentials = db.execute("""SELECT c.id,c.active,c.expires_at,c.token_hash,s.verbs FROM dispatch.service_credentials c
          JOIN dispatch.service_scopes s ON s.credential_id=c.id AND s.tenant_id=%s
          WHERE c.id IN ('m2_mutate','m2_observe') ORDER BY c.id""", (TENANT,)).fetchall()
        broker_profile = runtime_environment("broker")
        expected_scopes = {"m2_mutate": (["DISPATCH"], "DISPATCH_MUTATE_TOKEN"),
                           "m2_observe": (["OBSERVE"], "DISPATCH_OBSERVE_TOKEN")}
        if len(credentials) != 2 or any(not c[1] or c[4] != expected_scopes[c[0]][0]
                or c[3] != hashlib.sha256(broker_profile[expected_scopes[c[0]][1]].encode()).hexdigest() for c in credentials):
            raise RuntimeError("BROKER_SERVICE_AUTHORIZATION_BINDING_MISMATCH")
        lease = min(c[2] for c in credentials)
        if not lease or lease < datetime.now(UTC) + timedelta(minutes=30):
            raise RuntimeError("SERVICE_AUTHORIZATION_LEASE_TOO_SHORT")
        clock = db.execute("SELECT clock_timestamp()").fetchone()[0]
        if abs((clock - datetime.now(UTC)).total_seconds()) > 3:
            raise RuntimeError("DATABASE_APPLICATION_CLOCK_MISMATCH")
        hashes = historical_hashes(db, history)
        canonical_channel = db.execute("SELECT account_id,integration,active,secret FROM economic.channels WHERE id=%s",
                                       ("paypal-" + TENANT,)).fetchone()
        seller_channel = db.execute("SELECT secret,active FROM economic.channels WHERE id=%s", ("seller-" + TENANT,)).fetchone()
        capability_key = db.execute("SELECT secret FROM economic.channels WHERE id='seller-capability-key'").fetchone()[0]
        quote_key = db.execute("SELECT secret FROM authority.issuer_keys WHERE name='paypal-seller'").fetchone()[0]
        if (not canonical_channel or canonical_channel[:3] != (MERCHANT, state["integration"], True)
                or canonical_channel[3] != broker_profile["PAYPAL_CANONICAL_SIGNING_KEY"] or not seller_channel or not seller_channel[1]):
            raise RuntimeError("AUTHENTICATED_ECONOMIC_CHANNEL_MISMATCH")
    with connect(DATABASES["seller"]) as seller_db:
        seller_db.execute("SET TRANSACTION READ ONLY")
        keys = dict(seller_db.execute("SELECT name,secret FROM seller_truth.keys WHERE name IN ('capability','attestation','paypal-quote','paypal-quote-request')").fetchall())
        if (keys.get("capability") != capability_key or keys.get("attestation") != seller_channel[0]
                or keys.get("paypal-quote") != quote_key or keys.get("paypal-quote-request") != broker_profile["PAYPAL_QUOTE_REQUEST_KEY"]):
            raise RuntimeError("CONTROLLED_SELLER_KEY_BINDING_MISMATCH")
    original = state.setdefault("m2009_historical_hashes", hashes)
    if original != hashes:
        raise RuntimeError("PRESERVED_ACTION_OR_APPROVAL_CHANGED")
    for action in history:
        review = client.get("actions/" + action)
        if review["fresh"] or any(a["effective_status"] != "EXPIRED" for a in snapshot["approvals"] if a["action_id"] == action):
            raise RuntimeError("HISTORICAL_ACTION_MUST_REMAIN_EXPIRED")
    us = dotenv_values(PRIVATE / "paypal-us.env")
    if any(us.get(k) != v for k, v in {"PAYPAL_MERCHANT_ID": MERCHANT, "PAYPAL_CURRENCY": "USD",
            "PAYPAL_SELLER_COUNTRY": "US", "PAYPAL_BUYER_COUNTRY": "US", "PAYPAL_APP_BINDING_CONFIRMED": "true"}.items()):
        raise RuntimeError("US_MERCHANT_PROFILE_REQUIRED")
    config = runtime_environment("api")
    configured_model = dotenv_values(PRIVATE / "model.env")
    if any(config.get(k) != configured_model.get(k) for k in ("MODEL_PROVIDER", "MODEL_API_BASE", "MODEL_NAME", "MODEL_API_KEY")):
        raise RuntimeError("MODEL_RUNTIME_PROFILE_MISMATCH")
    if (runtime_environment("broker")["PAYPAL_PROVIDER_MUTATIONS_ENABLED"] != "false"
            or runtime_environment("web")["PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED"] != "false"):
        raise RuntimeError("PROVIDER_MUTATIONS_MUST_REMAIN_LOCKED")
    if not state.get("m2009_provider_binding"):
        if state.get("m2009_provider_read_attempted"):
            raise RuntimeError("UNKNOWN_PROVIDER_READ_NO_AUTOMATIC_RETRY")
        state["m2009_provider_read_attempted"] = True
        persist(state)
        response = client.http.post("http://127.0.0.1:18102/v1/internal/provider-preflight",
                                    headers={"Authorization": "Bearer " + config["PAYPAL_INTERNAL_TOKEN"]})
        data = response.json()
        if response.status_code != 200 or data.get("verified") is not True:
            raise RuntimeError("CANONICAL_US_MERCHANT_BINDING_NOT_VERIFIED")
        state["m2009_provider_binding"] = data
        persist(state)
    proposal = state["purchase_commands"]["m2009_model"]["response"]
    if (proposal["result"]["extraction"]["proposal"] != EXPECTED
            or proposal["result"]["provenance"]["actual_model_invocation"] is not True):
        raise RuntimeError("REAL_MODEL_PREFLIGHT_REQUIRED")
    for route in ("/paypal/return?token=UNTRUSTED_NAVIGATION_ONLY", "/paypal/cancel", "/api/paypal/status"):
        response = client.http.get("http://127.0.0.1:3000" + route)
        if response.status_code != 200:
            raise RuntimeError("FRONTEND_RETURN_CANCEL_OR_STATUS_UNAVAILABLE")
    planned = protected_body(100, "PREFLIGHT_NOT_AN_OPERATION", MERCHANT)
    checkout = planned["payment_source"]["paypal"]["experience_context"]
    if (checkout["return_url"] != "http://127.0.0.1:3000/paypal/return"
            or checkout["cancel_url"] != "http://127.0.0.1:3000/paypal/cancel"):
        raise RuntimeError("BUYER_RETURN_CANCEL_CONFIGURATION_MISMATCH")
    persist(state)
    return {"classification": "ACTUAL_RUNTIME_PREFLIGHT_NO_NEW_PAYPAL_MUTATION", "recorded_at": datetime.now(UTC).isoformat(),
            "system_identifier": cluster["system_identifier"], "port": cluster["port"], "services": services,
            "acceptance_evidence": state["acceptance_evidence"], "tests_run": EXPECTED_TESTS,
            "historical_hashes": hashes, "grant_id": gid, "grant_expires_at": grant["version"]["expires_at"],
            "budget_B_C_R_F": [100, 0, 0, 100], "service_credential_expires_at": lease.isoformat(),
            "clock_skew_seconds_max": 3, "provider_binding_diagnostic": state["m2009_provider_binding"],
            "authenticated_economic_and_seller_key_bindings_verified": True, "checkout": checkout,
            "real_model_provenance": proposal["result"]["provenance"], "profile_sha256": fingerprints,
            "merchant_id": MERCHANT, "currency": "USD", "amount_minor": 100,
            "provider_mutations_enabled": False, "new_order_or_capture_sent": False}


def quote(client):
    state = client.state
    path = ROOT / state["m2009_readiness_evidence"]
    readiness = json.loads(path.read_text())
    if (digest(path) != state["m2009_readiness_sha256"]
            or datetime.now(UTC) - datetime.fromisoformat(readiness["recorded_at"]) > timedelta(minutes=5)):
        raise RuntimeError("CURRENT_VALIDATED_READINESS_REQUIRED")
    # Recheck authority, clocks, profiles, history and all locks immediately;
    # this reuses recorded model/binding reads and sends no further provider call.
    check(client)
    if "m2009_prior_state_snapshot" not in state:
        snapshot_path = PRIVATE / ("m2009-before-fresh-quote-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        write_private(snapshot_path, json.dumps(state, indent=2))
        state["m2009_prior_state_snapshot"] = snapshot_path.name
        persist(state)
    proposal = state["purchase_commands"]["m2009_model"]["response"]
    signed = client.post("m2009_quote", "quote-renewals", {"proposal_id": proposal["proposal_id"]})
    terms = signed["quote"]
    if (terms["amount"] != 100 or terms["currency"] != "USD" or terms["merchant_id"] != MERCHANT
            or terms["provider"] != "PAYPAL_SANDBOX" or len(terms["lines"]) != 1
            or terms["lines"][0]["sku"] != "workspace-seat" or terms["lines"][0]["beneficiary"] != "alice"):
        raise RuntimeError("EXACT_FRESH_QUOTE_REQUIRED")
    action = client.post("m2009_action", "commands/propose", {"grant_id": proposal["grant_id"], "quote_id": signed["quote_id"], "currency": "USD"}, 0)
    review = client.get("actions/" + action["action_id"])
    if not (review["fresh"] and review["current"] and review["decision"]["allowed"] and review["decision"]["approval_required"]):
        raise RuntimeError("FRESH_DETERMINISTIC_POLICY_REQUIRED")
    state.update(action_id=action["action_id"], active_quote_command="m2009_quote", human_approval_required=True)
    persist(state)
    return {"classification": "REAL_AI_SIGNED_QUOTE_POLICY_AWAITING_INDEPENDENT_APPROVAL",
            "action_review": review, "quote_expires_at": terms["expires_at"], "proposal_id": proposal["proposal_id"],
            "readiness_evidence": state["m2009_readiness_evidence"], "reservation_attempted": False,
            "paypal_order_or_capture_sent": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("model", "check", "quote"))
    args = parser.parse_args()
    client = None
    try:
        checked_demo()
        client = DemoClient(json.loads(STATE.read_text()))
        result = {"model": model, "check": check, "quote": quote}[args.command](client)
        path = export(args.command, result)
        if args.command == "check":
            client.state.update(m2009_readiness_evidence=path.relative_to(ROOT).as_posix(), m2009_readiness_sha256=digest(path))
            persist(client.state)
        print(json.dumps({"evidence": path.relative_to(ROOT).as_posix(), "result": result}, indent=2))
    except Exception as error:
        message = str(error)
        print(json.dumps({"error": message if re.fullmatch(r"[A-Z_]{3,100}", message) else "M2009_PREFLIGHT_REJECTED",
                          "exception_type": type(error).__name__}))
        raise SystemExit(1) from None
    finally:
        if client:
            client.close()
