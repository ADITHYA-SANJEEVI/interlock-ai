"""Admitted-but-expired boundary tests on disposable SQL; synthetic PSP only."""

import time
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from interlock.contracts import canonical, signature
from interlock.paypal_flow import BoundaryFailure

from paypal_acceptance.harness import FIXTURE_QUOTE_REQUEST, Harness, deadline


class AdmittedExpiryAcceptance(unittest.TestCase):
    def setUp(self):
        self.h = Harness().prepare(approve=False, admit=False)
        self.addCleanup(self.h.close)

    def admit(self, exact, approval_expiry=None):
        approval = self.h.command("approve", {**exact, "expires_at": approval_expiry or deadline()}, 1, "approver")
        admitted = self.h.command("admit", {**exact, "approval_id": approval["approval_id"]}, 1)
        self.h.op = admitted["operation_id"]

    def test_admission_does_not_extend_quote_and_repeated_create_retains_reservation(self):
        h = self.h
        raw = canonical({"schema": "paypal-quote-request-v1", "tenant_id": h.tenant,
                         "request_id": "short_" + uuid4().hex, "product_id": "workspace-seat", "beneficiary": "alice",
                         "quantity": 1, "currency": "USD",
                         "expires_at": (datetime.now(UTC) + timedelta(seconds=3)).isoformat()})
        sealed = h.sql("SELECT seller_truth.paypal_quote_v1(:raw,:sig)",
                       {"raw": raw.decode(), "sig": signature(FIXTURE_QUOTE_REQUEST, raw)}, "seller")
        quote = h.api("quote", {"token": h.token("requester"), **sealed, "idem": "short_" + uuid4().hex})
        pid = h.sql("SELECT id::text FROM paypal_v1.proposals WHERE tenant_id=:id", {"id": h.tenant})
        h.api("link_quote", {"token": h.token("requester"), "pid": pid, "quoteid": quote["id"]})
        action = h.command("propose", {"grant_id": h.grant["grant_id"], "quote_id": quote["id"]})
        self.admit({k: action[k] for k in ("action_id", "evidence_id", "decision_id")})
        original = h.sql("SELECT jsonb_build_array(create_request_id,capture_request_id,wire_digest) FROM paypal_v1.bindings WHERE operation_id=:id", {"id": h.op})
        time.sleep(3.05)
        for _ in range(2):
            with self.assertRaisesRegex(BoundaryFailure, "QUOTE_EXPIRED"):
                h.driver.create(h.op)
        self.assertEqual(h.vector(), [500, 0, 100, 400])
        self.assertEqual(h.provider.creates, 0)
        self.assertEqual(h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id", {"id": h.op}), 0)
        self.assertEqual(h.sql("SELECT jsonb_build_array(create_request_id,capture_request_id,wire_digest) FROM paypal_v1.bindings WHERE operation_id=:id", {"id": h.op}), original)

    def test_consumed_approval_expiry_blocks_first_create_without_releasing_budget(self):
        self.admit(self.h.exact, (datetime.now(UTC) + timedelta(seconds=2)).isoformat())
        time.sleep(2.05)
        with self.assertRaisesRegex(BoundaryFailure, "APPROVAL_INVALID"):
            self.h.driver.create(self.h.op)
        self.assertEqual(self.h.provider.creates, 0)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])

    def test_unknown_create_then_expired_approval_never_replays_or_releases(self):
        self.admit(self.h.exact, (datetime.now(UTC) + timedelta(seconds=2)).isoformat())
        self.h.provider.lose_create = True
        self.assertEqual(self.h.driver.create(self.h.op)["knowledge"], "UNKNOWN")
        time.sleep(2.05)
        with self.assertRaisesRegex(BoundaryFailure, "APPROVAL_INVALID"):
            self.h.driver.create(self.h.op)
        self.assertEqual(self.h.provider.creates, 1)
        self.assertEqual(self.h.provider.captures, 0)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])
        self.assertEqual(self.h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id", {"id": self.h.op}), 1)


if __name__ == "__main__":
    unittest.main()
