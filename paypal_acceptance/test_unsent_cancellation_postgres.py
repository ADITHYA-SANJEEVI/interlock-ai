"""Isolated actual PostgreSQL recovery acceptance; all financial identities/PSP are synthetic."""

import hashlib
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from interlock.contracts import canonical, signature
from interlock.paypal_flow import BoundaryFailure
from interlock.paypal_services import create_api
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import FIXTURE_QUOTE_REQUEST, Harness, deadline


class UnsentCancellationAcceptance(unittest.TestCase):
    def setUp(self):
        self.h = Harness().prepare()
        self.addCleanup(self.h.close)

    def cancel(self, key=None, role="issuer", expected=1, dispatch_expected=1, op=None):
        return self.h.api("cancel_unsent", {"token": self.h.token(role), "opid": op or self.h.op,
            "expected": expected, "dispatch_expected": dispatch_expected, "idem": key or "cancel_" + uuid4().hex})

    def fresh(self, approve=True):
        h = self.h
        raw = canonical({"schema": "paypal-quote-request-v1", "tenant_id": h.tenant,
            "request_id": "replacement_" + uuid4().hex, "product_id": "workspace-seat", "beneficiary": "alice",
            "quantity": 1, "currency": "USD", "expires_at": deadline()})
        sealed = h.sql("SELECT seller_truth.paypal_quote_v1(:raw,:sig)",
            {"raw": raw.decode(), "sig": signature(FIXTURE_QUOTE_REQUEST, raw)}, "seller")
        q = h.api("quote", {"token": h.token("requester"), **sealed, "idem": "quote_" + uuid4().hex})
        pid = h.sql("SELECT id::text FROM paypal_v1.proposals WHERE tenant_id=:id", {"id": h.tenant})
        h.api("link_quote", {"token": h.token("requester"), "pid": pid, "quoteid": q["id"]})
        a = h.command("propose", {"grant_id": h.grant["grant_id"], "quote_id": q["id"]})
        exact = {k: a[k] for k in ("action_id", "evidence_id", "decision_id")}
        if approve:
            approval = h.command("approve", {**exact, "expires_at": deadline()}, 1, "approver")
            exact["approval_id"] = approval["approval_id"]
        return exact

    def replace(self, exact, key=None, role="requester"):
        return self.h.api("admit_replacement", {"token": self.h.token(role), "parentid": self.h.op,
            "body": json.dumps(exact), "expected": 1, "idem": key or "replacement_" + uuid4().hex})

    def test_cancel_releases_exact_hold_and_preserves_immutable_authority(self):
        h = self.h
        before = h.sql("SELECT to_jsonb(b) FROM paypal_v1.bindings b WHERE operation_id=:id", {"id": h.op})
        result = self.cancel()
        self.assertEqual(result["status"], "CANCELLED_NEVER_SENT")
        self.assertEqual(result["budget_at_cancellation"], [500, 0, 0, 500])
        self.assertEqual(h.vector(), [500, 0, 0, 500])
        self.assertFalse(result["settlement_proven"])
        self.assertFalse(result["provider_mutation_performed"])
        self.assertEqual(h.sql("SELECT to_jsonb(b) FROM paypal_v1.bindings b WHERE operation_id=:id", {"id": h.op}), before)
        self.assertEqual(h.sql("SELECT status FROM authority.approval_heads WHERE approval_id=:id", {"id": h.exact["approval_id"]}), "CONSUMED")
        self.assertEqual(h.sql("SELECT state FROM dispatch.operation_runtime WHERE operation_id=:id", {"id": h.op}), "CANCEL_UNSENT")
        self.assertEqual(h.sql("SELECT count(*) FROM economic.facts WHERE operation_id=:id", {"id": h.op}), 0)
        with self.assertRaises(BoundaryFailure):
            h.driver.create(h.op)
        self.assertEqual((h.provider.creates, h.provider.captures), (0, 0))

    def test_same_identity_retry_recovers_and_new_key_cannot_release_twice(self):
        key = "cancel_" + uuid4().hex
        first = self.cancel(key)
        self.assertEqual(self.cancel(key), first)
        with self.assertRaisesRegex(BoundaryFailure, "IDEMPOTENCY_CONFLICT"):
            self.cancel(key, expected=2)
        with self.assertRaisesRegex(BoundaryFailure, "CANCELLATION_ALREADY_RECORDED"):
            self.cancel()
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        self.assertEqual(self.h.sql("SELECT count(*) FROM authority.authority_movements WHERE reservation_id=:id AND kind='UNSENT_RELEASE'",
            {"id": first["reservation_id"]}), 1)

    def test_requester_approver_wrong_revision_and_other_tenant_cannot_cancel(self):
        for role in ("requester", "approver"):
            with self.subTest(role=role), self.assertRaises(BoundaryFailure):
                self.cancel(role=role)
        for expected, dispatch in ((2, 1), (1, 2)):
            with self.assertRaisesRegex(BoundaryFailure, "REVISION_CONFLICT"):
                self.cancel(expected=expected, dispatch_expected=dispatch)
        other = Harness().prepare()
        self.addCleanup(other.close)
        with self.assertRaises(BoundaryFailure):
            self.cancel(op=other.op)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])

    def test_unknown_create_commit_cannot_be_cancelled_or_released(self):
        h = self.h
        h.provider.lose_create = True
        h.driver.create(h.op)
        revision = h.sql("SELECT revision FROM dispatch.operation_runtime WHERE operation_id=:id", {"id": h.op})
        provider_revision = h.sql("SELECT revision FROM paypal_v1.runtime WHERE operation_id=:id", {"id": h.op})
        with self.assertRaisesRegex(BoundaryFailure, "NEVER_SENT_PROOF_UNAVAILABLE"):
            self.cancel(expected=provider_revision, dispatch_expected=revision)
        self.assertEqual(h.vector(), [500, 0, 100, 400])
        self.assertEqual(h.provider.creates, 1)

    def test_exposed_order_and_capture_commit_cannot_be_cancelled(self):
        h = self.h.ready_capture()
        revision = h.sql("SELECT revision FROM paypal_v1.runtime WHERE operation_id=:id", {"id": h.op})
        dispatch = h.sql("SELECT revision FROM dispatch.operation_runtime WHERE operation_id=:id", {"id": h.op})
        with self.assertRaisesRegex(BoundaryFailure, "NEVER_SENT_PROOF_UNAVAILABLE"):
            self.cancel(expected=revision, dispatch_expected=dispatch)
        h.driver.capture(h.op)
        with self.assertRaises(BoundaryFailure):
            self.cancel(expected=revision, dispatch_expected=dispatch)
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_concurrent_cancellation_releases_only_once(self):
        barrier = threading.Barrier(2)
        def contender():
            barrier.wait(timeout=5)
            try:
                return self.cancel()
            except BoundaryFailure:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: contender(), range(2)))
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])

    def test_concurrent_same_identity_recovers_one_immutable_result(self):
        key = "cancel_" + uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.cancel(key), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])

    def test_expired_terms_can_cancel_without_reviving_spending_authority(self):
        h = Harness()
        self.addCleanup(h.close)
        expires = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
        with patch("paypal_acceptance.harness.deadline", return_value=expires):
            h.prepare()
        self.h = h
        time.sleep(3.05)
        with self.assertRaises(BoundaryFailure):
            h.driver.create(h.op)
        self.cancel()
        self.assertEqual(h.vector(), [500, 0, 0, 500])
        self.assertEqual(h.sql("SELECT expires_at<clock_timestamp() FROM authority.grant_versions WHERE grant_id=:id",
                               {"id": h.grant["grant_id"]}), True)
        self.assertEqual(h.sql("SELECT status FROM authority.approval_heads WHERE approval_id=:id",
                               {"id": h.exact["approval_id"]}), "CONSUMED")
        with self.assertRaises(BoundaryFailure):
            h.command("admit", h.exact, 1)
        self.assertEqual((h.provider.creates, h.provider.captures), (0, 0))

    def test_revoked_grant_cancellation_restores_free_without_reactivation(self):
        h = self.h
        h.command("revoke-grant", {"grant_id": h.grant["grant_id"]}, 2, "issuer")
        self.cancel()
        self.assertEqual(h.sql("SELECT status FROM authority.grant_heads WHERE grant_id=:id",
                               {"id": h.grant["grant_id"]}), "REVOKED")
        with self.assertRaises(BoundaryFailure):
            h.driver.create(h.op)
        self.assertEqual(h.vector(), [500, 0, 0, 500])

    def test_terminal_state_and_proof_cannot_be_reopened_or_rewritten(self):
        h = self.h
        self.cancel()
        for sql in ("UPDATE paypal_v1.runtime SET blocked=false WHERE operation_id=:id",
                    "UPDATE dispatch.operation_runtime SET state='READY' WHERE operation_id=:id",
                    "UPDATE dispatch.outbox_items SET completed=false WHERE operation_id=:id",
                    "UPDATE paypal_v1.cancellations SET result='{}' WHERE operation_id=:id",
                    "DELETE FROM paypal_v1.cancellations WHERE operation_id=:id"):
            with self.subTest(sql=sql), self.assertRaises(DBAPIError):
                h.sql(sql + " RETURNING operation_id", {"id": h.op})
        self.assertEqual(h.vector(), [500, 0, 0, 500])

    def claim_statement(self):
        h = self.h
        secret = "synthetic_claim_" + uuid4().hex
        credential = "claim_" + uuid4().hex
        with h.engines["control_owner"].begin() as db:
            db.execute(text("""INSERT INTO dispatch.service_credentials(id,token_hash,expires_at)
                VALUES(:id,:hash,clock_timestamp()+interval '1 hour')"""),
                {"id": credential, "hash": hashlib.sha256(secret.encode()).hexdigest()})
            db.execute(text("INSERT INTO dispatch.service_scopes VALUES(:id,:tenant,ARRAY['CLAIM'])"),
                {"id": credential, "tenant": h.tenant})
        return "worker", text("SELECT dispatch.claim(:secret,30,:opid)"), {"secret": secret, "opid": h.op}

    def cancel_statement(self, expected=1, dispatch_expected=1):
        return "api", text("SELECT paypal_v1.cancel_never_sent(:token,:opid,:expected,:dispatch,:idem)"), {
            "token": self.h.token("issuer"), "opid": self.h.op, "expected": expected,
            "dispatch": dispatch_expected, "idem": "race_cancel_" + uuid4().hex}

    def phase_statement(self, phase="CREATE"):
        digest = (self.h.sql("SELECT wire_digest FROM paypal_v1.bindings WHERE operation_id=:id", {"id": self.h.op})
                  if phase == "CREATE" else hashlib.sha256(b"{}").hexdigest())
        return "broker", text("SELECT paypal_v1.commit_phase(:secret,:opid,:phase,:digest)"), {
            "secret": self.h.mutate, "opid": self.h.op, "phase": phase, "digest": digest}

    def blocking_race(self, first, second):
        h, pids = self.h, Queue()
        def contender():
            role, sql, args = second
            try:
                with h.engines[role].begin() as db:
                    db.execute(text("SET LOCAL statement_timeout='15s'"))
                    pids.put(db.scalar(text("SELECT pg_backend_pid()")))
                    return {"result": db.scalar(sql, args)}
            except DBAPIError as error:
                return {"code": error.orig.diag.message_primary}
        role, sql, args = first
        with ThreadPoolExecutor(max_workers=1) as pool, h.engines[role].connect() as db:
            tx = db.begin()
            try:
                db.execute(text("SET LOCAL statement_timeout='15s'"))
                first_pid = db.scalar(text("SELECT pg_backend_pid()"))
                result = db.scalar(sql, args)
                future = pool.submit(contender)
                second_pid = pids.get(timeout=5)
                until = time.monotonic() + 10
                while not h.sql("SELECT :first=ANY(pg_blocking_pids(:second))",
                                {"first": first_pid, "second": second_pid}):
                    if future.done() or time.monotonic() > until:
                        self.fail("ACTUAL_POSTGRES_BLOCKING_EDGE_REQUIRED")
                    time.sleep(0.03)
                tx.commit()
            finally:
                if tx.is_active:
                    tx.rollback()
            return result, future.result(timeout=20)

    def test_cancel_wins_actual_postgres_create_handoff_race(self):
        _, second = self.blocking_race(self.cancel_statement(), self.phase_statement())
        self.assertIn("code", second)
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])
        self.assertEqual(self.h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id", {"id": self.h.op}), 0)

    def test_create_handoff_wins_actual_postgres_cancellation_race(self):
        first, second = self.blocking_race(self.phase_statement(), self.cancel_statement(dispatch_expected=2))
        self.assertTrue(first["send"])
        self.assertEqual(second, {"code": "NEVER_SENT_PROOF_UNAVAILABLE"})
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])
        self.assertEqual(self.h.provider.creates, 0)  # Durable commit only; no PSP call.

    def test_active_claim_wins_actual_postgres_cancellation_race(self):
        first, second = self.blocking_race(self.claim_statement(), self.cancel_statement())
        self.assertEqual(first["operation_id"], self.h.op)
        self.assertEqual(second, {"code": "ACTIVE_CLAIM_PREVENTS_CANCELLATION"})
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])

    def test_cancel_wins_claim_skip_locked_and_terminal_queue(self):
        h = self.h
        role, claim, args = self.claim_statement()
        _, sql, cancel_args = self.cancel_statement()
        with h.engines["api"].begin() as db:
            db.scalar(sql, cancel_args)
            self.assertIsNone(h.sql(str(claim), args, role))
        self.assertIsNone(h.sql(str(claim), args, role))
        self.assertEqual(h.vector(), [500, 0, 0, 500])

    def test_capture_commit_wins_actual_postgres_cancellation_race(self):
        h = self.h.ready_capture()
        rev = h.sql("SELECT revision FROM paypal_v1.runtime WHERE operation_id=:id", {"id": h.op})
        dispatch = h.sql("SELECT revision FROM dispatch.operation_runtime WHERE operation_id=:id", {"id": h.op})
        first, second = self.blocking_race(self.phase_statement("CAPTURE"), self.cancel_statement(rev, dispatch + 1))
        self.assertTrue(first["send"])
        self.assertEqual(second, {"code": "NEVER_SENT_PROOF_UNAVAILABLE"})
        self.assertEqual(h.vector(), [500, 0, 100, 400])
        self.assertEqual(h.provider.captures, 0)

    def test_partial_release_or_missing_proof_cannot_commit(self):
        h = self.h
        reservation = h.admission["reservation_id"]
        with self.assertRaises(DBAPIError):
            h.sql("UPDATE authority.reservation_heads SET status='CANCELLED_UNSENT' WHERE reservation_id=:id RETURNING status", {"id": reservation})
        with self.assertRaises(DBAPIError), h.engines["control_owner"].begin() as db:
            db.execute(text("UPDATE authority.grant_heads SET reserved=reserved-100,free=free+100 WHERE grant_id=:id"), {"id": h.grant["grant_id"]})
            db.execute(text("""INSERT INTO authority.authority_movements(id,tenant_id,grant_id,grant_version_id,epoch,reservation_id,request_id,
                kind,amount,delta_consumed,delta_reserved,delta_free,reason)
                SELECT :id,tenant_id,grant_id,grant_version_id,epoch,id,request_id,'UNSENT_RELEASE',amount,0,-amount,amount,'INVALID_PARTIAL_FIXTURE'
                FROM authority.reservations WHERE id=:rid"""), {"id": "partial_" + uuid4().hex, "rid": reservation})
        self.assertEqual(h.vector(), [500, 0, 100, 400])

    def test_api_and_broker_have_no_private_table_or_transport_helper_privilege(self):
        for service in ("api", "broker", "economic"):
            with self.subTest(service=service), self.assertRaises(DBAPIError):
                self.h.sql("SELECT * FROM paypal_v1.cancellations", service=service)
            with self.assertRaises(DBAPIError):
                self.h.sql("SELECT paypal_v1.cancel_transport_unsent(:id,1,1)", {"id": self.h.op}, service)

    def test_one_linked_fresh_admission_requires_fresh_independent_approval(self):
        h = self.h
        self.cancel()
        exact = self.fresh()
        key = "replacement_" + uuid4().hex
        result = self.replace(exact, key)
        self.assertNotEqual(result["operation_id"], h.op)
        self.assertEqual(result["cancelled_operation_id"], h.op)
        self.assertEqual(self.replace(exact, key), result)
        self.assertEqual(h.vector(), [500, 0, 100, 400])
        self.assertEqual(h.sql("SELECT count(*) FROM paypal_v1.replacements WHERE cancelled_operation_id=:id", {"id": h.op}), 1)
        with self.assertRaisesRegex(BoundaryFailure, "REPLACEMENT_ALREADY_ADMITTED"):
            self.replace(self.fresh())
        with self.assertRaises(BoundaryFailure):
            h.command("admit", h.exact, 1)
        self.assertEqual((h.provider.creates, h.provider.captures), (0, 0))

    def test_replacement_rejects_uncancelled_parent_and_old_or_missing_approval(self):
        exact = self.fresh()
        with self.assertRaisesRegex(BoundaryFailure, "CANCELLED_PARENT_REQUIRED"):
            self.replace(exact)
        self.cancel()
        with self.assertRaisesRegex(BoundaryFailure, "REPLACEMENT_EXACT_TERMS_REQUIRED"):
            self.replace({**exact, "approval_id": self.h.exact["approval_id"]})
        with self.assertRaises(BoundaryFailure):
            self.replace(self.fresh(approve=False))
        self.assertEqual(self.h.vector(), [500, 0, 0, 500])

    def test_concurrent_replacements_admit_at_most_one_fresh_operation(self):
        self.cancel()
        exacts = [self.fresh(), self.fresh()]
        def contender(exact):
            try:
                return self.replace(exact)
            except BoundaryFailure:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(contender, exacts))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])

    def test_replacement_wrong_role_and_changed_idempotent_body_are_denied(self):
        self.cancel()
        exact = self.fresh()
        for role in ("issuer", "approver"):
            with self.subTest(role=role), self.assertRaises(BoundaryFailure):
                self.replace(exact, role=role)
        key = "replacement_" + uuid4().hex
        self.replace(exact, key)
        with self.assertRaisesRegex(BoundaryFailure, "IDEMPOTENCY_CONFLICT"):
            self.replace({**exact, "decision_id": "wrong_decision"}, key)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])

    def test_replacement_rolls_back_ambiguous_exact_approvals(self):
        h = self.h
        self.cancel()
        exact = self.fresh()
        h.sql("SELECT paypal_v1.command(:token,'approve',CAST(:body AS jsonb),1,:idem)", {
            "token": h.token("approver"), "body": json.dumps({k: v for k, v in
                {**exact, "expires_at": deadline()}.items() if k != "approval_id"}),
            "idem": "duplicate_" + uuid4().hex}, "api")
        with self.assertRaisesRegex(BoundaryFailure, "EXACT_APPROVAL_AMBIGUOUS"):
            self.replace(exact)
        self.assertEqual(h.vector(), [500, 0, 0, 500])
        self.assertEqual(h.sql("SELECT count(*) FROM dispatch.business_operations WHERE action_id=:id",
                               {"id": exact["action_id"]}), 0)
        self.assertEqual(h.sql("SELECT status FROM authority.approval_heads WHERE approval_id=:id",
                               {"id": exact["approval_id"]}), "AVAILABLE")

    def test_changed_shared_checks_preserve_legacy_movement_truth_table(self):
        # Evaluate actual installed CHECK expressions, including the unchanged
        # refund branch. No legacy database, credentials or financial row changes.
        with self.h.engines["control_owner"].connect() as db:
            checks = list(db.scalars(text("""SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint
                WHERE conrelid='authority.authority_movements'::regclass AND contype='c'""")))
            for kind, dc, dr, df in (("RESERVE", 0, 100, -100), ("FIXTURE_CONSUME", 100, -100, 0),
                                      ("FIXTURE_RELEASE", 0, -100, 100), ("REFUND_PROCESSED", 100, -100, 0)):
                params = {"kind": kind, "dc": dc, "dr": dr, "df": df}
                def valid(args):
                    return all(db.scalar(text("SELECT " + check + " FROM (SELECT CAST(:kind AS text) AS kind,"
                        "100::bigint AS amount, CAST(:dc AS bigint) AS delta_consumed, CAST(:dr AS bigint) AS delta_reserved,"
                        "CAST(:df AS bigint) AS delta_free) installed_check_input"), args) for check in checks)
                with self.subTest(kind=kind):
                    self.assertTrue(valid(params))
                    self.assertFalse(valid({**params, "dr": 0}))

    def test_http_recovery_contract_never_calls_broker(self):
        def forbidden(*_):
            raise AssertionError("RECOVERY_MUST_NOT_CONTACT_BROKER_OR_MODEL")
        app = create_api(self.h.api, object(), forbidden)
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + self.h.token("issuer"), "Idempotency-Key": "http_" + uuid4().hex, "If-Match": "1"}
            response = client.post("/v1/paypal/cancellations", headers=headers,
                json={"operation_id": self.h.op, "currency": "USD", "dispatch_revision": 1})
            self.assertEqual(response.status_code, 200, response.text)
            proof = client.get("/v1/paypal/evidence/" + self.h.op, headers=headers)
            self.assertEqual(proof.status_code, 200, proof.text)
            self.assertEqual(proof.json()["settlement"], "CANCELLED_NEVER_SENT")
            self.assertFalse(proof.json()["capture_confirmed"])
            self.assertEqual(proof.json()["cancellation"]["cancellation_id"], response.json()["cancellation_id"])


if __name__ == "__main__":
    unittest.main()
