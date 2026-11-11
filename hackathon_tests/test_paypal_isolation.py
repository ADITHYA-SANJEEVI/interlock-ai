"""Offline configuration/source checks; these never construct a database engine."""

import os
import subprocess
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from interlock.paypal_flow import BoundaryFailure
from interlock.paypal_kernel import PostgreSQLKernel
from sqlalchemy.exc import DBAPIError

from paypal_acceptance.harness import verified_configuration
from tools.hackathon.disposable_binding import DATA, SYSTEM, checked_metadata, checked_url
from tools.hackathon.verify_usd_lift import verify


class IsolationTests(unittest.TestCase):
    def test_cluster_selection_has_no_fallback_or_cross_cluster_acceptance(self):
        code = """from tools.hackathon.disposable_binding import PORT,SYSTEM,DATA,VERSION,checked_metadata,checked_url
checked_metadata(SYSTEM,DATA,VERSION,str(PORT),'127.0.0.1')
value=f'postgresql+psycopg://interlock_disposable_admin:synthetic@127.0.0.1:{PORT}/hackathon_disposable_fixture_control'
checked_url(value,'control')
try:
 checked_url(value.replace(':'+str(PORT)+'/',':'+str(55438 if PORT==55432 else 55432)+'/'),'control')
except RuntimeError: pass
else: raise AssertionError('CROSS_CLUSTER_PORT_ACCEPTED')
"""
        for selector in ("postgres16", "postgres18", "postgres17", ""):
            result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                                    env={**os.environ, "PAYPAL_DISPOSABLE_CLUSTER": selector})
            self.assertEqual(result.returncode == 0, selector in {"postgres16", "postgres18"})

    def test_authorized_cluster_binding_rejects_other_ports_hosts_and_cluster_metadata(self):
        good = "postgresql+psycopg://interlock_disposable_admin:synthetic@127.0.0.1:55432/hackathon_disposable_006_control"
        self.assertEqual(checked_url(good, "control").hostname, "127.0.0.1")
        checked_metadata(SYSTEM, DATA, "16.15", "55432", "127.0.0.1")
        for value in (good.replace(":55432/", ":5432/"), good.replace("127.0.0.1", "localhost"),
                      good.replace("hackathon_disposable_006_control", "control")):
            with self.assertRaises(RuntimeError):
                checked_url(value, "control")
        for metadata in (("12345", DATA, "16.15", "55432", "127.0.0.1"),
                         (SYSTEM, "C:/Program Files/PostgreSQL/17/data", "16.15", "55432", "127.0.0.1"),
                         (SYSTEM, DATA, "17.5", "55432", "127.0.0.1")):
            with self.assertRaises(RuntimeError):
                checked_metadata(*metadata)

    def test_disposable_acceptance_requires_explicit_human_verified_configuration(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            verified_configuration()

    def test_missing_selector_is_rejected_even_with_the_previous_valid_system_identifier(self):
        with patch.dict(os.environ, {"PAYPAL_DISPOSABLE_ACCEPTANCE_ACK":
                "SYNTHETIC_FIXTURES_ON_VERIFIED_DISPOSABLE_POSTGRES_ONLY",
                "PAYPAL_TEST_SYSTEM_IDENTIFIER": SYSTEM}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "EXPLICIT_AUTHORIZED_DISPOSABLE_CLUSTER_REQUIRED"):
                verified_configuration()

    def test_forbidden_existing_databases_are_rejected_before_connection(self):
        base = {"PAYPAL_DISPOSABLE_ACCEPTANCE_ACK": "SYNTHETIC_FIXTURES_ON_VERIFIED_DISPOSABLE_POSTGRES_ONLY",
                "PAYPAL_TEST_SYSTEM_IDENTIFIER": "123456789"}
        for database in ("control", "test_control", "seller", "reference", "postgres"):
            values = {**base, "PAYPAL_TEST_CONTROL_OWNER_URL": "postgresql+psycopg://owner:fixture@localhost:55432/" + database}
            with self.subTest(database=database), patch.dict(os.environ, values, clear=True), self.assertRaises(RuntimeError):
                verified_configuration()

    def test_runtime_database_has_no_fallback_and_requires_exact_non_owner_role(self):
        for url in ("", "sqlite:///fixture.sqlite", "postgresql+psycopg://interlock:fixture@localhost/newdb"):
            with self.subTest(url=url), self.assertRaises(BoundaryFailure):
                PostgreSQLKernel(url, "interlock_api")

    def test_currency_lift_preserves_every_non_allowlisted_source_statement(self):
        result = verify()
        self.assertEqual(result["functions_checked"], 3)
        self.assertTrue(result["legacy_sources_unchanged"])
        self.assertFalse(result["postgresql_executed"])

    def test_unknown_database_errors_never_echo_private_diagnostics(self):
        original = Exception("private-provider-secret")
        original.sqlstate = "P0001"
        original.diag = SimpleNamespace(message_primary="private-provider-secret", message_detail="403")

        @contextmanager
        def failed(url):
            raise DBAPIError(None, None, original)
            yield

        kernel = PostgreSQLKernel("postgresql+psycopg://interlock_api:fixture@localhost/disposable", "interlock_api")
        with patch("interlock.paypal_kernel.transaction", failed), self.assertRaises(BoundaryFailure) as error:
            kernel("snapshot", {"token": "fixture"})
        self.assertNotIn("private-provider-secret", str(error.exception))

    def test_observation_does_not_require_the_new_send_enable_gate(self):
        statements = []

        class Database:
            def execute(self, statement):
                statements.append(str(statement))

            def scalar(self, statement, parameters=None):
                statements.append(str(statement))
                return "interlock_broker" if str(statement) == "SELECT session_user" else {"binding": "fixture"}

        @contextmanager
        def connection(url):
            yield Database()

        kernel = PostgreSQLKernel("postgresql+psycopg://interlock_broker:fixture@localhost/disposable", "interlock_broker")
        with patch("interlock.paypal_kernel.transaction", connection):
            kernel("binding", {"secret": "fixture", "opid": "op"})
        self.assertFalse(any("assert_enabled" in s for s in statements))
