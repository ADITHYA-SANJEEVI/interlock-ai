"""Offline operator-boundary checks. No private profiles, providers or databases."""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from tools.hackathon import m2015_execution as run


class BoundedOperatorTests(unittest.TestCase):
    def fresh(self):
        expiry = (datetime.now(UTC)+timedelta(hours=1)).isoformat()
        return {"operation_id": "op_new_fixture", "order_id": "FIXTUREORDER12345", "grant_expires_at": expiry,
                "service_expires_at": expiry, "quote_expires_at": expiry, "binding": {"capture_request_id": "FIXTURE"}}

    def test_preserved_or_unbound_operation_is_never_a_mutation_target(self):
        state = self.fresh()
        for target in (None, run.OLD_OPERATION, "op_other_fixture"):
            with self.assertRaisesRegex(RuntimeError, "M2015_NEW_OPERATION_ONLY"):
                run.require_operation(state, target)
        run.require_operation(state, state["operation_id"])

    def test_human_approval_and_recovery_commands_are_not_exposed(self):
        client = run.Client.__new__(run.Client)
        client.state = {}
        for command in ("commands/approve", "cancellations", "replacement-admissions", "quote-renewals"):
            with self.assertRaisesRegex(RuntimeError, "M2015_COMMAND_NOT_EXPOSED"):
                client.post("fixture", command, {})

    def test_provider_command_must_use_exact_new_operation_and_explicit_usd(self):
        client = run.Client.__new__(run.Client)
        client.state = self.fresh()
        for body in ({"operation_id": run.OLD_OPERATION, "currency": "USD"},
                     {"operation_id": client.state["operation_id"]},
                     {"operation_id": client.state["operation_id"], "currency": "INR"}):
            with self.assertRaises(RuntimeError):
                client.post("fixture", "create", body)

    def test_uncertain_command_is_never_automatically_replayed(self):
        client = run.Client.__new__(run.Client)
        client.state = {"commands": {"fixture": {"path": "commands/propose", "body": {}, "revision": 0,
                                               "role": "requester", "sent": True}}}
        with self.assertRaisesRegex(RuntimeError, "M2015_UNKNOWN_COMMAND_NO_AUTOMATIC_RETRY"):
            client.post("fixture", "commands/propose", {}, 0)

    def test_known_command_response_is_recovered_without_http(self):
        client = run.Client.__new__(run.Client)
        client.state = {"commands": {"fixture": {"path": "commands/propose", "body": {}, "revision": 0,
                                               "role": "requester", "sent": True, "response": {"action_id": "fixture"}}}}
        self.assertEqual(client.post("fixture", "commands/propose", {}, 0), {"action_id": "fixture"})

    def test_expired_authority_always_stops(self):
        for key in ("grant_expires_at", "service_expires_at", "quote_expires_at"):
            state = self.fresh()
            state[key] = "2000-01-01T00:00:00Z"
            with self.assertRaises(RuntimeError):
                run.freshness(state)

    def test_capture_without_exact_human_decision_never_reaches_http(self):
        client = run.Client.__new__(run.Client)
        client.state = self.fresh()
        with patch.object(run, "binding"), self.assertRaisesRegex(RuntimeError, "M2015_EXACT_HUMAN_CAPTURE_DECISION_REQUIRED"):
            run.capture_once(client)

    def test_capture_window_without_human_decision_never_opens_sql_gate(self):
        state = self.fresh()
        with patch.object(run, "binding"), patch.object(run, "assert_preserved"), patch.object(run, "connect", side_effect=AssertionError("No database")):
            with self.assertRaisesRegex(RuntimeError, "M2015_SEPARATE_HUMAN_CAPTURE_AUTHORIZATION_REQUIRED"):
                with run.mutation_window(state, "CAPTURE"):
                    self.fail("Capture gate opened")

    def test_retry_never_creates_a_redundant_grant(self):
        client = run.Client.__new__(run.Client)
        client.state = {"retry_authorization": "DIRECTIVE_015B"}
        with self.assertRaisesRegex(RuntimeError, "M2015B_REUSE_EXISTING_GRANT_ONLY"):
            run.setup_grant(client)

    def test_renewal_requires_explicit_retry_scope_and_operator_go(self):
        client = run.Client.__new__(run.Client)
        base = {"retry_authorization": "DIRECTIVE_015B",
                "expired_action_id": "action_92320a20-9d71-409b-a5f0-b776a59d67b5"}
        for changes in ({}, {"operator_ready_at": "fixture", "operation_id": "op_already_admitted"},
                        {"operator_ready_at": "fixture", "expired_action_id": "wrong_action"}):
            client.state = {**base, **changes}
            with self.assertRaisesRegex(RuntimeError, "M2015_COMMAND_NOT_EXPOSED"):
                client.post("quote", "quote-renewals", {"proposal_id": "fixture"})

    def test_prepare_without_go_creates_no_quote(self):
        client = run.Client.__new__(run.Client)
        client.state = {"retry_authorization": "DIRECTIVE_015B"}
        with self.assertRaisesRegex(RuntimeError, "M2015_READY_OPERATOR_AND_NO_PRIOR_OPERATION_REQUIRED"):
            run.prepare(client)


if __name__ == "__main__":
    unittest.main()
