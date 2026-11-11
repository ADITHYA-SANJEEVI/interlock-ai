import json
import tempfile
import unittest
from pathlib import Path

from interlock.paypal_sandbox import PayPalFailure

from tools.hackathon.paypal_profiles import (
    ProfileBlocked,
    fingerprint,
    load_profile,
    previous_bindings,
    require_run_profile,
    require_verified_us,
    validate_us_profile,
    verify_us_profile,
)

US = {
    "PAYPAL_PROFILE_ID": "us", "PAYPAL_CLIENT_ID": "fixture-us-client", "PAYPAL_CLIENT_SECRET": "fixture-us-secret",
    "PAYPAL_MERCHANT_ID": "USMERCHANT1234", "PAYPAL_SELLER_COUNTRY": "US", "PAYPAL_BUYER_COUNTRY": "US",
    "PAYPAL_CURRENCY": "USD", "PAYPAL_ACCOUNT_TYPE": "BUSINESS", "PAYPAL_APP_TYPE": "MERCHANT",
    "PAYPAL_APP_BINDING_CONFIRMED": "true",
}


class FakeAuth:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def oauth(self):
        self.calls += 1
        if self.fail:
            raise PayPalFailure({"name": "invalid_client"})
        return {"authenticated": True, "environment": "PAYPAL_SANDBOX"}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_us_profile_never_falls_back_to_india(self):
        old = self.directory / "paypal.env"
        original = b"PAYPAL_CLIENT_ID=old-fixture\nPAYPAL_CLIENT_SECRET=old-secret\n"
        old.write_bytes(original)
        with self.assertRaisesRegex(ProfileBlocked, "MERCHANT_PROFILE_MISSING"):
            load_profile("us", self.directory)
        (self.directory / "paypal-us.env").write_text("PAYPAL_CLIENT_ID=\nPAYPAL_CLIENT_SECRET=\n")
        with self.assertRaisesRegex(ProfileBlocked, "PAYPAL_CREDENTIALS_REQUIRED"):
            load_profile("us", self.directory)
        self.assertEqual(old.read_bytes(), original)

    def test_complete_verification_is_oauth_only_and_preserves_india_file(self):
        old = self.directory / "paypal.env"
        old.write_bytes(b"old-configuration-to-retain")
        client = FakeAuth()
        result = verify_us_profile(US, client, directory=self.directory)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["provider_order_mutations"], 0)
        self.assertTrue(result["oauth_verified"])
        self.assertFalse(result["provider_merchant_binding_verified"])
        self.assertFalse(result["payment_acceptance_verified"])
        self.assertNotIn("configuration_sha256", result)
        require_verified_us(US, directory=self.directory)
        self.assertEqual(old.read_bytes(), b"old-configuration-to-retain")
        for path in self.directory.glob("*verification*.json"):
            raw = path.read_text()
            self.assertNotIn(US["PAYPAL_CLIENT_SECRET"], raw)
            self.assertNotIn(US["PAYPAL_CLIENT_ID"], raw)

    def test_oauth_failure_does_not_open_creation_gate(self):
        with self.assertRaises(PayPalFailure):
            verify_us_profile(US, FakeAuth(fail=True), directory=self.directory)
        with self.assertRaisesRegex(ProfileBlocked, "US_CONFIGURATION_VERIFICATION_REQUIRED"):
            require_verified_us(US, directory=self.directory)

    def test_credential_rotation_invalidates_verification(self):
        verify_us_profile(US, FakeAuth(), directory=self.directory)
        changed = {**US, "PAYPAL_CLIENT_SECRET": "rotated-fixture-secret"}
        with self.assertRaisesRegex(ProfileBlocked, "US_CONFIGURATION_CHANGED_REVERIFY"):
            require_verified_us(changed, directory=self.directory)

    def test_india_app_and_merchant_reuse_rejected_before_oauth(self):
        for kwargs in ({"india_client_id": US["PAYPAL_CLIENT_ID"]},
                       {"prior_merchants": {US["PAYPAL_MERCHANT_ID"]}}):
            client = FakeAuth()
            with self.subTest(kwargs=kwargs), self.assertRaises(ProfileBlocked):
                verify_us_profile(US, client, directory=self.directory, **kwargs)
            self.assertEqual(client.calls, 0)

    def test_us_account_attestation_required(self):
        for key, value in (("PAYPAL_SELLER_COUNTRY", "IN"), ("PAYPAL_ACCOUNT_TYPE", "PERSONAL"),
                           ("PAYPAL_APP_BINDING_CONFIRMED", "false"), ("PAYPAL_CURRENCY", "INR"),
                           ("PAYPAL_MERCHANT_ID", "")):
            with self.subTest(key=key), self.assertRaises(ProfileBlocked):
                validate_us_profile({**US, key: value})

    def test_existing_run_cannot_switch_credential_profiles(self):
        with self.assertRaisesRegex(ProfileBlocked, "RUN_MERCHANT_PROFILE_MISMATCH"):
            require_run_profile({"profile_id": "india"}, US)
        with self.assertRaisesRegex(ProfileBlocked, "RUN_MERCHANT_PROFILE_MISMATCH"):
            require_run_profile({}, US)  # Legacy runs are the original India profile.
        state = {"profile_id": "us", "profile_configuration_sha256": fingerprint(US)}
        require_run_profile(state, US)
        with self.assertRaisesRegex(ProfileBlocked, "RUN_CREDENTIAL_CONFIGURATION_CHANGED"):
            require_run_profile(state, {**US, "PAYPAL_CLIENT_ID": "different-client"})

    def test_historical_verifications_are_retained(self):
        verify_us_profile(US, FakeAuth(), directory=self.directory)
        first = {p.name: p.read_bytes() for p in self.directory.glob("paypal-us-verification-*.json")}
        verify_us_profile(US, FakeAuth(), directory=self.directory)
        self.assertEqual(len(list(self.directory.glob("paypal-us-verification-*.json"))), 2)
        self.assertTrue(all((self.directory / name).read_bytes() == raw for name, raw in first.items()))

    def test_optional_prior_bindings_do_not_require_private_history(self):
        evidence = self.directory / "evidence"
        self.assertEqual(previous_bindings(evidence, directory=self.directory), (None, set()))
        (self.directory / "paypal.env").write_text("PAYPAL_CLIENT_ID=old-client\n")
        prior = evidence / "india-01"
        prior.mkdir(parents=True)
        (prior / "state.json").write_text(json.dumps({"merchant_id": "OLDMERCHANT123"}))
        self.assertEqual(previous_bindings(evidence, directory=self.directory), ("old-client", {"OLDMERCHANT123"}))
