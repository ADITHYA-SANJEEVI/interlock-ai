"""Additional actual-PostgreSQL authority attacks; PSP/model remain synthetic."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from interlock.contracts import canonical, signature
from interlock.paypal_flow import BoundaryFailure
from interlock.paypal_reconciliation import execute
from interlock.paypal_services import create_api
from interlock.proposer import Proposer
from interlock.reconciliation import RunRequest
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import FIXTURE_QUOTE_REQUEST, Harness, deadline


class AdversarialPostgresAcceptance(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def test_paypal_approval_without_separate_capture_request_never_sends(self):
        h = self.h.prepare()
        h.driver.create(h.op)
        h.provider.status = "APPROVED"
        with self.assertRaisesRegex(BoundaryFailure, "SEPARATE_EXACT_CAPTURE_AUTHORIZATION_REQUIRED"):
            h.driver.capture(h.op)
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_buyer_owner_revision_change_invalidates_persisted_capture_authorization(self):
        h = self.h.prepare().ready_capture()
        h.sql("UPDATE authority.memberships SET revision=revision+1 WHERE principal=:p RETURNING principal",
              {"p": h.people["issuer"]})
        with self.assertRaisesRegex(BoundaryFailure, "BUYER_OWNER_CHANGED"):
            h.driver.capture(h.op)
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_requester_revocation_after_admission_prevents_capture(self):
        h = self.h.prepare().ready_capture()
        h.sql("UPDATE authority.memberships SET active=false,revision=revision+1 WHERE principal=:p RETURNING principal",
              {"p": h.people["requester"]})
        with self.assertRaises(BoundaryFailure):
            h.driver.capture(h.op)
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_unsigned_canonical_claim_cannot_create_receipt_or_capture_facts(self):
        h = self.h.prepare()
        h.driver.create(h.op)  # First establish that the authenticated read path works.
        before = h.sql("SELECT count(*) FROM economic.receipts")
        with self.assertRaises(BoundaryFailure):
            h.broker("record_read", {"secret": h.observe, "opid": h.op, "raw": '{"status":"COMPLETED"}',
                                      "sig": "0" * 64, "delivery": "forged_" + uuid4().hex})
        self.assertEqual(h.sql("SELECT count(*) FROM economic.receipts"), before)
        self.assertEqual(h.sql("SELECT count(*) FROM economic.facts WHERE operation_id=:id", {"id": h.op}), 0)

    def test_external_completed_capture_without_protected_commit_is_quarantined(self):
        h = self.h.prepare()
        h.driver.create(h.op)
        h.provider.status, h.provider.captured = "COMPLETED", True
        result = h.driver.observe(h.op)
        self.assertTrue(result["blocked"])
        self.assertEqual(h.sql("SELECT blocked_reason FROM paypal_v1.runtime WHERE operation_id=:id", {"id": h.op}),
                         "UNAUTHORIZED_EXTERNAL_CAPTURE")
        self.assertEqual(h.sql("SELECT count(*) FROM economic.facts WHERE operation_id=:id", {"id": h.op}), 0)
        self.assertEqual(h.provider.captures, 0)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_phase_identities_are_distinct_persistent_and_immutable(self):
        h = self.h.prepare()
        original = h.sql("SELECT jsonb_build_array(create_request_id,capture_request_id) FROM paypal_v1.bindings WHERE operation_id=:id", {"id": h.op})
        self.assertNotEqual(original[0], original[1])
        with self.assertRaises(DBAPIError):
            h.sql("UPDATE paypal_v1.bindings SET capture_request_id=:new WHERE operation_id=:id RETURNING operation_id",
                  {"new": str(uuid4()), "id": h.op})
        self.assertEqual(original, h.sql("SELECT jsonb_build_array(create_request_id,capture_request_id) FROM paypal_v1.bindings WHERE operation_id=:id", {"id": h.op}))

    def test_unchanged_journal_guard_rejects_incomplete_bundle_atomically(self):
        h = self.h.prepare().ready_capture()
        receipt = h.driver.capture(h.op)["receipt_id"]
        oid = h.sql("SELECT observation_id::text FROM economic.receipt_states WHERE receipt_id=:id", {"id": receipt})
        h.sql("SELECT economic.derive(CAST(:id AS uuid))", {"id": oid}, "economic")
        fid = h.sql("SELECT id::text FROM economic.facts WHERE operation_id=:id AND kind='CAPTURE'", {"id": h.op})
        with self.assertRaisesRegex(DBAPIError, "journal semantic bundle invalid"):
            h.sql("""INSERT INTO economic.postings(fact_id,template,provider,account_id,tenant_id,amount,currency)
                SELECT id,'CAPTURE_TO_SUSPENSE',provider,account_id,tenant_id,amount,currency
                FROM economic.facts WHERE id=CAST(:id AS uuid) RETURNING id""", {"id": fid})
        self.assertEqual(h.sql("SELECT count(*) FROM economic.postings WHERE fact_id=CAST(:id AS uuid)", {"id": fid}), 0)
        h.economic("post", {"fid": fid})
        self.assertEqual(h.sql("SELECT count(*) FROM economic.postings WHERE fact_id=CAST(:id AS uuid)", {"id": fid}), 1)
        for role in ("api", "broker", "economic"):
            with self.assertRaises(DBAPIError):
                h.sql("SELECT * FROM economic.lines", service=role)

    def test_reconciliation_records_unknown_without_mutating_provider(self):
        h = self.h.prepare()
        h.provider.lose_create = True
        h.driver.create(h.op)

        def seller(verb, body):
            return h.sql("SELECT seller_truth.reconciliation_read(:raw,:signature)", body, "seller")

        result = execute(h.api, seller, h.token("issuer"), RunRequest(request_id=uuid4(), batch_size=50))
        self.assertEqual(result.state, "COMPLETE")
        self.assertTrue(any(m.operation_id == h.op and m.state == "RECORDED" and m.evidence_count > 0 for m in result.members))
        self.assertEqual(h.sql("SELECT jsonb_build_array(class,state) FROM reconciliation.findings WHERE operation_id=:id",
                               {"id": h.op}), ["MUTATION_OUTCOME_UNKNOWN", "OPEN"])
        self.assertEqual((h.provider.creates, h.provider.captures), (1, 0))
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_product_http_contract_traverses_actual_sql_with_synthetic_model_and_provider(self):
        h = self.h.prepare(approve=False, admit=False)

        class SyntheticProposer(Proposer):
            def __init__(self):
                pass  # Explicit test-only injection. No HTTP client or model key.

            def propose(self, intent, beneficiaries):
                self.asserted_scope = beneficiaries
                return {"financial_authority": False, "extraction": {"status": "PROPOSED", "reason": "SUPPORTED_PURCHASE",
                    "proposal": {"product_id": "workspace-seat", "beneficiary": "alice", "quantity": 1,
                                 "spending_limit_minor": 100, "currency": "USD"}},
                        "provenance": {"actual_model_invocation": False, "classification": "SYNTHETIC_HTTP_SQL_ACCEPTANCE"}}

        proposer = SyntheticProposer()

        def broker(verb, body):
            if verb == "quote":
                raw = canonical(body)
                return h.sql("SELECT seller_truth.paypal_quote_v1(:raw,:sig)",
                             {"raw": raw.decode(), "sig": signature(FIXTURE_QUOTE_REQUEST, raw)}, "seller")
            return getattr(h.driver, verb)(body["operation_id"])

        with TestClient(create_api(h.api, proposer, broker)) as client:
            def headers(role, revision=None):
                return {"Authorization": "Bearer " + h.token(role), "Idempotency-Key": "http_" + uuid4().hex,
                        **({"If-Match": str(revision)} if revision is not None else {})}

            def post(path, body, role="requester", revision=None):
                response = client.post("/v1/paypal/" + path, json=body, headers=headers(role, revision))
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            proposal = post("proposals", {"grant_id": h.grant["grant_id"], "intent": "Synthetic seat, at most USD 1.00"})
            self.assertEqual(proposer.asserted_scope, ["alice"])
            quote = post("quotes", {"proposal_id": proposal["proposal_id"]})
            action = post("commands/propose", {"grant_id": h.grant["grant_id"], "quote_id": quote["quote_id"], "currency": "USD"}, revision=0)
            exact = {k: action[k] for k in ("action_id", "evidence_id", "decision_id")}
            approval = post("commands/approve", {**exact, "expires_at": deadline(), "currency": "USD"}, "approver", 1)
            renewal_headers = headers("requester")
            renewal_body = {"proposal_id": proposal["proposal_id"]}
            renewed = client.post("/v1/paypal/quote-renewals", json=renewal_body, headers=renewal_headers)
            self.assertEqual(renewed.status_code, 200, renewed.text)
            retry = client.post("/v1/paypal/quote-renewals", json=renewal_body, headers=renewal_headers)
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual(renewed.json(), retry.json())
            self.assertNotEqual(renewed.json()["quote_id"], quote["quote_id"])
            renewed_action = post("commands/propose", {"grant_id": h.grant["grant_id"],
                "quote_id": renewed.json()["quote_id"], "currency": "USD"}, revision=0)
            self.assertNotEqual(renewed_action["action_id"], action["action_id"])
            reused = client.post("/v1/paypal/commands/admit", headers=headers("requester", 1), json={
                **{k: renewed_action[k] for k in ("action_id", "evidence_id", "decision_id")},
                "approval_id": approval["approval_id"], "currency": "USD"})
            self.assertNotEqual(reused.status_code, 200)
            self.assertEqual((h.provider.creates, h.provider.captures), (0, 0))
            admission = post("commands/admit", {**exact, "approval_id": approval["approval_id"], "currency": "USD"}, revision=1)
            operation = {"operation_id": admission["operation_id"], "currency": "USD"}
            post("create", operation)

            def evidence():
                r = client.get("/v1/paypal/evidence/" + operation["operation_id"], headers=headers("issuer"))
                self.assertEqual(r.status_code, 200, r.text)
                return r.json()

            initial = evidence()
            self.assertEqual(initial["settlement"], "UNVERIFIED")
            consent = post("consent", operation, "issuer", initial["dispatch_revision"])
            self.assertIn(h.provider.order, consent["approval_url"])
            self.assertFalse(consent["settlement_verified"])
            h.provider.status = "APPROVED"  # Synthetic manual buyer consent.
            post("observe", operation, "issuer")
            captured = post("capture-requests", operation, "issuer", evidence()["revision"])
            self.assertTrue(captured["capture_confirmed"])
            h.economic("process_receipt", {"rid": captured["receipt_id"]})
            final = evidence()
            self.assertEqual(final["settlement"], "VERIFIED_CAPTURE")
            self.assertEqual((final["amount_minor"], final["currency"]), (100, "USD"))
            self.assertEqual(len(final["journals"]), 2)
            self.assertFalse(final["seller_fulfilled"])
            self.assertEqual((h.provider.creates, h.provider.captures), (1, 1))


if __name__ == "__main__":
    unittest.main()
