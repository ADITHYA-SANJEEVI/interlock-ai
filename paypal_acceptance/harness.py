"""Synthetic PSP/model fixtures around ACTUAL protected PostgreSQL functions.

No migrations, database creation, TRUNCATE, provider HTTP or private profiles.
Every run requires explicit URLs plus a human-verified cluster system identifier.
Do not execute while Docker/PostgreSQL is unavailable.
"""

import base64
import copy
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from interlock.contracts import canonical, signature
from interlock.paypal_flow import VERIFIED_US_MERCHANT, BrokerIdentity, ProtectedPayPal
from interlock.paypal_kernel import PostgreSQLKernel
from interlock.paypal_sandbox import PayPalFailure
from sqlalchemy import create_engine, text

from tools.hackathon.disposable_binding import (
    HEAD,
    SYSTEM,
    checked_metadata,
    checked_url,
    require_explicit_selection,
)

FIXTURE_SESSION = "synthetic-acceptance-session-key-not-a-real-secret"
FIXTURE_QUOTE = "synthetic-acceptance-quote-key-not-a-real-secret"
FIXTURE_CANONICAL = "synthetic-acceptance-canonical-key-not-a-real-secret"
FIXTURE_CAPABILITY = "synthetic-acceptance-capability-key-not-a-real-secret"
FIXTURE_ATTESTATION = "synthetic-acceptance-attestation-key-not-a-real-secret"
FIXTURE_QUOTE_REQUEST = "synthetic-acceptance-quote-request-not-a-real-secret"


def deadline():
    return (datetime.now(UTC) + timedelta(minutes=45)).isoformat()


def verified_configuration():
    require_explicit_selection()
    if os.environ.get("PAYPAL_DISPOSABLE_ACCEPTANCE_ACK") != "SYNTHETIC_FIXTURES_ON_VERIFIED_DISPOSABLE_POSTGRES_ONLY":
        raise RuntimeError("Disposable acceptance is not authorized/configured")
    system = os.environ.get("PAYPAL_TEST_SYSTEM_IDENTIFIER", "")
    if system != SYSTEM:
        raise RuntimeError("Human-verified disposable cluster identifier required")
    expected = {"control_owner": "control", "api": "control", "broker": "control", "worker": "control", "economic": "control",
                "seller_owner": "seller", "seller": "seller"}
    roles = {"api": "interlock_api", "broker": "interlock_broker", "worker": "interlock_worker", "economic": "interlock_economic", "seller": "interlock_seller"}
    urls = {}
    for service, domain in expected.items():
        value = os.environ.get("PAYPAL_TEST_" + service.upper() + "_URL", "")
        p = urlsplit(value)
        db = p.path.removeprefix("/")
        if (p.scheme != "postgresql+psycopg" or p.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not db.startswith("hackathon_disposable_") or not db.endswith("_" + domain)
                or p.query or p.fragment or not p.username or (service in roles and p.username != roles[service])):
            raise RuntimeError("All disposable role URLs must be explicit and isolated")
        urls[service] = value
        checked_url(value, domain, roles.get(service, "interlock_disposable_admin"))
    # Runtime roles must connect to precisely the same endpoint/database as owner.
    for service, domain in expected.items():
        p, owner = urlsplit(urls[service]), urlsplit(urls[domain + "_owner"])
        if (p.hostname, p.port, p.path) != (owner.hostname, owner.port, owner.path):
            raise RuntimeError("Disposable role endpoint mismatch")
    return urls, system


class Harness:
    def __init__(self):
        self.urls, expected_system = verified_configuration()
        self.engines = {name: create_engine(url, hide_parameters=True) for name, url in self.urls.items()}
        for domain in ("control", "seller"):
            with self.engines[domain + "_owner"].connect() as db:
                actual = db.execute(text("""SELECT system_identifier::text,current_setting('data_directory'),
                    current_setting('server_version'),current_setting('port'),current_setting('listen_addresses')
                    FROM pg_control_system()""")).one()
                checked_metadata(*actual)
                if actual[0] != expected_system:
                    raise RuntimeError("Disposable PostgreSQL cluster does not match human verification")
                database = db.scalar(text("SELECT current_database()"))
                if database != urlsplit(self.urls[domain + "_owner"]).path.removeprefix("/"):
                    raise RuntimeError("Disposable database binding mismatch")
                expected_head = os.environ.get("PAYPAL_TEST_SCHEMA_HEAD", HEAD)
                if expected_head not in {HEAD, "0031_continuation_cutoff"}:
                    raise RuntimeError("Unknown acceptance schema")
                if expected_head != HEAD and database != "hackathon_disposable_013_continuation_" + domain:
                    raise RuntimeError("Continuation tests require the exact isolated acceptance databases")
                if db.scalar(text("SELECT version_num FROM alembic_version")) != expected_head:
                    raise RuntimeError("Opt-in schema must be independently installed before acceptance")
        suffix = uuid4().hex
        self.tenant = "usd_test_" + suffix
        self.integration = "usd_integration_" + suffix
        self.people = {role: role + "_" + suffix for role in ("issuer", "requester", "approver")}
        self.mutate, self.observe = "fixture_mutate_" + suffix, "fixture_observe_" + suffix
        self.seed()
        self.api = PostgreSQLKernel(self.urls["api"], "interlock_api")
        self.broker = PostgreSQLKernel(self.urls["broker"], "interlock_broker")
        self.economic = PostgreSQLKernel(self.urls["economic"], "interlock_economic")
        self.provider = SyntheticProvider()
        self.driver = ProtectedPayPal(self.broker, self.provider,
                                      BrokerIdentity(self.mutate, self.observe, FIXTURE_CANONICAL, VERIFIED_US_MERCHANT))

    def close(self):
        for engine in self.engines.values():
            engine.dispose()
        from interlock.db import engine as runtime_engine
        for value in self.urls.values():
            runtime_engine(value).dispose()

    def sql(self, statement, params=None, service="control_owner"):
        with self.engines[service].begin() as db:
            return db.scalar(text(statement), params or {})

    def seed(self):
        with self.engines["control_owner"].begin() as db:
            db.execute(text("""INSERT INTO public.tenants VALUES(:tenant,'synthetic-buyer','synthetic-merchant',
                'workspace-seller-v1',:integration,:merchant)"""),
                       {"tenant": self.tenant, "integration": self.integration, "merchant": VERIFIED_US_MERCHANT})
            for role, user in self.people.items():
                db.execute(text("""INSERT INTO public.identities(username,tenant_id,beneficiary,password_hash,role)
                    VALUES(:user,:tenant,'alice','SYNTHETIC_UNUSED_PASSWORD_HASH',:role)"""),
                           {"user": user, "tenant": self.tenant, "role": role})
                db.execute(text("INSERT INTO authority.memberships(principal,tenant_id,roles) VALUES(:user,:tenant,:roles)"),
                           {"user": user, "tenant": self.tenant, "roles": ["PURCHASE_" + role.upper()]})
                if role in {"issuer", "requester"}:
                    db.execute(text("INSERT INTO dispatch.observation_memberships(tenant_id,principal) VALUES(:tenant,:user)"),
                               {"tenant": self.tenant, "user": user})
            db.execute(text("INSERT INTO authority.tenant_control(tenant_id,kind) VALUES(:tenant,'PURCHASE')"), {"tenant": self.tenant})
            db.execute(text("""INSERT INTO paypal_v1.tenants(tenant_id,merchant_id,currency,seller_country,buyer_country,profile_id)
                VALUES(:tenant,:merchant,'USD','US','US','us')"""), {"tenant": self.tenant, "merchant": VERIFIED_US_MERCHANT})
            db.execute(text("""INSERT INTO dispatch.integrations VALUES(:tenant,:integration,'PAYPAL_SANDBOX',
                :merchant,'paypal-orders-v2-v1',true)"""),
                       {"tenant": self.tenant, "integration": self.integration, "merchant": VERIFIED_US_MERCHANT})
            for name, value in (("session", FIXTURE_SESSION), ("paypal-seller", FIXTURE_QUOTE), ("seller", FIXTURE_QUOTE)):
                db.execute(text("INSERT INTO authority.issuer_keys VALUES(:name,:value) ON CONFLICT(name) DO UPDATE SET secret=EXCLUDED.secret"),
                           {"name": name, "value": value})
            for verb, secret in (("DISPATCH", self.mutate), ("OBSERVE", self.observe)):
                cid = verb + "_" + self.tenant
                db.execute(text("""INSERT INTO dispatch.service_credentials(id,token_hash,expires_at)
                    VALUES(:id,:hash,clock_timestamp()+interval '1 hour')"""),
                           {"id": cid, "hash": hashlib.sha256(secret.encode()).hexdigest()})
                db.execute(text("INSERT INTO dispatch.service_scopes VALUES(:id,:tenant,:verbs)"),
                           {"id": cid, "tenant": self.tenant, "verbs": [verb]})
            for provider, channel, source, secret in (
                ("PAYPAL_SANDBOX", "paypal-" + self.tenant, "CANONICAL_READ", FIXTURE_CANONICAL),
                ("CONTROLLED_SELLER", "seller-" + self.tenant, "SELLER_ATTESTATION", FIXTURE_ATTESTATION),
            ):
                db.execute(text("""INSERT INTO economic.channels(id,provider,account_id,integration,tenant_id,source,secret)
                    VALUES(:id,:provider,:merchant,:integration,:tenant,:source,:secret)"""),
                           {"id": channel, "provider": provider, "merchant": VERIFIED_US_MERCHANT,
                            "integration": self.integration, "tenant": self.tenant, "source": source, "secret": secret})
            db.execute(text("""INSERT INTO economic.channels VALUES('seller-capability-key','CONTROLLED_SELLER',
                'CAPABILITY_ONLY','INTERNAL',NULL,'SELLER_ATTESTATION',:secret,true)
                ON CONFLICT(id) DO UPDATE SET secret=EXCLUDED.secret"""), {"secret": FIXTURE_CAPABILITY})
            # TEST ONLY: an explicit disposable fixture gate, NOT an acceptance certificate.
            db.execute(text("UPDATE paypal_v1.deployment SET enabled=true,disposable_acceptance_sha256=:fixture"),
                       {"fixture": hashlib.sha256(b'SYNTHETIC_ACCEPTANCE_FIXTURE_NOT_CERTIFICATION').hexdigest()})
        with self.engines["seller_owner"].begin() as db:
            db.execute(text("""INSERT INTO seller_truth.bindings VALUES(:tenant,:merchant,:integration,:workspace,'workspace-seller-v1')"""),
                       {"tenant": self.tenant, "merchant": VERIFIED_US_MERCHANT, "integration": self.integration,
                        "workspace": "workspace_" + self.tenant})
            db.execute(text("INSERT INTO seller_truth.paypal_tenants VALUES(:tenant,:merchant,'USD',100)"),
                       {"tenant": self.tenant, "merchant": VERIFIED_US_MERCHANT})
            db.execute(text("""INSERT INTO seller_truth.identities VALUES(:name,:tenant,'alice',:hash)"""),
                       {"name": "seat_" + self.tenant, "tenant": self.tenant,
                        "hash": hashlib.sha256(("synthetic-seat-token-" + self.tenant).encode()).hexdigest()})
            for name, secret in (("capability", FIXTURE_CAPABILITY), ("attestation", FIXTURE_ATTESTATION),
                                 ("paypal-quote", FIXTURE_QUOTE), ("paypal-quote-request", FIXTURE_QUOTE_REQUEST)):
                db.execute(text("INSERT INTO seller_truth.keys VALUES(:name,:secret) ON CONFLICT(name) DO UPDATE SET secret=EXCLUDED.secret"),
                           {"name": name, "secret": secret})

    def token(self, role):
        payload = base64.urlsafe_b64encode(canonical({"user": self.people[role], "exp": int(time.time()) + 3600})).decode()
        return payload + "." + signature(FIXTURE_SESSION, payload.encode())

    def command(self, family, body, expected=0, role="requester", idem=None):
        return self.api("command", {"token": self.token(role), "family": family, "body": json.dumps(body),
                                    "expected": expected, "idem": idem or "fixture_" + uuid4().hex})

    def prepare(self, bound=500, beneficiary="alice", approve=True, admit=True):
        self.command("publish-policy", {"kind": "PURCHASE", "approval_above": 0, "max_amount": 1000}, 1, "issuer")
        self.grant = self.command("create-grant", {"kind": "PURCHASE", "subject": self.people["requester"],
                                                  "lifetime_bound": bound, "currency": "USD", "beneficiaries": ["alice"],
                                                  "expires_at": deadline(), "resource_id": None}, role="issuer")
        self.command("activate-grant", {"grant_id": self.grant["grant_id"]}, 1, "issuer")
        proposal = {"financial_authority": False, "provenance": {"actual_model_invocation": False,
                    "classification": "SYNTHETIC_DATABASE_ACCEPTANCE_FIXTURE"}, "extraction": {"status": "PROPOSED",
                    "reason": "SUPPORTED_PURCHASE", "proposal": {"product_id": "workspace-seat", "beneficiary": beneficiary,
                    "quantity": 1, "spending_limit_minor": 500, "currency": "USD"}}}
        p = self.api("record_proposal", {"token": self.token("requester"), "grantid": self.grant["grant_id"],
                                         "idem": "proposal_" + uuid4().hex, "intent_hash": "a" * 64, "result": json.dumps(proposal)})
        request = canonical({"schema": "paypal-quote-request-v1", "tenant_id": self.tenant,
                             "request_id": "fixture_" + uuid4().hex, "expires_at": deadline(),
                             "product_id": "workspace-seat", "beneficiary": beneficiary, "quantity": 1, "currency": "USD"})
        sealed = self.sql("SELECT seller_truth.paypal_quote_v1(:raw,:sig)",
                          {"raw": request.decode(), "sig": signature(FIXTURE_QUOTE_REQUEST, request)}, "seller")
        q = self.api("quote", {"token": self.token("requester"), **sealed, "idem": "quote_" + uuid4().hex})
        self.api("link_quote", {"token": self.token("requester"), "pid": p["proposal_id"], "quoteid": q["id"]})
        self.action = self.command("propose", {"grant_id": self.grant["grant_id"], "quote_id": q["id"]})
        self.exact = {k: self.action[k] for k in ("action_id", "evidence_id", "decision_id")}
        if approve:
            a = self.command("approve", {**self.exact, "expires_at": deadline()}, 1, "approver")
            self.exact["approval_id"] = a["approval_id"]
        if admit:
            self.admission = self.command("admit", self.exact, 1)
            self.op = self.admission["operation_id"]
        return self

    def ready_capture(self):
        self.driver.create(self.op)
        data = self.api("inspect", {"token": self.token("issuer"), "opid": self.op})
        self.api("consent", {"token": self.token("issuer"), "opid": self.op,
                             "expected": data["dispatch_revision"], "idem": "consent_" + uuid4().hex})
        self.provider.status = "APPROVED"  # Explicit synthetic buyer consent, no browser automation.
        self.driver.observe(self.op)
        self.request_capture()
        return self

    def request_capture(self):
        data = self.api("inspect", {"token": self.token("issuer"), "opid": self.op})
        return self.api("capture_request", {"token": self.token("issuer"), "opid": self.op,
                                             "expected": data["revision"], "idem": "capture_" + uuid4().hex})

    def vector(self):
        return self.sql("SELECT jsonb_build_array(bound,consumed,reserved,free) FROM authority.grant_heads WHERE grant_id=:id",
                        {"id": self.grant["grant_id"]})


class SyntheticProvider:
    def __init__(self):
        self.status = "PAYER_ACTION_REQUIRED"
        self.body = None
        self.order = uuid4().hex[:17].upper()
        self.capture = uuid4().hex[:17].upper()
        self.creates = self.captures = 0
        self.captured = False
        self.lose_create = self.lose_capture = self.unavailable = False
        self.override = None

    def create_order(self, body, request_id):
        self.creates += 1
        self.body = copy.deepcopy(body)
        if self.lose_create:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        return {"id": self.order}

    def get_order(self, order_id):
        if self.unavailable:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        assert self.body is not None, "Synthetic order must be created before a canonical read"
        unit = copy.deepcopy(self.body["purchase_units"][0])
        if self.captured:
            unit["payments"] = {"captures": [{"id": self.capture, "status": "COMPLETED", "final_capture": True,
                                              "amount": copy.deepcopy(unit["amount"])}]}
        result = {"id": self.order, "status": self.status, "purchase_units": [unit]}
        if self.override:
            self.override(result)
        return result

    def capture_order(self, order_id, request_id):
        self.captures += 1
        self.captured, self.status = True, "COMPLETED"
        if self.lose_capture:
            raise PayPalFailure({"name": "TRANSPORT_OUTCOME_UNKNOWN"})
        return {"id": self.order}
