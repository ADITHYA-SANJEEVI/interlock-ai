"""Pass 4 exercises actual login roles, independent seller/provider databases."""

import json
from uuid import uuid4

import httpx
import pytest
from interlock import economic, returns, returns_broker
from interlock.security import issue_session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from test_authority import auth as auth
from test_authority import deadline
from test_dispatch import TOKENS
from test_dispatch import d as d
from test_economic import truth as truth

from tools.pass3.export import write
from tools.pass4.checker import verify
from tools.pass4.export import export

RECORDS = []


@pytest.fixture(scope="session", autouse=True)
def save_returns():
    yield
    write("tmp/pass4-postgres.json", RECORDS)


class ReturnHarness:
    def __init__(self, truth):
        self.truth = truth
        self.d = truth.d
        self.api = self.d.auth.clients["api"]
        self.grant = None
        self.lock = None
        self.calls = []

    def sql(self, statement, params=None, role="api"):
        url = self.truth.runtime.get(
            role,
            self.truth.urls["control"].replace("interlock:local_only@", "interlock_" + role + ":local_only@"),
        )
        with create_engine(url, hide_parameters=True).begin() as db:
            return db.scalar(text(statement), params or {})

    def command(self, family, body, principal="refund-requester-a", revision=0, key=None):
        response = self.api.post(
            "/returns/commands/" + family,
            json=body,
            headers={
                "Authorization": "Bearer " + issue_session(principal),
                "If-Match": str(revision),
                "Idempotency-Key": key or "return_" + uuid4().hex,
            },
        )
        self.check("command_" + family, status=response.status_code)
        return response

    def check(self, step, **extra):
        pack = export(self.truth.urls)
        checked = verify(pack)
        RECORDS.append({"step": step, "classification": "VERIFIED_POSTGRES", "checked": checked, **extra})
        write("tmp/pass4-audit-export.json", pack)
        return pack

    def ok(self, *args, **kwargs):
        response = self.command(*args, **kwargs)
        assert response.status_code == 200, response.text
        return response.json()

    def prepare(self, approval=False, limit=3000):
        self.d.auth.policy("REFUND", approval_above=0 if approval else 1000000)
        self.grant = self.ok(
            "create-grant",
            {
                "purchase_operation": self.truth.op["id"],
                "subject": "refund-requester-a",
                "limit": limit,
                "expires_at": deadline(),
                "beneficiaries": ["alice"],
            },
            "merchant-a",
        )
        result = self.api.post(
            "/returns/lock",
            json={"purchase_operation": self.truth.op["id"]},
            headers={"Authorization": "Bearer " + issue_session("refund-requester-a")},
        )
        assert result.status_code == 200, result.text
        self.lock = result.json()["lock_id"]
        self.check("seller_lock_confirmed")
        return self.lock

    def propose(self, amount=1000, grant=None):
        return self.ok(
            "propose", {"lock_id": self.lock, "grant_id": (grant or self.grant)["grant_id"], "amount": amount}
        )

    @staticmethod
    def exact(action):
        return {key: action[key] for key in ("action_id", "evidence_id", "decision_id")}

    def admit(self, action, approval=None, key=None):
        return self.command("admit", {**self.exact(action), "approval_id": approval}, revision=1, key=key)

    def admitted(self, amount=1000):
        action = self.propose(amount)
        response = self.admit(action)
        assert response.status_code == 200, response.text
        return response.json()

    def claim(self, op):
        return self.sql(
            "SELECT returns.claim(:secret,CAST(:op AS uuid))",
            {"secret": TOKENS["claim"], "op": op["operation_id"]},
            "worker",
        )

    def send(self, op):
        claim = self.claim(op)
        assert claim
        response = self.d.auth.clients["broker"].post(
            "/returns/handoff", json=claim, headers=self.d.auth.clients["internal"]
        )
        self.check("broker_handoff", status=response.status_code)
        return response

    def fault(self, op, fault, delay=0):
        with create_engine(self.truth.urls["reference"]).begin() as db:
            db.execute(
                text("INSERT INTO ref_returns.schedule VALUES(:op,:fault,:delay)"),
                {"op": op["operation_id"], "fault": fault, "delay": delay},
            )

    def observe(self, op):
        response = self.d.auth.clients["broker"].post(
            "/returns/observe",
            json={"operation_id": op["operation_id"]},
            headers=self.d.auth.clients["internal"],
        )
        self.check("broker_observation", status=response.status_code)
        return response

    def resolve(self, op):
        rows = self.d.sql(
            "SELECT id FROM economic.receipts WHERE channel_id='reference-tenant-a' ORDER BY received_at"
        )
        observation = None
        for row in rows:
            economic.kernel("authenticate", {"receipt": str(row[0])}, self.truth.runtime["economic"])
            candidate = returns.kernel("observe", {"receipt": str(row[0])}, self.truth.runtime["economic"])
            if candidate:
                observation = str(candidate)
                self.check("refund_observation")
        facts = returns.kernel(
            "derive",
            {"operation": op["operation_id"], "observation": observation},
            self.truth.runtime["economic"],
        )
        self.check("refund_fact_derivation")
        for fact in facts:
            returns.kernel("post", {"fact": fact}, self.truth.runtime["economic"])
            self.check("trusted_posting")
        return facts

    def snapshot(self):
        return self.sql("SELECT returns.inspect(:token)", {"token": issue_session("refund-requester-a")})


@pytest.fixture
def refund(truth, monkeypatch):
    truth.process(truth.receipt())
    truth.finalize()
    h = ReturnHarness(truth)
    with create_engine(truth.urls["reference"]).begin() as db:
        db.execute(text("TRUNCATE ref_returns.schedule,ref_returns.refunds,ref_returns.calls"))

    def kernel(name, params, url=None):
        role = (
            "broker"
            if name in {"binding", "cutoff"}
            else "economic"
            if name in {"observe", "derive", "post", "pending"}
            else "api"
        )
        url = url or truth.urls["control"].replace(
            "interlock:local_only@", "interlock_" + role + ":local_only@"
        )
        return original(name, params, url)

    original = returns.kernel
    monkeypatch.setattr(returns, "kernel", kernel)
    monkeypatch.setattr(returns_broker, "kernel", kernel)
    monkeypatch.setattr(
        returns,
        "seller_call",
        lambda path, body: h.sql(
            "SELECT seller_truth."
            + ("return_lock" if path.endswith("lock") else "return_close")
            + "(:raw,:signature)",
            body,
            "seller",
        ),
    )
    original_receive = returns_broker.receive
    monkeypatch.setattr(
        returns_broker,
        "receive",
        lambda *args: original_receive(
            *args, url=truth.urls["control"].replace("interlock:local_only@", "interlock_broker:local_only@")
        ),
    )

    def transport(method, body=None, params=None):
        # Distinct owner connection can acquire local locks during network handoff.
        with create_engine(truth.urls["control"]).begin() as db:
            for table in ("authority.grant_heads", "returns.principal", "returns.runtime", "returns.outbox"):
                db.execute(text("SELECT * FROM " + table + " FOR UPDATE NOWAIT"))
        h.calls.append(method)
        if method == "POST":
            result = h.sql(
                "SELECT ref_returns.mutate(CAST(:body AS jsonb))", {"body": json.dumps(body)}, "reference"
            )
        else:
            result = h.sql(
                "SELECT ref_returns.read_refund(:op,:account)",
                {"op": params["operation"], "account": params["account"]},
                "reference",
            )
        if result is None:
            raise httpx.ReadTimeout("Synthetic lost response")
        return result

    monkeypatch.setattr(returns_broker, "provider_call", transport)
    yield h


def test_partial_then_full_exact_return(refund):
    purchase_before = refund.d.sql(
        "SELECT * FROM authority.grant_heads WHERE grant_id NOT LIKE 'refund_grant_%'"
    )
    refund.prepare()
    first = refund.admitted()
    assert refund.send(first).status_code == 200
    assert refund.snapshot()["principal"][0]["unresolved"] == 1000
    facts = refund.resolve(first)
    assert len(facts) == 2
    partial = refund.snapshot()
    assert (partial["principal"][0]["processed"], partial["principal"][0]["unresolved"]) == (1000, 0)
    assert (
        partial["grants"][0]["consumed"],
        partial["grants"][0]["reserved"],
        partial["grants"][0]["free"],
    ) == (1000, 0, 2000)
    with pytest.raises(DBAPIError):
        refund.sql(
            "SELECT seller_truth.workspace(:token,:seat,NULL)",
            {"token": "seat-token-a", "seat": refund.truth.quote["lines"][0]["allocation_id"]},
            "seller",
        )
    response = refund.api.post(
        "/returns/close",
        json={"lock_id": refund.lock},
        headers={"Authorization": "Bearer " + issue_session("refund-requester-a")},
    )
    assert response.status_code == 403
    second = refund.admitted(2000)
    assert refund.send(second).status_code == 200
    refund.resolve(second)
    response = refund.api.post(
        "/returns/close",
        json={"lock_id": refund.lock},
        headers={"Authorization": "Bearer " + issue_session("refund-requester-a")},
    )
    assert response.status_code == 200, response.text
    snapshot = refund.snapshot()
    assert len(snapshot["closures"]) == 1 and len(snapshot["postings"]) == 4
    assert snapshot["principal"][0]["processed"] == 3000
    assert snapshot["grants"][0]["free"] == 0
    assert (
        refund.d.sql("SELECT * FROM authority.grant_heads WHERE grant_id NOT LIKE 'refund_grant_%'")
        == purchase_before
    )


def test_unknown_retains_every_obligation_and_no_resend(refund):
    refund.prepare()
    action = refund.propose(3000)
    key = "stable_admission_" + uuid4().hex
    response = refund.admit(action, key=key)
    assert response.status_code == 200, response.text
    op = response.json()
    assert refund.admit(action, key=key).json() == op
    refund.fault(op, "LOST_RESPONSE", delay=20)
    assert refund.send(op).json()["state"] == "UNKNOWN"
    assert refund.claim(op) is None
    assert refund.observe(op).json()["state"] == "UNKNOWN"
    refund.resolve(op)
    state = refund.snapshot()
    assert state["principal"][0]["unresolved"] == 3000 and state["grants"][0]["reserved"] == 3000
    assert state["operations"][0]["recovery_duty"] and len(state["locks"]) == 1
    assert refund.calls.count("POST") == 1
    with create_engine(refund.truth.urls["reference"]).begin() as db:
        db.execute(text("UPDATE ref_dispatch.clock SET tick=tick+30"))
    refund.observe(op)
    refund.resolve(op)
    assert refund.snapshot()["principal"][0]["processed"] == 3000


def test_seller_lock_precedes_final_action_and_duplicates_are_exact(refund):
    refund.prepare()
    action = refund.propose()
    row = refund.d.sql(
        "SELECT a.created_at,l.confirmed_at,a.terms,e.return_lock_id FROM authority.action_revisions a JOIN authority.evidence_revisions e ON e.action_id=a.id JOIN returns.locks l ON l.id=e.return_lock_id WHERE a.id=:id",
        {"id": action["action_id"]},
    )[0]
    assert row[0] >= row[1] and row[2]["seller_lock"]["lock_id"] == str(row[3])
    result = refund.api.post(
        "/returns/lock",
        json={"purchase_operation": refund.truth.op["id"]},
        headers={"Authorization": "Bearer " + issue_session("refund-requester-a")},
    )
    assert result.json()["lock_id"] == refund.lock


@pytest.mark.parametrize(
    "field",
    [
        "tenant",
        "account",
        "integration",
        "payment",
        "amount",
        "currency",
        "purchase",
        "workspace",
        "seat",
        "beneficiary",
        "host",
        "url",
        "method",
        "headers",
        "body",
        "operation_key",
        "provider_order",
        "provider_refund",
        "seller_order",
        "quote",
        "obligation",
        "grant",
        "approval",
    ],
)
def test_broker_rejects_substitution_before_handoff(refund, field):
    response = refund.d.auth.clients["broker"].post(
        "/returns/handoff",
        json={"operation_id": str(uuid4()), "claim_id": str(uuid4()), field: "substitution"},
        headers=refund.d.auth.clients["internal"],
    )
    assert response.status_code == 422 and not refund.calls


def test_revocation_blocks_dispatch_but_preserves_obligation(refund):
    refund.prepare()
    op = refund.admitted()
    refund.d.auth.ok("revoke-grant", {"grant_id": refund.grant["grant_id"]}, 1, "merchant-a")
    assert refund.send(op).status_code == 403
    assert len(refund.snapshot()["obligations"]) == 1
    assert refund.snapshot()["principal"][0]["unresolved"] == 1000
    assert not refund.calls
