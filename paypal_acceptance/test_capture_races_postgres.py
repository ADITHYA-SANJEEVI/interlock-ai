"""Actual PG wait edges for the USD authority cutoff; no external provider I/O."""

import hashlib
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import Harness


class CaptureCutoffRaces(unittest.TestCase):
    def setUp(self):
        self.h = Harness().prepare().ready_capture()
        self.addCleanup(self.h.close)

    def statement(self, kind):
        h = self.h
        if kind == "revoke":
            return ("api", text("SELECT paypal_v1.command(:token,'revoke-grant',CAST(:body AS jsonb),2,:idem)"),
                    {"token": h.token("issuer"), "body": json.dumps({"grant_id": h.grant["grant_id"]}),
                     "idem": "race_revoke_" + uuid4().hex})
        return ("broker", text("SELECT paypal_v1.commit_phase(:secret,:opid,'CAPTURE',:digest)"),
                {"secret": h.mutate, "opid": h.op, "digest": hashlib.sha256(b"{}").hexdigest()})

    def race(self, first_kind):
        h, pids = self.h, Queue[int]()
        second_kind = "capture" if first_kind == "revoke" else "revoke"

        def contender():
            service, statement, params = self.statement(second_kind)
            try:
                with h.engines[service].begin() as db:
                    db.execute(text("SET LOCAL statement_timeout='15s'"))
                    pids.put(db.scalar(text("SELECT pg_backend_pid()")))
                    return {"result": db.scalar(statement, params)}
            except DBAPIError as error:
                return {"code": getattr(getattr(error.orig, "diag", None), "message_primary", "")}

        service, statement, params = self.statement(first_kind)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with h.engines[service].connect() as db:
                tx = db.begin()
                try:
                    db.execute(text("SET LOCAL statement_timeout='15s'"))
                    first_pid = db.scalar(text("SELECT pg_backend_pid()"))
                    first_result = db.scalar(statement, params)
                    future = pool.submit(contender)
                    second_pid = pids.get(timeout=5)
                    self.assertNotEqual(first_pid, second_pid)
                    until = time.monotonic() + 10
                    while time.monotonic() < until:
                        waiting = h.sql("SELECT :first=ANY(pg_blocking_pids(:second))",
                                        {"first": first_pid, "second": second_pid})
                        if waiting:
                            break
                        if future.done():
                            self.fail("CONTENDER_DID_NOT_WAIT_FOR_AUTHORITY_CUTOFF")
                        time.sleep(0.03)
                    else:
                        self.fail("ACTUAL_POSTGRES_BLOCKING_EDGE_NOT_OBSERVED")
                    tx.commit()
                finally:
                    if tx.is_active:
                        tx.rollback()
            return first_result, future.result(timeout=20)

    def test_revocation_wins_before_capture_commit(self):
        _, second = self.race("revoke")
        self.assertEqual(second, {"code": "AUTHORITY_REVOKED"})
        self.assertEqual(self.h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id AND phase='CAPTURE'",
                                   {"id": self.h.op}), 0)
        self.assertEqual(self.h.provider.captures, 0)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])

    def test_capture_commit_wins_then_revocation_retains_unknown_obligation(self):
        first, second = self.race("capture")
        self.assertTrue(first["send"])
        self.assertIn("result", second)
        self.assertEqual(self.h.sql("SELECT count(*) FROM paypal_v1.phase_commits WHERE operation_id=:id AND phase='CAPTURE'",
                                   {"id": self.h.op}), 1)
        self.assertEqual(self.h.sql("SELECT count(*) FROM dispatch.recovery_obligations WHERE operation_id=:id AND origin_kind='OPERATION'",
                                   {"id": self.h.op}), 1)
        # Stop at the durable handoff. No simulated or real capture is sent here.
        self.assertEqual(self.h.provider.captures, 0)
        self.assertEqual(self.h.vector(), [500, 0, 100, 400])
        proof = self.h.api("inspect", {"token": self.h.token("issuer"), "opid": self.h.op})
        self.assertFalse(proof["capture_confirmed"])


if __name__ == "__main__":
    unittest.main()
