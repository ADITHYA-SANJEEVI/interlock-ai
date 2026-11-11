"""Offline protocol fixtures only; no model, provider, database or private profile."""

import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from interlock.paypal_continuation import ContinuationApprove, ContinuationPrepare, capture
from interlock.paypal_flow import BoundaryFailure, BrokerIdentity, ProtectedPayPal
from interlock.paypal_services import broker_app, create_api, local_caller, seller_factory
from pydantic import ValidationError

from hackathon_tests.test_paypal_flow import MERCHANT, ORDER, FakeKernel, FakeProvider


class ContinuationOffline(unittest.TestCase):
    def setUp(self):
        self.cid = str(uuid4())
        self.operation = {"operation_id": "op_fixture", "currency": "USD"}
        self.headers = {"Authorization": "Bearer fixture." + "a" * 64,
                        "Idempotency-Key": "fixture-command", "If-Match": "7"}

    def test_dto_requires_explicit_usd_and_rejects_authority_or_destinations(self):
        good = {**self.operation, "continuation_id": self.cid}
        self.assertEqual(str(ContinuationPrepare.model_validate(good).continuation_id), self.cid)
        for changed in ({"currency": "INR"}, {"merchant_id": "arbitrary"},
                        {"amount_minor": 1}, {"approve": True}, {"capture": True}):
            with self.assertRaises(ValidationError):
                ContinuationPrepare.model_validate({**good, **changed})
        with self.assertRaises(ValidationError):
            ContinuationPrepare.model_validate({"operation_id": "op_fixture", "continuation_id": self.cid})

    def test_approval_requires_exact_digest_and_aware_deadline(self):
        args = {**self.operation, "continuation_digest": "a" * 64,
                "expires_at": datetime.now(UTC) + timedelta(minutes=5)}
        ContinuationApprove.model_validate(args)
        ContinuationApprove.model_validate({**args, "expires_at": args["expires_at"].isoformat()})
        for changed in ({"continuation_digest": "invalid"}, {"expires_at": datetime.now()}):
            with self.assertRaises(ValidationError):
                ContinuationApprove.model_validate({**args, **changed})

    def test_default_api_and_broker_expose_no_continuation_routes(self):
        def unavailable(*args):
            self.fail("Disabled continuation must do no work")

        with TestClient(create_api(unavailable, None, unavailable)) as client:
            self.assertEqual(client.post("/v2/paypal/capture-continuations", json={}).status_code, 404)
        app = broker_app(None, "x" * 32, unavailable, "q" * 32)
        with TestClient(app) as client:
            self.assertEqual(client.post("/v1/internal/continuation-capture", json={}).status_code, 404)

    def test_seller_route_is_opt_in_and_never_loads_provider_profile(self):
        values = {"PAYPAL_SELLER_DATABASE_URL": "postgresql+psycopg://interlock_seller:synthetic@127.0.0.1:55438/unused"}
        with patch.dict("os.environ", values, clear=True), TestClient(seller_factory()) as client:
            self.assertEqual(client.post("/v1/paypal/seller/continuation-attestation", json={}).status_code, 404)

    def test_preparation_authenticates_and_freezes_seed_before_seller_io(self):
        calls = []

        def kernel(name, args):
            calls.append(name)
            return {"schema": "SYNTHETIC_FROZEN_SEED", "continuation_id": self.cid} if name == "continuation_seed" else {"recorded": True}

        def broker(verb, body):
            calls.append(verb)
            return {"raw": json.dumps(body), "signature": "a" * 64}

        with TestClient(create_api(kernel, None, broker, continuations_enabled=True)) as client:
            response = client.post("/v2/paypal/capture-continuations", json={**self.operation, "continuation_id": self.cid}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls, ["continuation_seed", "continuation-attestation", "continuation_register"])

    def test_failed_preparation_authorization_never_contacts_seller(self):
        def kernel(*args):
            raise BoundaryFailure("CONTINUATION_SCOPE_DENIED", 403)

        with TestClient(create_api(kernel, None, lambda *a: self.fail("No seller I/O"), continuations_enabled=True)) as client:
            response = client.post("/v2/paypal/capture-continuations", json={**self.operation, "continuation_id": self.cid}, headers=self.headers)
            self.assertEqual(response.status_code, 403)

    def test_capture_requires_issuer_command_success_before_broker_handoff(self):
        calls = []

        def kernel(name, args):
            calls.append(name)
            raise BoundaryFailure("CONTINUATION_APPROVAL_REQUIRED", 403)

        with TestClient(create_api(kernel, None, lambda *a: self.fail("No broker capture"), continuations_enabled=True)) as client:
            response = client.post("/v2/paypal/capture-continuations/" + self.cid + "/capture-requests", json=self.operation, headers=self.headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(calls, ["continuation_capture_request"])

    def test_broker_requires_internal_auth_and_separate_mutation_gate(self):
        app = broker_app(None, "x" * 32, lambda *a: self.fail("No seller"), "q" * 32,
                         mutations_enabled=False, continuations_enabled=True)
        with TestClient(app) as client:
            body = {**self.operation, "continuation_id": self.cid}
            self.assertEqual(client.post("/v1/internal/continuation-capture", json=body).status_code, 401)
            response = client.post("/v1/internal/continuation-capture", json=body, headers={"Authorization": "Bearer " + "x" * 32})
            self.assertEqual(response.status_code, 423)

    def test_adapter_reuses_original_capture_uuid_and_independent_canonical_get(self):
        kernel = FakeKernel()
        provider = FakeProvider(kernel)
        commits = []

        def continuation(name, args):
            if name == "continuation_commit":
                commits.append(args)
                return kernel("commit_phase", {**args, "phase": "CAPTURE"})
            return kernel(name, args)

        driver = ProtectedPayPal(continuation, provider, BrokerIdentity("mutate", "observe", "x" * 32, MERCHANT))
        driver.create("op_fixture")
        provider.status, kernel.capture_authorized = "APPROVED", True
        result = capture(driver, "op_fixture", self.cid)
        self.assertTrue(result["capture_confirmed"])
        self.assertEqual(commits[0]["cid"], self.cid)
        self.assertEqual(provider.calls[-2], ("POST_CAPTURE", kernel.b["capture_request_id"]))
        self.assertEqual(provider.calls[-1], ("GET", ORDER))
        capture(driver, "op_fixture", self.cid)
        self.assertEqual(sum(kind == "POST_CAPTURE" for kind, _ in provider.calls), 1)

    def test_local_caller_allows_only_named_continuation_verbs(self):
        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, **kwargs):
                self.url = url
                class Response:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"classification": "SYNTHETIC_HTTP_FIXTURE"}
                return Response()

        with patch("interlock.paypal_services.httpx.Client", return_value=Client()):
            caller = local_caller("http://127.0.0.1:12345", "/v1/internal/")
            for verb in ("continuation-attestation", "continuation-capture"):
                self.assertIn("classification", caller(verb, {}))
            with self.assertRaisesRegex(BoundaryFailure, "SERVICE_VERB_DENIED"):
                caller("arbitrary-command", {})

    def test_migration_guard_rejects_demo_before_any_database_engine(self):
        script = """import sqlalchemy
def forbidden(*a,**k): raise RuntimeError('DATABASE_CONNECTION_FORBIDDEN_IN_OFFLINE_TEST')
sqlalchemy.create_engine=forbidden
from alembic.config import Config
from alembic.command import upgrade
upgrade(Config('alembic.paypal.ini'),'0031_continuation_cutoff')
"""
        for domain in ("control", "seller", "reference"):
            for prefix in ("hackathon_disposable_008_demo_", "hackathon_disposable_013_continuation_"):
                env = {**os.environ, "PAYPAL_DISPOSABLE_CLUSTER": "postgres18", "PAYPAL_DISPOSABLE_DATASET": "m2_continuation",
                       "DB_DOMAIN": domain, "PAYPAL_DISPOSABLE_INSTANCE_VERIFIED": "true",
                       "PAYPAL_DISPOSABLE_SYSTEM_IDENTIFIER": "7706108909131505256",
                       "PAYPAL_DISPOSABLE_DATABASE_URL": "postgresql+psycopg://interlock_disposable_admin:synthetic@127.0.0.1:55438/" + prefix + domain}
                result = subprocess.run([sys.executable, "-B", "-c", script], env=env, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                expected = "CONTINUATION_ACCEPTANCE_DATABASE_REQUIRED" if "008_demo" in prefix else "DATABASE_CONNECTION_FORBIDDEN_IN_OFFLINE_TEST"
                self.assertIn(expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
