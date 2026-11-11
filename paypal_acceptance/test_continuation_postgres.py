"""Actual isolated SQL, synthetic PSP only. Never touches the genuine demo order."""

import copy
import hashlib
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from interlock.contracts import canonical, signature
from interlock.paypal_continuation import capture
from interlock.paypal_flow import BoundaryFailure
from interlock.paypal_reconciliation import execute
from interlock.paypal_services import broker_app, create_api, seller_factory
from interlock.reconciliation import RunRequest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import FIXTURE_QUOTE, FIXTURE_QUOTE_REQUEST, Harness, deadline


class SameOrderContinuation(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.cid = str(uuid4())

    def approved(self, expired=False, grant_expires=None):
        h = self.h
        if expired:
            short = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
            with patch("paypal_acceptance.harness.deadline", side_effect=[grant_expires or deadline(), short, short]):
                h.prepare()
        else:
            h.prepare()
        h.driver.create(h.op)
        proof = self.proof()
        h.api("consent", {"token": h.token("issuer"), "opid": h.op,
                          "expected": proof["dispatch_revision"], "idem": "consent_" + uuid4().hex})
        h.provider.status = "APPROVED"
        h.driver.observe(h.op)
        if expired:
            time.sleep(max(0, datetime.fromisoformat(short).timestamp() - time.time()) + .05)
        h.sql("UPDATE paypal_v2.deployment SET enabled=true RETURNING enabled")
        return self

    def proof(self):
        return self.h.api("inspect", {"token": self.h.token("issuer"), "opid": self.h.op})

    def seed(self):
        self.expected = self.proof()["revision"]
        self.seed_args = {"token": self.h.token("requester"), "opid": self.h.op,
                          "cid": self.cid, "expected": self.expected}
        self.request = self.h.api("continuation_seed", self.seed_args)
        return self.request

    def seal(self, request=None):
        raw = canonical(request or self.request)
        return self.h.sql("SELECT seller_truth.paypal_continuation_v2(:raw,:sig)",
                          {"raw": raw.decode(), "sig": signature(FIXTURE_QUOTE_REQUEST, raw)}, "seller")

    def register(self, sealed=None):
        sealed = sealed or self.seal()
        self.register_args = {**self.seed_args, **sealed, "sig": sealed["signature"], "idem": "register_" + uuid4().hex}
        self.register_args.pop("signature")
        self.cont = self.h.api("continuation_register", self.register_args)
        return self.cont

    def approve(self, expires=None):
        self.approval_args = {"token": self.h.token("approver"), "opid": self.h.op,
                              "cid": self.cid, "digest": self.cont["digest"], "expected": 1,
                              "expires": expires or self.request["expires_at"], "idem": "approve_" + uuid4().hex}
        return self.h.api("continuation_approve", self.approval_args)

    def authorize(self):
        self.capture_args = {"token": self.h.token("issuer"), "opid": self.h.op, "cid": self.cid,
                             "expected": self.proof()["revision"], "idem": "capture_" + uuid4().hex}
        return self.h.api("continuation_capture_request", self.capture_args)

    def ready(self, expired=False, grant_expires=None):
        self.approved(expired, grant_expires)
        self.seed()
        self.register()
        self.approve()
        self.authorize()
        return self

    def commit_args(self):
        return {"secret": self.h.mutate, "opid": self.h.op, "cid": self.cid,
                "expected_digest": hashlib.sha256(b"{}").hexdigest()}

    def phases(self):
        return self.h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id AND phase='CAPTURE'",
                          {"id": self.h.op})

    def assert_held(self):
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])
        self.assertEqual(self.h.sql("SELECT count(*) FROM authority.reservations WHERE action_id=:id",
                                   {"id": self.h.action["action_id"]}), 1)

    def test_expired_original_evidence_new_approval_same_order_books_and_fulfills(self):
        self.ready(expired=True)
        h = self.h
        with self.assertRaisesRegex(BoundaryFailure, "QUOTE_EXPIRED"):
            h.driver.capture(h.op)  # Original validator is unchanged.
        original = h.sql("SELECT jsonb_build_array(create_request_id,capture_request_id,wire_digest) FROM paypal_v1.bindings WHERE operation_id=:id", {"id": h.op})
        history = h.sql("SELECT jsonb_build_array(o,q,a) FROM dispatch.business_operations o JOIN authority.evidence_revisions e ON e.id=o.evidence_id JOIN authority.seller_quotes q ON q.id=e.quote_id JOIN authority.approvals a ON a.id=o.approval_id WHERE o.id=:id", {"id": h.op})
        result = capture(h.driver, h.op, self.cid)
        self.assertTrue(result["capture_confirmed"])
        h.economic("process_receipt", {"rid": result["receipt_id"]})
        capability = h.economic("capability", {"opid": h.op})
        attestation = h.sql("SELECT seller_truth.paypal_finalize_v1(:raw,:signature)", capability, "seller")
        receipt = h.economic("receive", {"channel": "seller-" + h.tenant, "raw": attestation["raw"].encode(),
                            "headers": json.dumps({"signature": attestation["signature"]}), "delivery": "synthetic-v2-finalize"})
        h.economic("process_receipt", {"rid": str(receipt)})
        proof = self.proof()
        self.assertEqual({f["kind"] for f in proof["economic"]["facts"]},
                         {"CAPTURE", "ORDER_LIABILITY", "SELLER_FULFILLED", "REVENUE"})
        self.assertEqual(len(proof["economic"]["postings"]), 3)
        self.assertIsNotNone(proof["economic"]["seller_entitlement"])

        def seller(verb, body):
            return h.sql("SELECT seller_truth.reconciliation_read(:raw,:signature)", body, "seller")

        run = execute(h.api, seller, h.token("issuer"), RunRequest(request_id=uuid4(), batch_size=50))
        self.assertEqual(run.state, "COMPLETE")
        self.assertTrue(any(m.operation_id == h.op and m.state == "RECORDED" for m in run.members))
        self.assertEqual(h.sql("SELECT jsonb_build_array(create_request_id,capture_request_id,wire_digest) FROM paypal_v1.bindings WHERE operation_id=:id", {"id": h.op}), original)
        self.assertEqual(h.sql("SELECT jsonb_build_array(o,q,a) FROM dispatch.business_operations o JOIN authority.evidence_revisions e ON e.id=o.evidence_id JOIN authority.seller_quotes q ON q.id=e.quote_id JOIN authority.approvals a ON a.id=o.approval_id WHERE o.id=:id", {"id": h.op}), history)
        self.assertEqual(h.sql("SELECT request_id::text FROM paypal_v1.phase_commits WHERE operation_id=:id AND phase='CAPTURE'", {"id": h.op}), original[1])
        self.assertEqual((h.provider.creates, h.provider.captures), (1, 1))
        self.assert_held()

    def test_original_unexpired_path_cannot_bypass_continuation_revocation(self):
        self.approved()
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_ORIGINAL_QUOTE_NOT_EXPIRED"):
            self.seed()
        self.assertEqual(self.phases(), 0)

    def test_default_gate_disabled(self):
        self.approved(expired=True)
        self.h.sql("UPDATE paypal_v2.deployment SET enabled=false RETURNING enabled")
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_DISABLED"):
            self.seed()

    def test_seed_and_seller_retries_freeze_expiry_and_allocation(self):
        self.approved(expired=True)
        first = self.seed()
        self.assertEqual(self.h.api("continuation_seed", self.seed_args), first)
        sealed = self.seal()
        self.assertEqual(self.seal(), sealed)
        changed = {**self.seed_args, "expected": self.expected + 1}
        with self.assertRaisesRegex(BoundaryFailure, "IDEMPOTENCY_CONFLICT"):
            self.h.api("continuation_seed", changed)
        altered = {**self.request, "expires_at": deadline()}
        with self.assertRaises(DBAPIError):
            self.seal(altered)
        self.assert_held()

    def test_missing_independent_approval_denies_issuer_and_broker(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_APPROVAL_REQUIRED"):
            self.authorize()
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_APPROVAL_REQUIRED"):
            self.h.broker("continuation_commit", self.commit_args())
        self.assertEqual(self.phases(), 0)

    def test_missing_separate_issuer_authorization_denies_broker(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        self.approve()
        with self.assertRaisesRegex(BoundaryFailure, "SEPARATE_EXACT_CAPTURE_AUTHORIZATION_REQUIRED"):
            self.h.broker("continuation_commit", self.commit_args())
        self.assertEqual(self.phases(), 0)

    def test_same_command_recovery_and_two_sessions_never_add_approvers(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        original = self.approve()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.h.api("continuation_approve", self.approval_args), range(2)))
        self.assertEqual(results, [original, original])
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_ALREADY_APPROVED"):
            self.h.api("continuation_approve", {**self.approval_args, "idem": "second_" + uuid4().hex})
        self.assertEqual(self.h.sql("SELECT count(*) FROM paypal_v2.approvals WHERE continuation_id=CAST(:id AS uuid)", {"id": self.cid}), 1)

    def test_concurrent_new_approval_commands_only_one_commits(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        self.approval_args = {"token": self.h.token("approver"), "opid": self.h.op, "cid": self.cid,
                              "digest": self.cont["digest"], "expected": 1, "expires": self.request["expires_at"]}

        def attempt(_):
            try:
                self.h.api("continuation_approve", {**self.approval_args, "idem": "parallel_" + uuid4().hex})
                return "COMMITTED"
            except BoundaryFailure as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), ["COMMITTED", "CONTINUATION_ALREADY_APPROVED"])

    def test_self_approval_denied_even_when_requester_has_approver_role(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        # Direct fixture membership mutation, never a real user/role change.
        self.h.sql("UPDATE authority.memberships SET roles=roles||ARRAY['PURCHASE_APPROVER'] WHERE principal=:p RETURNING principal", {"p": self.h.people["requester"]})
        with self.assertRaisesRegex(BoundaryFailure, "SELF_APPROVAL_DENIED"):
            self.h.api("continuation_approve", {"token": self.h.token("requester"), "opid": self.h.op,
                       "cid": self.cid, "digest": self.cont["digest"], "expected": 1,
                       "expires": self.request["expires_at"], "idem": "self_" + uuid4().hex})

    def test_invalid_digest_or_overlong_approval_denied(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        args = {"token": self.h.token("approver"), "opid": self.h.op, "cid": self.cid,
                "digest": self.cont["digest"], "expected": 1, "expires": self.request["expires_at"], "idem": "invalid_" + uuid4().hex}
        for fields in ({"digest": "0" * 64}, {"expires": deadline()}, {"expected": 2}):
            with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_EXACT_APPROVAL_REQUIRED"):
                self.h.api("continuation_approve", {**args, **fields})

    def test_fresh_approval_expiry_blocks_send(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        self.approve((datetime.now(UTC) + timedelta(seconds=1)).isoformat())
        self.authorize()
        time.sleep(1.05)
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_APPROVAL_REQUIRED"):
            capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.h.provider.captures, 0)
        self.assert_held()

    def test_signature_and_every_exact_binding_mismatch_denied(self):
        self.approved(expired=True)
        self.seed()
        sealed = self.seal()
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_SELLER_SIGNATURE_INVALID"):
            self.register({**sealed, "signature": "0" * 64})
        att = json.loads(sealed["raw"])
        substitutions = {"operation_id": "op_wrong", "provider_order_id": "OTHERORDER123456",
                         "merchant_id": "OTHERSELLER12345", "currency": "INR", "amount_minor": 101,
                         "beneficiary": "mallory", "product_id": "another-product", "quantity": 2,
                         "reservation_id": "reservation_other", "capture_request_id": str(uuid4()),
                         "tenant_id": "other_tenant", "quote_digest": "0" * 64, "source_revision": 0}
        for field, value in substitutions.items():
            with self.subTest(field=field):
                raw = canonical({**att, field: value})
                with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_EXACT_BINDING_REQUIRED"):
                    self.register({"raw": raw.decode(), "signature": signature(FIXTURE_QUOTE, raw)})
        changed = copy.deepcopy(att)
        changed["quote"]["lines"][0]["allocation_id"] = "replacement-allocation"
        raw = canonical(changed)
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_EXACT_BINDING_REQUIRED"):
            self.register({"raw": raw.decode(), "signature": signature(FIXTURE_QUOTE, raw)})
        self.assertEqual(self.phases(), 0)

    def test_seller_rejects_forgery_allocation_change_expiry_and_wrong_beneficiary(self):
        self.approved(expired=True)
        self.seed()
        request = self.request
        for fields in ({"currency": "INR"}, {"merchant_id": "OTHERSELLER12345"},
                       {"beneficiary": "mallory"}, {"expires_at": "2000-01-01T00:00:00Z"},
                       {"expires_at": deadline()}):
            with self.assertRaises(DBAPIError):
                self.seal({**request, **fields})
        changed = copy.deepcopy(request)
        changed["quote"]["lines"][0]["allocation_id"] = "replacement-allocation"
        with self.assertRaises(DBAPIError):
            self.seal(changed)
        with self.assertRaises(DBAPIError):
            self.h.sql("SELECT seller_truth.paypal_continuation_v2(:raw,:sig)",
                       {"raw": canonical(request).decode(), "sig": "0" * 64}, "seller")

    def test_revoked_grant_and_changed_policy_block_existing_continuation(self):
        self.ready(expired=True)
        self.h.command("revoke-grant", {"grant_id": self.h.grant["grant_id"]}, 2, "issuer")
        with self.assertRaisesRegex(BoundaryFailure, "AUTHORITY_REVOKED"):
            capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.phases(), 0)
        self.assert_held()

    def test_current_policy_change_denied(self):
        self.ready(expired=True)
        self.h.command("publish-policy", {"kind": "PURCHASE", "approval_above": 0, "max_amount": 50}, 2, "issuer")
        with self.assertRaisesRegex(BoundaryFailure, "POLICY_CHANGED"):
            capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.phases(), 0)

    def role_revision_denied(self, role):
        self.ready(expired=True)
        self.h.sql("UPDATE authority.memberships SET revision=revision+1 WHERE principal=:p RETURNING principal", {"p": self.h.people[role]})
        with self.assertRaises(BoundaryFailure):
            capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.phases(), 0)

    def test_approver_revision_change_denied(self):
        self.role_revision_denied("approver")

    def test_requester_revision_change_denied(self):
        self.role_revision_denied("requester")

    def test_issuer_revision_change_denied(self):
        self.role_revision_denied("issuer")

    def test_existing_grant_expiry_denies_without_renewal_or_release(self):
        expiry = datetime.now(UTC) + timedelta(seconds=6)
        self.ready(expired=True, grant_expires=expiry.isoformat())
        time.sleep(max(0, expiry.timestamp() - time.time()) + .05)
        with self.assertRaisesRegex(BoundaryFailure, "AUTHORITY_EXPIRED"):
            capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.phases(), 0)
        self.assert_held()

    def test_continuation_revocation_preserves_hold_and_denies_capture(self):
        self.ready(expired=True)
        args = {"token": self.h.token("issuer"), "opid": self.h.op, "cid": self.cid}
        self.assertFalse(self.h.api("continuation_revoke", args)["reservation_released"])
        self.assertEqual(self.h.api("continuation_revoke", args), self.h.api("continuation_revoke", args))
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_REVOKED"):
            capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.phases(), 0)
        self.assert_held()

    def test_capture_command_recovery_then_concurrent_transport_only_one_send(self):
        self.ready(expired=True)
        first = self.h.api("continuation_capture_request", self.capture_args)
        self.assertEqual(self.h.api("continuation_capture_request", self.capture_args), first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: capture(self.h.driver, self.h.op, self.cid), range(2)))
        capture(self.h.driver, self.h.op, self.cid)
        self.assertEqual(self.phases(), 1)
        self.assertEqual((self.h.provider.creates, self.h.provider.captures), (1, 1))
        self.assert_held()

    def test_capture_unknown_then_read_only_recovery_never_replays(self):
        self.ready(expired=True)
        original = self.h.provider.capture_order

        def lost(order, request):
            self.h.provider.unavailable = True
            self.h.provider.lose_capture = True
            return original(order, request)

        self.h.provider.capture_order = lost
        self.assertEqual(capture(self.h.driver, self.h.op, self.cid)["knowledge"], "UNKNOWN")
        self.assertFalse(capture(self.h.driver, self.h.op, self.cid)["capture_sent"])
        self.assertEqual(self.h.provider.captures, 1)
        self.assert_held()
        self.h.provider.unavailable = False
        self.assertTrue(capture(self.h.driver, self.h.op, self.cid)["capture_confirmed"])
        self.assertEqual(self.h.provider.captures, 1)

    def test_lost_commit_response_never_sends_or_replays(self):
        self.ready(expired=True)
        self.assertTrue(self.h.broker("continuation_commit", self.commit_args())["send"])
        self.assertFalse(capture(self.h.driver, self.h.op, self.cid).get("capture_confirmed", False))
        self.assertEqual(self.h.provider.captures, 0)
        self.assertEqual(self.phases(), 1)
        self.assert_held()

    def test_unavailable_or_conflicting_canonical_read_cannot_send(self):
        self.ready(expired=True)
        self.h.provider.unavailable = True
        self.assertFalse(capture(self.h.driver, self.h.op, self.cid)["capture_sent"])
        self.h.provider.unavailable = False
        self.h.provider.override = lambda d: d["purchase_units"][0]["payee"].update(merchant_id="OTHERSELLER12345")
        self.assertTrue(capture(self.h.driver, self.h.op, self.cid)["blocked"])
        self.assertEqual(self.phases(), 0)
        self.assertEqual(self.h.provider.captures, 0)

    def test_null_or_stale_read_rejected_at_cutoff(self):
        self.ready(expired=True)
        # The installed identity guard also rejects deleting/backdating evidence.
        with self.assertRaises(DBAPIError):
            self.h.sql("UPDATE paypal_v1.runtime SET latest_read_at=NULL WHERE operation_id=:id RETURNING operation_id", {"id": self.h.op})
        read = self.h.sql("SELECT latest_read_at FROM paypal_v1.runtime WHERE operation_id=:id", {"id": self.h.op})
        time.sleep(max(0, read.timestamp()+60.1-time.time()))
        with self.assertRaisesRegex(BoundaryFailure, "SEPARATE_EXACT_CAPTURE_AUTHORIZATION_REQUIRED"):
            self.h.broker("continuation_commit", self.commit_args())
        self.assertEqual(self.phases(), 0)

    def test_http_chain_uses_actual_sql_seller_broker_and_independent_roles(self):
        self.approved(expired=True)
        h = self.h
        auth = "synthetic-internal-service-key-" + uuid4().hex
        with patch.dict("os.environ", {"PAYPAL_SELLER_DATABASE_URL": h.urls["seller"],
                                      "PAYPAL_CAPTURE_CONTINUATION_ENABLED": "true"}), TestClient(seller_factory()) as seller:
            def call_seller(verb, body):
                response = seller.post("/v1/paypal/seller/" + verb, json=body)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            broker = broker_app(h.driver, auth, call_seller, FIXTURE_QUOTE_REQUEST, continuations_enabled=True)
            with TestClient(broker) as isolated_broker:
                def call_broker(verb, body):
                    response = isolated_broker.post("/v1/internal/" + verb, json=body,
                                                     headers={"Authorization": "Bearer " + auth})
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()

                with TestClient(create_api(h.api, None, call_broker, continuations_enabled=True)) as api:
                    base = "/v2/paypal/capture-continuations"
                    operation = {"operation_id": h.op, "currency": "USD"}

                    def post(path, body, role, revision):
                        response = api.post(path, json=body, headers={"Authorization": "Bearer " + h.token(role),
                                            "Idempotency-Key": "http_" + uuid4().hex, "If-Match": str(revision)})
                        self.assertEqual(response.status_code, 200, response.text)
                        return response.json()

                    cont = post(base, {**operation, "continuation_id": self.cid}, "requester", self.proof()["revision"])
                    read = api.get(base + "/" + self.cid, headers={"Authorization": "Bearer " + h.token("approver")})
                    self.assertEqual(read.status_code, 200, read.text)
                    self.assertFalse(read.json()["financial_authority"])
                    post(base + "/" + self.cid + "/approvals", {**operation, "continuation_digest": cont["digest"],
                         "expires_at": read.json()["expires_at"]}, "approver", 1)
                    self.assertEqual(h.provider.captures, 0)
                    result = post(base + "/" + self.cid + "/capture-requests", operation, "issuer", self.proof()["revision"])
                    self.assertTrue(result["capture_confirmed"])
                    self.assertEqual((h.provider.creates, h.provider.captures), (1, 1))

    def test_foreign_continuation_or_tenant_cannot_authorize_existing_order(self):
        self.ready(expired=True)
        other = Harness().prepare()
        self.addCleanup(other.close)
        with self.assertRaises(BoundaryFailure):
            self.h.api("continuation_inspect", {"token": other.token("approver"), "cid": self.cid})
        with self.assertRaisesRegex(BoundaryFailure, "CONTINUATION_SCOPE_DENIED"):
            self.h.broker("continuation_commit", {**self.commit_args(), "cid": str(uuid4())})
        self.assertEqual(self.phases(), 0)

    def test_runtime_roles_cannot_read_keys_or_call_private_predicates(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        for service in ("api", "broker", "economic"):
            for statement in ("SELECT * FROM authority.issuer_keys", "SELECT * FROM paypal_v2.approvals",
                              "SELECT paypal_v2.scope(:op)", "SELECT paypal_v2.permission(CAST(:cid AS uuid),false)"):
                with self.assertRaises(DBAPIError):
                    self.h.sql(statement, {"op": self.h.op, "cid": self.cid}, service)
        with self.assertRaises(DBAPIError):
            self.h.sql("SELECT paypal_v2.commit_capture(:secret,:op,CAST(:cid AS uuid),:digest)",
                       {"secret": self.h.mutate, "op": self.h.op, "cid": self.cid, "digest": "0" * 64}, "api")
        with self.assertRaises(DBAPIError):
            self.h.sql("SELECT seller_truth.paypal_continuation_v2('{}','invalid')", service="api")

    def test_expired_history_new_records_and_seller_attestation_are_immutable(self):
        self.ready(expired=True)
        for statement, params, service in (
            ("UPDATE paypal_v2.approvals SET expires_at=clock_timestamp()+interval '1 hour' WHERE continuation_id=CAST(:cid AS uuid)", {"cid": self.cid}, "control_owner"),
            ("DELETE FROM paypal_v2.continuations WHERE id=CAST(:cid AS uuid)", {"cid": self.cid}, "control_owner"),
            ("UPDATE seller_truth.paypal_continuations SET request_raw='{}' WHERE id=CAST(:cid AS uuid)", {"cid": self.cid}, "seller_owner"),
        ):
            with self.assertRaises(DBAPIError):
                self.h.sql(statement, params, service)
        self.assert_held()

    def race(self, revoke_first):
        h, pids = self.h, Queue()
        revoke = ("api", text("SELECT paypal_v1.command(:token,'revoke-grant',CAST(:body AS jsonb),2,:idem)"),
                  {"token": h.token("issuer"), "body": json.dumps({"grant_id": h.grant["grant_id"]}), "idem": "race_" + uuid4().hex})
        commit = ("broker", text("SELECT paypal_v2.commit_capture(:secret,:opid,CAST(:cid AS uuid),:expected_digest)"), self.commit_args())
        first, second = (revoke, commit) if revoke_first else (commit, revoke)

        def contender():
            service, sql, params = second
            try:
                with h.engines[service].begin() as db:
                    db.execute(text("SET LOCAL statement_timeout='15s'"))
                    pids.put(db.scalar(text("SELECT pg_backend_pid()")))
                    return {"result": db.scalar(sql, params)}
            except DBAPIError as error:
                return {"code": error.orig.diag.message_primary}

        with ThreadPoolExecutor(max_workers=1) as pool:
            service, sql, params = first
            with h.engines[service].connect() as db:
                tx = db.begin()
                try:
                    db.execute(text("SET LOCAL statement_timeout='15s'"))
                    first_pid = db.scalar(text("SELECT pg_backend_pid()"))
                    first_result = db.scalar(sql, params)
                    future = pool.submit(contender)
                    second_pid = pids.get(timeout=5)
                    until = time.monotonic() + 10
                    while not h.sql("SELECT :first=ANY(pg_blocking_pids(:second))", {"first": first_pid, "second": second_pid}):
                        if future.done() or time.monotonic() > until:
                            self.fail("ACTUAL_POSTGRES_LOCK_WAIT_NOT_OBSERVED")
                        time.sleep(.02)
                    tx.commit()
                finally:
                    if tx.is_active:
                        tx.rollback()
            return first_result, future.result(timeout=20)

    def test_revocation_wins_actual_postgres_lock_wait(self):
        self.ready(expired=True)
        _, second = self.race(True)
        self.assertEqual(second, {"code": "AUTHORITY_REVOKED"})
        self.assertEqual(self.phases(), 0)
        self.assert_held()

    def test_capture_cutoff_wins_revocation_retains_obligation(self):
        self.ready(expired=True)
        first, second = self.race(False)
        self.assertTrue(first["send"])
        self.assertIn("result", second)
        self.assertEqual(self.phases(), 1)
        self.assertEqual(self.h.provider.captures, 0)
        self.assert_held()

    def test_expiry_during_actual_runtime_lock_wait_denies_capture(self):
        self.approved(expired=True)
        self.seed()
        self.register()
        expiry = datetime.now(UTC) + timedelta(seconds=2)
        self.approve(expiry.isoformat())
        self.authorize()
        pids = Queue()

        def contender():
            try:
                with self.h.engines["broker"].begin() as db:
                    db.execute(text("SET LOCAL statement_timeout='10s'"))
                    pids.put(db.scalar(text("SELECT pg_backend_pid()")))
                    return db.scalar(text("SELECT paypal_v2.commit_capture(:secret,:opid,CAST(:cid AS uuid),:expected_digest)"), self.commit_args())
            except DBAPIError as error:
                return error.orig.diag.message_primary

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.h.engines["control_owner"].connect() as db:
                tx = db.begin()
                try:
                    db.execute(text("SELECT 1 FROM paypal_v1.runtime WHERE operation_id=:id FOR UPDATE"), {"id": self.h.op})
                    blocker = db.scalar(text("SELECT pg_backend_pid()"))
                    future = pool.submit(contender)
                    pid = pids.get(timeout=5)
                    until = time.monotonic() + 5
                    while not self.h.sql("SELECT :blocker=ANY(pg_blocking_pids(:pid))", {"blocker": blocker, "pid": pid}):
                        if future.done() or time.monotonic() > until:
                            self.fail("EXPIRY_TEST_DID_NOT_OBSERVE_ACTUAL_LOCK_WAIT")
                        time.sleep(.02)
                    time.sleep(max(0, expiry.timestamp() - time.time()) + .05)
                    tx.commit()
                finally:
                    if tx.is_active:
                        tx.rollback()
            self.assertEqual(future.result(timeout=10), "CONTINUATION_APPROVAL_REQUIRED")
        self.assertEqual(self.phases(), 0)
        self.assert_held()

    def test_concurrent_v1_and_v2_commit_only_fresh_path_can_win(self):
        self.ready(expired=True)

        def attempt(version):
            try:
                if version == 1:
                    return self.h.broker("commit_phase", {"secret": self.h.mutate, "opid": self.h.op,
                                         "phase": "CAPTURE", "expected_digest": hashlib.sha256(b"{}").hexdigest()})
                return self.h.broker("continuation_commit", self.commit_args())
            except BoundaryFailure as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            original, continuation = list(pool.map(attempt, [1, 2]))
        self.assertEqual(original, "QUOTE_EXPIRED")
        self.assertTrue(continuation["send"])
        self.assertEqual(self.phases(), 1)
        self.assertEqual(self.h.provider.captures, 0)
        self.assert_held()


if __name__ == "__main__":
    unittest.main()
