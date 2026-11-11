"""Pass 3 attacks through actual runtime login roles and independent evidence checks."""

import copy
import json
from uuid import uuid4

import pytest
from interlock import economic
from interlock.config import settings
from interlock.contracts import canonical, signature
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_dispatch import d as d

from tools.pass3.bootstrap import seed
from tools.pass3.checker import verify
from tools.pass3.export import export, write

KEYS = {
    name: "automated-pass3-secret-" + name
    for name in ("reference", "capability", "attestation", "canonical", "razorpay")
}
RECORDS = []


@pytest.fixture(scope="session", autouse=True)
def save_pass3():
    yield
    if RECORDS:
        write("tmp/pass3-tests.json", RECORDS)


class Truth:
    def __init__(self, dispatch):
        self.d = dispatch
        self.urls = {x: getattr(settings(), x + "_db") for x in ("control", "seller", "reference")}
        self.runtime = {
            name: self.urls[domain].replace("interlock:local_only@", "interlock_" + role + ":local_only@")
            for name, domain, role in [
                ("api", "control", "api"),
                ("economic", "control", "economic"),
                ("seller", "seller", "seller"),
                ("reference", "reference", "reference"),
            ]
        }

    def sql(self, sql, params=None, role="economic"):
        with create_engine(self.runtime[role], hide_parameters=True).begin() as db:
            return db.scalar(text(sql), params or {})

    def count(self, table):
        return self.d.sql("SELECT count(*) FROM economic." + table)[0][0]

    def operation(self, send=True, publish_policy=True):
        if publish_policy:
            self.d.auth.policy()
        grant = self.d.auth.grant(bound=3000)
        q = self.d.auth.clients["seller_transport"](
            "tenant-a",
            "quote-" + uuid4().hex,
            "quote",
            {"seller": "workspace-seller-v1", "lines": [{"sku": "workspace-seat", "beneficiary": "alice"}]},
        )
        raw = canonical(q).decode()
        with self.d.auth.runtime.begin() as db:
            db.execute(
                text("SELECT authority.register_quote(:token,:raw,:sig)"),
                {
                    "token": issue_session("requester-a"),
                    "raw": raw,
                    "sig": signature(settings().seller_key, raw.encode()),
                },
            )
        action = self.d.auth.ok(
            "propose", {"grant_id": grant["grant_id"], "quote_id": q["id"]}, principal="requester-a"
        )
        assert self.d.auth.admit(action).status_code == 200
        op = dict(
            self.d.sql(
                "SELECT * FROM dispatch.business_operations WHERE action_id=:id", {"id": action["action_id"]}
            )[0]._mapping
        )
        if send:
            response = self.d.send(op)
            assert response.status_code == 200, response.text
        self.op = op
        self.order = self.d.state(op)["provider_order"]
        self.quote = q
        return op

    def provider(self):
        return self.sql("SELECT ref_economic.capture(:order)", {"order": self.order}, "reference")

    def receipt(self, envelope=None, raw=None, sig=None, delivery=None, channel="reference-tenant-a"):
        env = envelope or self.provider()
        raw = raw if raw is not None else env["raw"].encode()
        return economic.receive(
            channel,
            raw,
            sig if sig is not None else env["signature"],
            delivery or env["delivery_id"],
            self.runtime["api"],
        )

    def step(self, name, **params):
        return economic.kernel(name, params, self.runtime["economic"])

    def process(self, receipt):
        return economic.process(receipt, self.runtime["economic"])

    def finalize(self):
        capability = self.step("capability", operation=self.op["id"])
        att = self.sql(
            "SELECT seller_truth.finalize(:raw,:sig)",
            {"raw": capability["raw"], "sig": capability["signature"]},
            "seller",
        )
        rid = self.receipt(att, delivery="seller-" + self.op["id"], channel="seller-tenant-a")
        self.process(rid)
        return capability, att

    def export(self):
        return export(self.urls)


@pytest.fixture
def truth(d):
    h = Truth(d)
    for domain, schema in (
        ("control", "economic"),
        ("seller", "seller_truth"),
        ("reference", "ref_economic"),
    ):
        with create_engine(h.urls[domain]).begin() as db:
            tables = db.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname=:s"), {"s": schema}
            ).all()
            db.execute(text("TRUNCATE " + ",".join(schema + "." + x for x in tables) + " CASCADE"))
    # Reinsert the fixed vocabulary after fixture reset; runtime cannot modify it.
    with create_engine(h.urls["control"]).begin() as db:
        db.execute(
            text(
                "INSERT INTO economic.accounts VALUES ('ProviderClearing'),('CustomerOrderLiability'),('SalesRevenue'),('SalesReturns'),('RefundPayable'),('UnmatchedProviderSuspense')"
            )
        )
    seed(h.urls, KEYS, "acc_TEST")
    h.operation()
    yield h


def record(case, truth, **extra):
    result = verify(truth.export())
    RECORDS.append({"gate": case, "classification": "VERIFIED_POSTGRES", "verified": result, **extra})


def test_capture_fact_posting_and_fulfillment_remain_distinct(truth):
    rid = truth.receipt()
    assert truth.count("facts") == 0
    assert truth.step("authenticate", receipt=str(rid)) == "AUTHENTICATED"
    oid = truth.step("observe", receipt=str(rid))
    assert truth.count("observations") == 1 and truth.count("facts") == 0
    facts = truth.step("derive", observation=str(oid))
    assert len(facts) == 2 and truth.count("postings") == 0
    assert len(verify(truth.export())["facts_without_postings"]) == 2
    for fid in facts:
        truth.step("post", fact=fid)
    assert truth.count("postings") == 2 and truth.count("facts") == 2
    capability, att = truth.finalize()
    assert truth.count("facts") == 4 and truth.count("postings") == 3
    assert (
        truth.sql(
            "SELECT seller_truth.finalize(:raw,:sig)",
            {"raw": capability["raw"], "sig": capability["signature"]},
            "seller",
        )
        == att
    )
    seatid = truth.quote["lines"][0]["allocation_id"]
    access = truth.sql(
        "SELECT seller_truth.workspace(:token,:seat,:note)",
        {"token": "seat-token-a", "seat": seatid, "note": "Persisted protected work"},
        "seller",
    )
    assert access["beneficiary"] == "alice" and access["entries"][0]["text"] == "Persisted protected work"
    with pytest.raises(DBAPIError):
        truth.sql(
            "SELECT seller_truth.workspace(:token,:seat,NULL)",
            {"token": "seat-token-b", "seat": seatid},
            "seller",
        )
    # Pass 2 capacity semantics remain frozen: observation and bookkeeping never refill F.
    assert truth.d.sql("SELECT consumed,reserved,free FROM authority.grant_heads")[0] == (0, 3000, 0)
    record("orthogonal_truth_and_exact_entitlement", truth)
    write("tmp/pass3-audit-export.json", truth.export())


def test_duplicate_delivery_derivation_and_posting(truth):
    env = truth.provider()
    first = truth.process(truth.receipt(env))
    for delivery in (env["delivery_id"], "reordered-other-delivery"):
        assert truth.process(truth.receipt(env, delivery=delivery)) == first
    for fid in first:
        p1 = truth.step("post", fact=fid)
        assert truth.step("post", fact=fid) == p1
    assert truth.count("receipts") == 3 and truth.count("observations") == 1
    assert truth.count("facts") == 2 and truth.count("postings") == 2
    record("duplicate_delivery_fact_and_post", truth)


@pytest.mark.parametrize(
    "attack",
    [
        "bad_signature",
        "malformed",
        "account",
        "integration",
        "tenant",
        "amount",
        "currency",
        "provider_object",
        "local_purchase",
    ],
)
def test_unauthenticated_malformed_and_substituted_evidence(truth, attack):
    env = truth.provider()
    v = json.loads(env["raw"])
    p = v["payload"]["payment"]["entity"]
    if attack in {"account", "integration", "tenant"}:
        v[{"account": "account_id", "integration": "integration", "tenant": "tenant_id"}[attack]] = (
            "wrong-scope"
        )
    elif attack == "amount":
        p["amount"] += 1
    elif attack == "currency":
        p["currency"] = "USD"
    elif attack == "provider_object":
        v["operation_id"] = "op_other"
    elif attack == "local_purchase":
        v["operation_id"] = "op_foreign"
    raw = b"{malformed" if attack == "malformed" else canonical(v)
    sig = "0" * 64 if attack == "bad_signature" else signature(KEYS["reference"], raw)
    rid = truth.receipt(env, raw=raw, sig=sig)
    assert truth.process(rid) == []
    assert truth.count("facts") == truth.count("postings") == 0
    record(attack, truth)


def test_authenticated_unknown_object_books_only_account_suspense(truth):
    v = json.loads(truth.provider()["raw"])
    v["payload"]["payment"]["entity"]["order_id"] = "order_unknown"
    v["operation_id"] = "op_unknown"
    raw = canonical(v)
    truth.process(truth.receipt(raw=raw, sig=signature(KEYS["reference"], raw)))
    data = truth.export()
    assert len(data["facts"]) == len(data["postings"]) == 1
    assert data["facts"][0]["tenant_id"] is None and data["facts"][0]["operation_id"] is None
    assert data["lines"][1]["account"] == "UnmatchedProviderSuspense"
    record("unknown_object_account_suspense", truth)


def test_stale_weaker_and_contradictory_observations(truth):
    env = truth.provider()
    truth.process(truth.receipt(env))
    with create_engine(truth.urls["reference"]).begin() as db:
        db.execute(
            text("INSERT INTO ref_economic.delivery_schedule VALUES(:order,ARRAY['AUTHORIZED','FAILED'])"),
            {"order": truth.order},
        )
    for ordinal in (1, 2):
        delivery = truth.sql(
            "SELECT ref_economic.deliver(:order,:ordinal)",
            {"order": truth.order, "ordinal": ordinal},
            "reference",
        )
        truth.process(truth.receipt(delivery))
    assert truth.count("facts") == 2 and truth.count("postings") == 2
    assert truth.d.sql("SELECT blocked,strength FROM economic.object_heads")[0] == (True, 2)
    with pytest.raises(Exception):
        truth.step("capability", operation=truth.op["id"])
    record("stale_weaker_and_sticky_contradiction", truth, classification_detail="VERIFIED_REFERENCE_FAULT")


@pytest.mark.parametrize("role", ["api", "broker", "worker", "economic", "seller", "reference"])
@pytest.mark.parametrize(
    "table",
    [
        "economic.facts",
        "economic.lines",
        "economic.postings",
        "seller_truth.entitlements",
        "seller_truth.orders",
    ],
)
def test_actual_login_roles_cannot_forge_truth(truth, role, table):
    domain = "seller" if table.startswith("seller") else "control"
    url = truth.urls[domain].replace("interlock:local_only@", "interlock_" + role + ":local_only@")
    with pytest.raises(DBAPIError), create_engine(url).begin() as db:
        db.execute(text("INSERT INTO " + table + " DEFAULT VALUES"))
    RECORDS.append(
        {"gate": "direct_sql_" + role + "_" + table, "classification": "VERIFIED_POSTGRES", "denied": True}
    )


@pytest.mark.parametrize("field", ["tenant_id", "account_id", "purchase_id", "payment_id", "quote_id"])
def test_seller_cannot_fulfill_substituted_unsigned_capability(truth, field):
    truth.process(truth.receipt())
    cap = truth.step("capability", operation=truth.op["id"])
    v = json.loads(cap["raw"])
    v[field] = "substitution"
    with pytest.raises(DBAPIError):
        truth.sql(
            "SELECT seller_truth.finalize(:raw,:sig)",
            {"raw": canonical(v).decode(), "sig": cap["signature"]},
            "seller",
        )
    record("seller_unsigned_" + field, truth)


def test_seller_fulfillment_without_economic_evidence_and_arbitrary_api_inputs(truth):
    with pytest.raises(Exception):
        truth.step("capability", operation=truth.op["id"])
    api = truth.d.auth.clients["api"]
    for path in ("/economic/facts", "/economic/postings", "/economic/journal-lines"):
        response = api.post(
            path,
            json={"amount": 3000, "lines": [{"account": "SalesRevenue", "credit": 3000}]},
            headers={"Authorization": "Bearer " + issue_session("owner-a")},
        )
        assert response.status_code in {404, 405}
    record("no_economic_or_journal_caller_authority", truth)


@pytest.mark.parametrize("attack", ["beneficiary", "tenant", "account", "quantity", "quote_terms"])
def test_seller_invariants_reject_even_authenticated_substitution(truth, attack):
    truth.process(truth.receipt())
    cap = truth.step("capability", operation=truth.op["id"])
    v = json.loads(cap["raw"])
    if attack == "tenant":
        v["tenant_id"] = "tenant-b"
    elif attack == "account":
        v["account_id"] = "other-account"
    elif attack == "quantity":
        v["quote"]["lines"].append(copy.deepcopy(v["quote"]["lines"][0]))
    elif attack == "quote_terms":
        v["quote"]["amount"] += 1
    else:
        v["quote"]["lines"][0]["beneficiary"] = "bob"
    raw = canonical(v).decode()
    with pytest.raises(DBAPIError):
        truth.sql(
            "SELECT seller_truth.finalize(:raw,:sig)",
            {"raw": raw, "sig": signature(KEYS["capability"], raw.encode())},
            "seller",
        )
    record("seller_authenticated_" + attack, truth)


def test_seller_revocation_excludes_access_and_preserves_delivery(truth):
    truth.process(truth.receipt())
    truth.finalize()
    seat = truth.quote["lines"][0]["allocation_id"]
    with create_engine(truth.urls["seller"]).begin() as db:
        db.execute(
            text("SELECT seller_truth.revoke(:seat,'local access suspension fixture')"), {"seat": seat}
        )
    with pytest.raises(DBAPIError):
        truth.sql("SELECT seller_truth.workspace('seat-token-a',:seat,NULL)", {"seat": seat}, "seller")
    assert truth.count("facts") == 4 and truth.count("postings") == 3
    record("seller_revocation_preserves_historical_fulfillment", truth)


def test_existing_razorpay_webhook_shape_requires_canonical_read(truth):
    # Frozen wire shape only; no external call or new Razorpay behavioral experiment.
    v = json.loads(truth.provider()["raw"])
    v["account_id"] = "acc_TEST"
    # A reference order cannot be relabeled as a Razorpay object. This modeled
    # webhook is authenticated but deliberately unmatched in the Razorpay lane.
    v["payload"]["payment"]["entity"]["order_id"] = "order_UNKNOWNRZ"
    raw = canonical(v)
    rid = truth.receipt(raw=raw, sig=signature(KEYS["razorpay"], raw), channel="razorpay-tenant-a")
    assert truth.process(rid) == []
    assert truth.count("observations") == 1 and truth.count("facts") == 0
    record("razorpay_webhook_only_observation", truth, classification_detail="MODELED_FROZEN_WIRE_SHAPE")


@pytest.mark.parametrize(
    "tamper",
    [
        "balanced_wrong_account",
        "fact_amount",
        "receipt_bytes",
        "beneficiary",
        "tenant",
        "double_post",
        "parent",
        "authentication",
        "fact_account",
        "fact_integration",
        "fact_provider",
        "observation_object",
    ],
)
def test_independent_checker_rejects_meaningful_tampering(truth, tamper):
    truth.process(truth.receipt())
    truth.finalize()
    data = copy.deepcopy(truth.export())
    if tamper == "balanced_wrong_account":
        data["lines"][0]["account"] = "SalesRevenue"
    elif tamper == "fact_amount":
        data["facts"][0]["amount"] += 1
    elif tamper == "receipt_bytes":
        data["receipts"][0]["body_b64"] = "e30="
    elif tamper == "beneficiary":
        data["entitlements"][0]["beneficiary"] = "bob"
    elif tamper == "tenant":
        data["entitlements"][0]["tenant_id"] = "tenant-b"
    elif tamper == "double_post":
        p = copy.deepcopy(data["postings"][0])
        p["id"] = str(uuid4())
        data["postings"].append(p)
    elif tamper == "parent":
        next(x for x in data["facts"] if x["kind"] == "REVENUE")["parent_fact_id"] = data["facts"][0]["id"]
    elif tamper == "authentication":
        data["receipt_states"][0]["authentication"] = "REJECTED"
    elif tamper.startswith("fact_"):
        field = {
            "fact_account": "account_id",
            "fact_integration": "integration",
            "fact_provider": "provider",
        }[tamper]
        next(x for x in data["facts"] if x["kind"] == "CAPTURE")[field] = "substitution"
    else:
        data["observations"][0]["object_id"] = "pay_SUBSTITUTED"
    with pytest.raises((ValueError, KeyError)):
        verify(data)
    RECORDS.append({"gate": "tamper_" + tamper, "classification": "VERIFIED_POSTGRES", "rejected": True})
