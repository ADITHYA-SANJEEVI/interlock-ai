"""NOT RUN until a human verifies disposable PostgreSQL configuration.

Actual SQL boundaries with synthetic model/PSP fixtures; no external providers.
This directory must never import tests/conftest.py or its migration/cleanup hooks.
"""

import base64
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from interlock.contracts import canonical, signature
from interlock.paypal_flow import BoundaryFailure
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import FIXTURE_CAPABILITY, FIXTURE_QUOTE, FIXTURE_SESSION, Harness, deadline


class ProtectedPostgresAcceptance(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def test_permitted_purchase_facts_journal_and_controlled_seller(self):
        h = self.h.prepare().ready_capture()
        result = h.driver.capture(h.op)
        self.assertTrue(result["capture_confirmed"])
        h.economic("process_receipt", {"rid": result["receipt_id"]})
        capability = h.economic("capability", {"opid": h.op})
        self.assertEqual(signature(FIXTURE_CAPABILITY, capability["raw"].encode()), capability["signature"])
        attestation = h.sql("SELECT seller_truth.paypal_finalize_v1(:raw,:signature)", capability, "seller")
        rid = h.economic("receive", {"channel": "seller-" + h.tenant, "raw": attestation["raw"].encode(),
                                     "headers": json.dumps({"signature": attestation["signature"]}), "delivery": "synthetic-finalize"})
        h.economic("process_receipt", {"rid": str(rid)})
        proof = h.api("inspect", {"token": h.token("issuer"), "opid": h.op})
        self.assertEqual({f["kind"] for f in proof["economic"]["facts"]},
                         {"CAPTURE", "ORDER_LIABILITY", "SELLER_FULFILLED", "REVENUE"})
        self.assertEqual(len(proof["economic"]["postings"]), 3)
        self.assertIsNotNone(proof["economic"]["seller_entitlement"])
        self.assertEqual(h.vector(), [500, 0, 100, 400])  # Preserve the kernel's retained lifetime reservation.
        self.assertEqual((h.provider.creates, h.provider.captures), (1, 1))
        self.assertTrue(all(p["currency"] == "USD" for p in proof["economic"]["postings"]))

    def test_over_budget_denial_reserves_nothing_and_never_calls_provider(self):
        with self.assertRaisesRegex(BoundaryFailure, "^INSUFFICIENT_AUTHORITY$"):
            self.h.prepare(bound=50)
        self.assertEqual(self.h.vector(), [50, 0, 0, 50])
        self.assertEqual(self.h.provider.creates, 0)

    def test_unauthorized_beneficiary_denied_before_quote_or_reservation(self):
        with self.assertRaisesRegex(BoundaryFailure, "^UNTRUSTED_PROPOSAL_REJECTED$"):
            self.h.prepare(beneficiary="mallory")
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        self.assertEqual(self.h.provider.creates, 0)

    def test_missing_invalid_and_self_approval_are_denied(self):
        h = self.h
        h.sql("""UPDATE authority.memberships SET roles=ARRAY['PURCHASE_REQUESTER','PURCHASE_APPROVER'],
              revision=revision+1 WHERE principal=:principal RETURNING principal""",
              {"principal": h.people["requester"]})
        h.prepare(approve=False, admit=False)
        for exact in (h.exact, {**h.exact, "approval_id": "nonexistent_approval"}):
            with self.assertRaises(BoundaryFailure):
                h.command("admit", exact, 1)
        with self.assertRaisesRegex(BoundaryFailure, "SELF_APPROVAL_DENIED"):
            h.command("approve", {**h.exact, "expires_at": deadline()}, 1, "requester")
        self.assertEqual(h.vector(), [500, 0, 0, 500])

    def test_revocation_before_capture_keeps_obligation_and_stops_send(self):
        h = self.h.prepare().ready_capture()
        h.command("revoke-grant", {"grant_id": h.grant["grant_id"]}, 2, "issuer")
        with self.assertRaises(BoundaryFailure):
            h.driver.capture(h.op)
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_duplicate_and_concurrent_create_capture_commit_once(self):
        h = self.h.prepare().ready_capture()
        h.driver.create(h.op)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: h.driver.capture(h.op), range(2)))
        h.driver.capture(h.op)
        self.assertEqual((h.provider.creates, h.provider.captures), (1, 1))
        self.assertEqual(h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id", {"id": h.op}), 2)
        self.assertEqual(h.sql("SELECT count(*) FROM authority.reservations WHERE action_id=:id", {"id": h.action["action_id"]}), 1)

    def test_lost_create_response_never_replays_or_releases_reservation(self):
        h = self.h.prepare()
        h.provider.lose_create = True
        self.assertEqual(h.driver.create(h.op)["knowledge"], "UNKNOWN")
        self.assertEqual(h.driver.create(h.op)["knowledge"], "UNKNOWN")
        self.assertEqual(h.provider.creates, 1)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_lost_capture_response_recovers_through_canonical_get(self):
        h = self.h.prepare().ready_capture()
        h.provider.lose_capture = True
        self.assertTrue(h.driver.capture(h.op)["capture_confirmed"])
        self.assertTrue(h.driver.capture(h.op)["capture_confirmed"])
        self.assertEqual(h.provider.captures, 1)

    def test_currency_merchant_and_native_order_mismatch_are_sticky_conflicts(self):
        h = self.h.prepare().ready_capture()
        for mutation in (lambda d: d.update(id="OTHERORDER123456"),
                         lambda d: d["purchase_units"][0]["amount"].update(currency_code="INR"),
                         lambda d: d["purchase_units"][0]["payee"].update(merchant_id="OTHERSELLER12345")):
            h.provider.override = mutation
            self.assertTrue(h.driver.capture(h.op)["blocked"])
        self.assertTrue(h.sql("SELECT blocked FROM paypal_v1.runtime WHERE operation_id=:id", {"id": h.op}))
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_contradictory_capture_evidence_blocks_fulfillment_without_erasing_facts(self):
        h = self.h.prepare().ready_capture()
        result = h.driver.capture(h.op)
        h.economic("process_receipt", {"rid": result["receipt_id"]})
        h.provider.captured, h.provider.status = False, "APPROVED"
        self.assertTrue(h.driver.observe(h.op)["blocked"])
        with self.assertRaises(BoundaryFailure):
            h.economic("capability", {"opid": h.op})
        self.assertEqual(h.sql("SELECT count(*) FROM economic.facts WHERE operation_id=:id", {"id": h.op}), 2)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_unavailable_provider_evidence_never_authorizes_capture(self):
        h = self.h.prepare().ready_capture()
        h.provider.unavailable = True
        self.assertFalse(h.driver.capture(h.op)["capture_sent"])
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_runtime_roles_cannot_read_private_tables_or_bypass_legacy_guard(self):
        h = self.h.prepare()
        for service in ("api", "broker", "economic"):
            with self.assertRaises(DBAPIError):
                h.sql("SELECT * FROM paypal_v1.phase_commits", service=service)
        with self.assertRaises(DBAPIError):
            h.sql("SELECT authority.paypal_prepare_v1(:token,'admit','{}'::jsonb,1,'bypass_attempt')",
                  {"token": h.token("requester")}, "api")

    def test_legacy_inr_reference_admission_is_unchanged(self):
        h = self.h
        tenant = "legacy_" + uuid4().hex
        issuer, requester = tenant + "_issuer", tenant + "_requester"
        h.sql("""INSERT INTO public.tenants VALUES(:id,'synthetic-buyer','synthetic-merchant',
              'workspace-seller-v1',:id,'NO_REAL_ACCOUNT') RETURNING id""", {"id": tenant})
        for user, role in ((issuer, "PURCHASE_ISSUER"), (requester, "PURCHASE_REQUESTER")):
            h.sql("""INSERT INTO public.identities VALUES(:user,:tenant,'alice','SYNTHETIC_HASH','owner') RETURNING username""",
                  {"user": user, "tenant": tenant})
            h.sql("INSERT INTO authority.memberships(principal,tenant_id,roles) VALUES(:user,:tenant,:roles) RETURNING principal",
                  {"user": user, "tenant": tenant, "roles": [role]})
        h.sql("INSERT INTO authority.tenant_control(tenant_id,kind) VALUES(:id,'PURCHASE') RETURNING tenant_id", {"id": tenant})

        def token(user):
            payload = base64.urlsafe_b64encode(canonical({"user": user, "exp": int(time.time()) + 3600})).decode()
            return payload + "." + signature(FIXTURE_SESSION, payload.encode())

        def command(family, body, expected=0, user=issuer):
            return h.sql("SELECT authority.command(:token,:family,CAST(:body AS jsonb),:expected,:idem)",
                         {"token": token(user), "family": family, "body": json.dumps(body), "expected": expected,
                          "idem": "legacy_" + uuid4().hex}, "api")

        command("publish-policy", {"kind": "PURCHASE", "approval_above": 1000000, "max_amount": 1000000}, 1)
        grant = command("create-grant", {"kind": "PURCHASE", "subject": requester, "lifetime_bound": 6000,
                                         "currency": "INR", "beneficiaries": ["alice"], "expires_at": deadline(), "resource_id": None})
        command("activate-grant", {"grant_id": grant["grant_id"]}, 1)
        q = {"id": "legacyquote_" + uuid4().hex, "tenant_id": tenant, "seller": "workspace-seller-v1", "revision": 1,
             "amount": 3000, "currency": "INR", "expires_at": deadline(), "lines": [{"sku": "workspace-seat",
             "beneficiary": "alice", "unit_price": 3000, "allocation_id": "legacyseat_" + uuid4().hex}]}
        raw = canonical(q)
        h.sql("SELECT authority.register_quote(:token,:raw,:sig,:idem)",
              {"token": token(requester), "raw": raw.decode(), "sig": signature(FIXTURE_QUOTE, raw),
               "idem": "quote_" + uuid4().hex}, "api")
        action = command("propose", {"grant_id": grant["grant_id"], "quote_id": q["id"]}, user=requester)
        command("admit", {k: action[k] for k in ("action_id", "evidence_id", "decision_id")}, 1, requester)
        result = h.sql("SELECT jsonb_build_array(adapter,currency,amount) FROM dispatch.business_operations WHERE action_id=:id",
                       {"id": action["action_id"]})
        self.assertEqual(result, ["REFERENCE_SYNTHETIC", "INR", 3000])


if __name__ == "__main__":
    unittest.main()
