"""Provision a clean legitimate USD demo; no fixture imports or provider mutation.

Owner credentials are setup-only. Human approval is deliberately absent from
this tool. Private profiles are created once, ignored, and never printed.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

from dotenv import dotenv_values
from interlock.paypal_flow import VERIFIED_US_MERCHANT
from interlock.security import password_hash

from tools.hackathon.disposable_binding import CLUSTER, HEAD
from tools.hackathon.disposable_postgres import (
    DATABASES,
    DATASET,
    PRIVATE,
    ROOT,
    connect,
    environment,
    verify,
)

TENANT = "interlock_m2_demo"
INTEGRATION = "interlock_m2_paypal_us"
PEOPLE = {"issuer": "m2_owner", "requester": "m2_requester", "approver": "m2_approver"}
PORTS = {"session": 18100, "api": 18101, "broker": 18102, "seller": 18103}
STATE = PRIVATE / "m2-demo-state.json"


def write_private(path: Path, values):
    relative = path.relative_to(ROOT).as_posix()
    if subprocess.run(["git", "check-ignore", "--quiet", relative], cwd=ROOT).returncode:
        raise RuntimeError("PRIVATE_PROFILE_MUST_BE_GIT_IGNORED")
    content = values if isinstance(values, str) else "".join(f"{k}={v}\n" for k, v in values.items())
    with path.open("x", encoding="utf-8") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def checked_demo():
    if CLUSTER != "postgres18" or DATASET != "m2_demo":
        raise RuntimeError("EXPLICIT_CLEAN_M2_DEMO_DATASET_REQUIRED")
    return verify()


def provision():
    cluster = checked_demo()
    if STATE.exists() or any(PRIVATE.glob("m2-runtime-*.env")):
        raise RuntimeError("DEMO_ALREADY_PROVISIONED_NO_OVERWRITE")
    # Runtime broker alone opens the US credential profile and performs the
    # authenticated binding read. Setup uses the already verified merchant ID.
    for domain in ("control", "seller", "reference"):
        with connect(DATABASES[domain]) as db:
            if db.execute("SELECT version_num FROM alembic_version").fetchone()[0] != HEAD:
                raise RuntimeError("CURRENT_ADDITIVE_SCHEMA_REQUIRED")
            if domain == "control" and db.execute("SELECT count(*) FROM public.tenants").fetchone()[0]:
                raise RuntimeError("CLEAN_DEMO_DATABASE_REQUIRED")
            if domain == "seller" and db.execute("SELECT count(*) FROM seller_truth.bindings").fetchone()[0]:
                raise RuntimeError("CLEAN_SELLER_DATABASE_REQUIRED")
    key = {name: secrets.token_urlsafe(40) for name in
           ("session", "quote", "quote_request", "canonical", "capability", "attestation", "mutate", "observe", "internal", "seat")}
    passwords = {role: secrets.token_urlsafe(24) for role in PEOPLE}
    state = {"classification": "LEGITIMATE_DEMO_SETUP_NOT_PAYMENT_EVIDENCE", "tenant_id": TENANT,
             "people": PEOPLE, "merchant_id": VERIFIED_US_MERCHANT, "currency": "USD", "amount_minor": 100,
             "integration": INTEGRATION, "database_names": DATABASES, "ports": PORTS,
             "system_identifier": cluster["system_identifier"], "provider_mutations_authorized": False,
             "grant_commands": {}, "purchase_commands": {}}
    write_private(STATE, json.dumps(state, indent=2) + "\n")
    write_private(PRIVATE / "m2-provisioning.env", {"SETUP_" + name.upper(): value for name, value in key.items()})
    write_private(PRIVATE / "m2-operator.env", {"DEMO_ISSUER_USERNAME": PEOPLE["issuer"],
        "DEMO_ISSUER_PASSWORD": passwords["issuer"], "DEMO_REQUESTER_USERNAME": PEOPLE["requester"],
        "DEMO_REQUESTER_PASSWORD": passwords["requester"]})
    write_private(PRIVATE / "m2-approver.env", {"DEMO_APPROVER_USERNAME": PEOPLE["approver"],
        "DEMO_APPROVER_PASSWORD": passwords["approver"]})
    write_private(PRIVATE / "m2-seller-user.env", {"DEMO_SEAT_USERNAME": "m2_alice", "DEMO_SEAT_TOKEN": key["seat"]})
    env = environment()
    common = {"PAYPAL_DEMO_TENANT": TENANT, "PAYPAL_DISPOSABLE_CLUSTER": CLUSTER,
              "PAYPAL_DISPOSABLE_DATASET": DATASET, "PAYPAL_EXPECTED_SYSTEM_IDENTIFIER": cluster["system_identifier"]}
    model = {k: v for k, v in dotenv_values(PRIVATE / "model.env").items() if v is not None and k.startswith("MODEL_")}
    if not model.get("MODEL_API_KEY") or model.get("MODEL_PROVIDER") != "openai-compatible":
        raise RuntimeError("CONFIGURED_REAL_MODEL_PROFILE_REQUIRED")
    profiles = {
        "session": {"CONTROL_DATABASE_URL": env["PAYPAL_TEST_API_URL"], "SESSION_KEY": key["session"]},
        "api": {"PAYPAL_API_DATABASE_URL": env["PAYPAL_TEST_API_URL"], **model,
                "PAYPAL_BROKER_URL": "http://127.0.0.1:18102", "PAYPAL_SELLER_URL": "http://127.0.0.1:18103",
                "PAYPAL_INTERNAL_TOKEN": key["internal"]},
        "broker": {"PAYPAL_BROKER_DATABASE_URL": env["PAYPAL_TEST_BROKER_URL"],
                   "PAYPAL_INTERNAL_TOKEN": key["internal"], "DISPATCH_MUTATE_TOKEN": key["mutate"],
                   "DISPATCH_OBSERVE_TOKEN": key["observe"], "PAYPAL_CANONICAL_SIGNING_KEY": key["canonical"],
                   "PAYPAL_QUOTE_REQUEST_KEY": key["quote_request"], "PAYPAL_SELLER_URL": "http://127.0.0.1:18103",
                   "PAYPAL_PROVIDER_MUTATIONS_ENABLED": "false"},
        "seller": {"PAYPAL_SELLER_DATABASE_URL": env["PAYPAL_TEST_SELLER_URL"]},
        "economic": {"PAYPAL_ECONOMIC_DATABASE_URL": env["PAYPAL_TEST_ECONOMIC_URL"],
                     "PAYPAL_SELLER_URL": "http://127.0.0.1:18103"},
        "web": {"CONTROL_API_URL": "http://127.0.0.1:18100", "PAYPAL_API_URL": "http://127.0.0.1:18101",
                "WEB_ORIGIN": "http://127.0.0.1:3000", "PAYPAL_WEB_PAYMENT_ACTIONS_ENABLED": "false"},
    }
    for role, values in profiles.items():
        write_private(PRIVATE / ("m2-runtime-" + role + ".env"), {**common, **values})
    with connect(DATABASES["control"]) as db:
        db.execute("INSERT INTO public.tenants VALUES(%s,'interlock-demo-buyer','interlock-us-sandbox','workspace-seller-v1',%s,%s)",
                   (TENANT, INTEGRATION, VERIFIED_US_MERCHANT))
        for role, person in PEOPLE.items():
            db.execute("INSERT INTO public.identities VALUES(%s,%s,'alice',%s,%s)",
                       (person, TENANT, password_hash(passwords[role], person), role))
            db.execute("INSERT INTO authority.memberships(principal,tenant_id,roles) VALUES(%s,%s,%s)",
                       (person, TENANT, ["PURCHASE_" + role.upper()]))
            if role in {"issuer", "requester"}:
                db.execute("INSERT INTO dispatch.observation_memberships(tenant_id,principal) VALUES(%s,%s)", (TENANT, person))
        db.execute("INSERT INTO authority.tenant_control(tenant_id,kind) VALUES(%s,'PURCHASE')", (TENANT,))
        db.execute("INSERT INTO paypal_v1.tenants(tenant_id,merchant_id,currency,seller_country,buyer_country,profile_id) VALUES(%s,%s,'USD','US','US','us')",
                   (TENANT, VERIFIED_US_MERCHANT))
        # This remains false until the human separately authorizes order creation.
        db.execute("INSERT INTO dispatch.integrations VALUES(%s,%s,'PAYPAL_SANDBOX',%s,'paypal-orders-v2-v1',false)",
                   (TENANT, INTEGRATION, VERIFIED_US_MERCHANT))
        for name, secret in (("session", key["session"]), ("seller", key["quote"]), ("paypal-seller", key["quote"])):
            db.execute("INSERT INTO authority.issuer_keys VALUES(%s,%s)", (name, secret))
        for verb, name in (("DISPATCH", "mutate"), ("OBSERVE", "observe")):
            identity = "m2_" + name
            db.execute("INSERT INTO dispatch.service_credentials(id,token_hash,expires_at) VALUES(%s,%s,clock_timestamp()+interval '4 hours')",
                       (identity, hashlib.sha256(key[name].encode()).hexdigest()))
            db.execute("INSERT INTO dispatch.service_scopes VALUES(%s,%s,%s)", (identity, TENANT, [verb]))
        for provider, channel, source, secret in (("PAYPAL_SANDBOX", "paypal-" + TENANT, "CANONICAL_READ", key["canonical"]),
                    ("CONTROLLED_SELLER", "seller-" + TENANT, "SELLER_ATTESTATION", key["attestation"])):
            db.execute("INSERT INTO economic.channels(id,provider,account_id,integration,tenant_id,source,secret) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                       (channel, provider, VERIFIED_US_MERCHANT, INTEGRATION, TENANT, source, secret))
        db.execute("INSERT INTO economic.channels VALUES('seller-capability-key','CONTROLLED_SELLER','CAPABILITY_ONLY','INTERNAL',NULL,'SELLER_ATTESTATION',%s,true)",
                   (key["capability"],))
    with connect(DATABASES["seller"]) as db:
        db.execute("INSERT INTO seller_truth.bindings VALUES(%s,%s,%s,'interlock-demo-workspace','workspace-seller-v1')",
                   (TENANT, VERIFIED_US_MERCHANT, INTEGRATION))
        db.execute("INSERT INTO seller_truth.paypal_tenants VALUES(%s,%s,'USD',100)", (TENANT, VERIFIED_US_MERCHANT))
        db.execute("INSERT INTO seller_truth.identities VALUES('m2_alice',%s,'alice',%s)",
                   (TENANT, hashlib.sha256(key["seat"].encode()).hexdigest()))
        for name, secret in (("capability", key["capability"]), ("attestation", key["attestation"]),
                             ("paypal-quote", key["quote"]), ("paypal-quote-request", key["quote_request"])):
            db.execute("INSERT INTO seller_truth.keys VALUES(%s,%s)", (name, secret))
    print(json.dumps({"provisioned_tenant": TENANT, "merchant_id": VERIFIED_US_MERCHANT, "currency": "USD",
                      "authorized_product": "workspace-seat", "beneficiary": "alice", "price_minor": 100,
                      "deployment_gate_enabled": False, "provider_mutations_enabled": False}))


def activate_authority(evidence_path):
    from tools.hackathon.m2_acceptance import EXPECTED_TESTS, SUITES, source_hashes

    checked_demo()
    path = (ROOT / evidence_path).resolve()
    if not path.is_relative_to(ROOT / "docs/hackathon/evidence"):
        raise RuntimeError("SANITIZED_ACCEPTANCE_EVIDENCE_REQUIRED")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if (evidence.get("tests_run") != EXPECTED_TESTS or evidence.get("suites") != list(SUITES)
            or evidence.get("failures") or evidence.get("errors") or evidence.get("skipped")
            or evidence.get("cluster_system_identifier") != "7706108909131505256"
            or evidence.get("dataset") != "m2_acceptance"
            or evidence.get("opt_in_head") != HEAD or evidence.get("genuine_paypal_operations") != 0):
        raise RuntimeError("CURRENT_PASSING_PROTECTED_ACCEPTANCE_REQUIRED")
    if evidence.get("source_sha256") != source_hashes():
        raise RuntimeError("COMPLETE_CURRENT_ACCEPTANCE_SOURCE_BINDING_REQUIRED")
    for name, digest in evidence.get("source_sha256", {}).items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("ACCEPTANCE_SOURCE_DRIFT")
    with connect(DATABASES["control"]) as db:
        if db.execute("SELECT count(*) FROM dispatch.integrations WHERE real_mutations_enabled").fetchone()[0]:
            raise RuntimeError("PROVIDER_GATE_MUST_REMAIN_LOCKED")
        db.execute("UPDATE paypal_v1.deployment SET enabled=true,disposable_acceptance_sha256=%s",
                   (hashlib.sha256(path.read_bytes()).hexdigest(),))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    state["acceptance_evidence"] = path.relative_to(ROOT).as_posix()
    state["acceptance_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    temporary = PRIVATE / ("m2-acceptance-" + secrets.token_hex(12) + ".pending")
    write_private(temporary, json.dumps(state, indent=2))
    os.replace(temporary, STATE)
    print(json.dumps({"authority_gate_enabled": True, "provider_mutations_enabled": False, "acceptance": path.name}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("provision", "activate-authority"))
    parser.add_argument("--evidence")
    args = parser.parse_args()
    try:
        provision() if args.command == "provision" else activate_authority(args.evidence or "")
    except Exception as error:
        # Never echo a failed SQL row, URL, credential or model profile.
        code = str(error)
        print(json.dumps({"error": code if re.fullmatch(r"[A-Z_]{3,100}", code) else "M2_SETUP_REJECTED",
                          "exception_type": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None),
                          "constraint": getattr(getattr(error, "diag", None), "constraint_name", None)}))
        raise SystemExit(1) from None
