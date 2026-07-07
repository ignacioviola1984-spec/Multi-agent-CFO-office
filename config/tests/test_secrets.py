"""
test_secrets.py - Deterministic, offline tests for config/secrets.py and the
governance audit trail's redaction. No network, no real vault, no API key.

Proves:
  * EnvFileProvider is the default and reads from the environment.
  * get_required names the missing secret but never leaks a value.
  * VaultProvider is a stub: its fetch seam raises until a backend is injected;
    with an injected backend the interface works end to end.
  * Provider selection is by SECRETS_PROVIDER (configuration, not code).
  * redact()/redact_obj() mask known secret values.
  * A generated artifact (a governance audit entry) never contains a known
    test-secret value -- the "grep artifacts for the secret" guarantee.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets
from governance import audit

TEST_SECRET = "sk-ant-TESTSECRET-do-not-log-1234567890abcdef"
TEST_HMAC = "whsec_TESTHMAC_rotationsecret_0987654321fedcba"


class EnvVarSandbox(unittest.TestCase):
    """Base: isolate SECRETS_PROVIDER and any secret env vars per test."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "SECRETS_PROVIDER", "ANTHROPIC_API_KEY", "QBO_CLIENT_SECRET",
            "WEBHOOK_HMAC_WALLET", "WEBHOOK_HMAC_WALLET_NEXT")}
        for k in self._saved:
            os.environ.pop(k, None)
        appsecrets.reset_provider()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        appsecrets.reset_provider()


class TestEnvProvider(EnvVarSandbox):
    def test_default_provider_is_envfile(self):
        p = appsecrets.get_provider()
        self.assertIsInstance(p, appsecrets.EnvFileProvider)
        self.assertEqual(p.name, "env")

    def test_get_secret_reads_environment(self):
        os.environ["ANTHROPIC_API_KEY"] = TEST_SECRET
        appsecrets.reset_provider()
        self.assertEqual(appsecrets.get_secret("ANTHROPIC_API_KEY"), TEST_SECRET)

    def test_absent_secret_returns_default_not_raise(self):
        self.assertIsNone(appsecrets.get_secret("ANTHROPIC_API_KEY"))
        self.assertEqual(appsecrets.get_secret("NOPE", "fallback"), "fallback")

    def test_get_required_raises_without_leaking_value(self):
        with self.assertRaises(appsecrets.SecretNotFound) as cm:
            appsecrets.get_required("QBO_CLIENT_SECRET")
        self.assertIn("QBO_CLIENT_SECRET", str(cm.exception))

    def test_repr_never_contains_value(self):
        os.environ["ANTHROPIC_API_KEY"] = TEST_SECRET
        appsecrets.reset_provider()
        self.assertNotIn(TEST_SECRET, repr(appsecrets.get_provider()))


class TestProviderSelection(EnvVarSandbox):
    def test_select_vault_by_env(self):
        os.environ["SECRETS_PROVIDER"] = "vault"
        appsecrets.reset_provider()
        self.assertIsInstance(appsecrets.get_provider(), appsecrets.VaultProvider)

    def test_unknown_provider_raises(self):
        os.environ["SECRETS_PROVIDER"] = "banana"
        appsecrets.reset_provider()
        with self.assertRaises(ValueError):
            appsecrets.get_provider()


class TestVaultStub(EnvVarSandbox):
    def test_stub_fetch_raises_until_backend_injected(self):
        v = appsecrets.VaultProvider()
        with self.assertRaises(NotImplementedError):
            v._fetch("ANTHROPIC_API_KEY")

    def test_injected_backend_satisfies_interface(self):
        class FakeVault:
            def __init__(self, store):
                self.store = store
            def get_secret_value(self, name):
                return self.store[name]

        v = appsecrets.VaultProvider(backend=FakeVault({"ANTHROPIC_API_KEY": TEST_SECRET}))
        self.assertEqual(v.get_secret("ANTHROPIC_API_KEY"), TEST_SECRET)
        # a genuine backend miss returns the default, not an exception
        self.assertEqual(v.get_secret("MISSING", "d"), "d")


class TestRedaction(EnvVarSandbox):
    def test_redact_masks_known_secret(self):
        os.environ["ANTHROPIC_API_KEY"] = TEST_SECRET
        appsecrets.reset_provider()
        out = appsecrets.redact(f"key is {TEST_SECRET} ok")
        self.assertNotIn(TEST_SECRET, out)
        self.assertIn(appsecrets.MASK, out)

    def test_redact_masks_dynamic_webhook_secret(self):
        os.environ["WEBHOOK_HMAC_WALLET"] = TEST_HMAC
        appsecrets.reset_provider()
        self.assertNotIn(TEST_HMAC, appsecrets.redact(f"sig from {TEST_HMAC}"))

    def test_redact_obj_recurses(self):
        os.environ["ANTHROPIC_API_KEY"] = TEST_SECRET
        appsecrets.reset_provider()
        obj = {"a": [TEST_SECRET, {"b": TEST_SECRET}], "n": 3}
        red = appsecrets.redact_obj(obj)
        self.assertNotIn(TEST_SECRET, str(red))
        self.assertEqual(red["n"], 3)

    def test_short_values_are_not_scrubbed(self):
        # A trivially short "secret" must not cause incidental masking.
        os.environ["ANTHROPIC_API_KEY"] = "abc"
        appsecrets.reset_provider()
        self.assertEqual(appsecrets.redact("abc def"), "abc def")


class TestArtifactsHaveNoSecret(EnvVarSandbox):
    """The spec's grep guarantee: generated artifacts never carry a secret value."""

    def test_audit_trail_never_contains_secret(self):
        os.environ["ANTHROPIC_API_KEY"] = TEST_SECRET
        os.environ["WEBHOOK_HMAC_WALLET"] = TEST_HMAC
        appsecrets.reset_provider()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trail.jsonl")
            # A careless caller puts a secret into the reason and a field...
            audit.record("webhook.received", actor="system",
                         reason=f"payload signed with {TEST_HMAC}",
                         path=path, api_key=TEST_SECRET, note="ok")
            raw = open(path, encoding="utf-8").read()
        # ...and it is scrubbed on the way to disk.
        self.assertNotIn(TEST_SECRET, raw)
        self.assertNotIn(TEST_HMAC, raw)
        self.assertIn(appsecrets.MASK, raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
