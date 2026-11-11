"""Actual SQL transactions; synthetic identities only, no provider HTTP."""

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from interlock.contracts import canonical, signature
from interlock.paypal_flow import BoundaryFailure
from interlock.paypal_services import create_api
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import FIXTURE_QUOTE_REQUEST, Harness, deadline


class ExactApprovalAcceptance(unittest.TestCase):
    def setUp(self):
        self.h = Harness().prepare(approve=False, admit=False)
        self.addCleanup(self.h.close)

    def approve(self, idem=None, expires=None):
        return self.h.command("approve", {**self.h.exact, "expires_at": expires or deadline()},
                              1, "approver", idem)

    def count(self):
        return self.h.sql("SELECT count(*) FROM authority.approvals WHERE action_id=:id", {"id": self.h.action["action_id"]})

    def test_distinct_keys_same_principal_do_not_create_additional_approvals_or_votes(self):
        first = self.approve()
        with self.assertRaisesRegex(BoundaryFailure, "EXACT_APPROVAL_ALREADY_RECORDED"):
            self.approve()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.h.sql("SELECT count(*) FROM authority.request_identities WHERE tenant_id=:id AND family='approve'",
                                   {"id": self.h.tenant}), 1)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        exact = {**self.h.exact, "approval_id": first["approval_id"]}
        self.h.command("admit", exact, 1)
        with self.assertRaises(BoundaryFailure):
            self.h.command("admit", exact, 1)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])
        self.assertEqual(self.h.sql("SELECT count(*) FROM authority.reservations WHERE action_id=:id",
                                   {"id": self.h.action["action_id"]}), 1)

    def test_same_key_refresh_retry_recovers_identical_approval_without_extending_expiry(self):
        key, expires = "approval_" + uuid4().hex, deadline()
        first = self.approve(key, expires)
        self.assertEqual(self.approve(key, expires), first)
        with self.assertRaisesRegex(BoundaryFailure, "IDEMPOTENCY_CONFLICT"):
            self.approve(key, (datetime.now(UTC) + timedelta(minutes=30)).isoformat())
        self.assertEqual(self.count(), 1)

    def test_concurrent_commands_from_two_sessions_commit_only_one_exact_approval(self):
        barrier = threading.Barrier(2)
        body = {**self.h.exact, "expires_at": deadline(), "currency": "USD"}

        def submit():
            # Separate clients and DB transactions, same authenticated principal.
            with TestClient(create_api(self.h.api, object(), lambda *_: {})) as client:
                barrier.wait(timeout=5)
                return client.post("/v1/paypal/commands/approve", json=body, headers={
                    "Authorization": "Bearer " + self.h.token("approver"),
                    "Idempotency-Key": "browser_" + uuid4().hex, "If-Match": "1"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])

    def test_concurrent_same_key_returns_one_approval_to_both_sessions(self):
        barrier, key, expires = threading.Barrier(2), "approval_" + uuid4().hex, deadline()

        def submit():
            barrier.wait(timeout=5)
            return self.approve(key, expires)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count(), 1)

    def test_expired_approval_cannot_admit_or_count_as_current_and_history_is_preserved(self):
        expires = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
        first = self.approve(expires=expires)
        time.sleep(1.05)
        with self.assertRaisesRegex(BoundaryFailure, "APPROVAL_INVALID"):
            self.h.command("admit", {**self.h.exact, "approval_id": first["approval_id"]}, 1)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        second = self.approve()
        self.assertNotEqual(first["approval_id"], second["approval_id"])
        self.assertEqual(self.count(), 2)
        snapshot = self.h.api("snapshot", {"token": self.h.token("approver")})
        current = [a for a in snapshot["approvals"] if a["action_id"] == self.h.action["action_id"]
                   and a["effective_status"] == "AVAILABLE"]
        self.assertEqual([a["id"] for a in current], [second["approval_id"]])

    def test_expired_quote_cannot_be_approved_and_existing_approval_cannot_admit(self):
        first = self.approve()
        # A separate short-lived authentic signed quote, never rewriting history.
        request = {"schema": "paypal-quote-request-v1", "tenant_id": self.h.tenant,
                   "request_id": "short_" + uuid4().hex, "product_id": "workspace-seat", "beneficiary": "alice",
                   "quantity": 1, "currency": "USD", "expires_at": (datetime.now(UTC) + timedelta(seconds=2)).isoformat()}
        raw = canonical(request)
        sealed = self.h.sql("SELECT seller_truth.paypal_quote_v1(:raw,:sig)",
                           {"raw": raw.decode(), "sig": signature(FIXTURE_QUOTE_REQUEST, raw)}, "seller")
        quote = self.h.api("quote", {"token": self.h.token("requester"), **sealed, "idem": "short_" + uuid4().hex})
        pid = self.h.sql("SELECT id::text FROM paypal_v1.proposals WHERE tenant_id=:id LIMIT 1", {"id": self.h.tenant})
        self.h.api("link_quote", {"token": self.h.token("requester"), "pid": pid, "quoteid": quote["id"]})
        action = self.h.command("propose", {"grant_id": self.h.grant["grant_id"], "quote_id": quote["id"]})
        exact = {k: action[k] for k in ("action_id", "evidence_id", "decision_id")}
        approved = self.h.command("approve", {**exact, "expires_at": deadline()}, 1, "approver")
        time.sleep(2.05)
        for family, data, role in (("approve", {**exact, "expires_at": deadline()}, "approver"),
                                   ("admit", {**exact, "approval_id": approved["approval_id"]}, "requester")):
            with self.assertRaisesRegex(BoundaryFailure, "EVIDENCE_STALE"):
                self.h.command(family, data, 1, role)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        self.assertNotEqual(first["approval_id"], approved["approval_id"])
        self.assertEqual((self.h.provider.creates, self.h.provider.captures), (0, 0))

    def test_historical_duplicate_rows_never_increase_independent_principals_or_spending(self):
        # Reproduce the pre-guard SQL semantics in disposable fixtures only.
        # This is not a public HTTP path and does not touch demo history.
        approvals = [self.h.sql("SELECT paypal_v1.command(:token,'approve',CAST(:body AS jsonb),1,:idem)", {
            "token": self.h.token("approver"), "body": json.dumps({**self.h.exact, "expires_at": deadline()}),
            "idem": "historical_" + uuid4().hex}, "api") for _ in range(3)]
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.h.sql("SELECT count(DISTINCT approver) FROM authority.approvals WHERE action_id=:id",
                                   {"id": self.h.action["action_id"]}), 1)
        with self.assertRaisesRegex(BoundaryFailure, "EXACT_APPROVAL_AMBIGUOUS"):
            self.h.command("admit", {**self.h.exact, "approval_id": approvals[0]["approval_id"]}, 1)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        barrier = threading.Barrier(2)

        def admit(approval):
            barrier.wait(timeout=5)
            try:
                return self.h.sql("SELECT paypal_v1.command(:token,'admit',CAST(:body AS jsonb),1,:idem)", {
                    "token": self.h.token("requester"), "body": json.dumps({**self.h.exact, "approval_id": approval["approval_id"]}),
                    "idem": "historical_admit_" + uuid4().hex}, "api")
            except DBAPIError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, approvals[:2]))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])
        self.assertEqual(self.h.sql("SELECT count(*) FROM dispatch.business_operations WHERE action_id=:id",
                                   {"id": self.h.action["action_id"]}), 1)


if __name__ == "__main__":
    unittest.main()
