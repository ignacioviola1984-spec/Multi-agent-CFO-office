"""
test_identity.py - Deterministic, offline tests for identity/ (auth, RBAC, SoD).

No external IdP, no network, no API key: everything runs against LocalDevIdentity
signed tokens. Proves the spec's requirements:
  * unauthenticated approval calls fail;
  * wrong-role approvals fail;
  * maker == checker (proposer == approver) is rejected;
  * audit entries carry the authenticated identity (subject id + display name).
Plus token integrity (tamper/expiry), the OIDC stub boundary, and claim mapping.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets
from identity import access, providers, signoff, tokens
from governance import audit


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "IDENTITY_PROVIDER", "SECRETS_PROVIDER", "LOCAL_IDENTITY_SIGNING_KEY")}
        for k in self._saved:
            os.environ.pop(k, None)
        appsecrets.reset_provider()
        providers.reset_provider()
        self.local = providers.LocalDevIdentity()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        appsecrets.reset_provider()
        providers.reset_provider()


class TestTokens(Base):
    def test_mint_verify_roundtrip(self):
        tok = self.local.mint_token("u-tax-mgr")
        ident = self.local.authenticate(tok)
        self.assertEqual(ident.subject, "u-tax-mgr")
        self.assertEqual(ident.name, "Nadia Vega")
        self.assertIn("Tax Manager", ident.roles)

    def test_tampered_token_rejected(self):
        tok = self.local.mint_token("u-tax-mgr")
        # Flip a character in the payload segment.
        h, p, s = tok.split(".")
        bad = h + "." + ("A" if p[0] != "A" else "B") + p[1:] + "." + s
        with self.assertRaises(access.Unauthenticated):
            self.local.authenticate(bad)

    def test_expired_token_rejected(self):
        tok = tokens.mint("u-cfo", "Ignacio Viola", ["CFO"], ttl_seconds=-10)
        with self.assertRaises(access.Unauthenticated):
            self.local.authenticate(tok)

    def test_signing_key_rotation_window(self):
        # A token signed with the _NEXT key still verifies while the window is open.
        os.environ["LOCAL_IDENTITY_SIGNING_KEY"] = "current-key-abcdefgh"
        os.environ["LOCAL_IDENTITY_SIGNING_KEY_NEXT"] = "next-key-12345678"
        appsecrets.reset_provider()
        try:
            tok = tokens.mint("u-cfo", "Ignacio Viola", ["CFO"], key="next-key-12345678")
            ident = self.local.authenticate(tok)
            self.assertEqual(ident.subject, "u-cfo")
        finally:
            os.environ.pop("LOCAL_IDENTITY_SIGNING_KEY_NEXT", None)


class TestAuthAndRBAC(Base):
    def test_unauthenticated_fails(self):
        with self.assertRaises(access.Unauthenticated):
            access.authenticate("", provider=self.local)
        with self.assertRaises(access.Unauthenticated):
            access.authenticate(None, provider=self.local)

    def test_wrong_role_unauthorized(self):
        tok = self.local.mint_token("u-tax-mgr")          # holds "Tax Manager"
        ident = access.authenticate(tok, provider=self.local)
        with self.assertRaises(access.Unauthorized):
            access.require_role(ident, "Treasurer")
        # correct role passes
        self.assertIs(access.require_role(ident, "Tax Manager"), ident)

    def test_authorize_combines_auth_and_role(self):
        tok = self.local.mint_token("u-treasurer")
        ident = access.authorize(tok, "Treasurer", provider=self.local)
        self.assertEqual(ident.subject, "u-treasurer")


class TestSegregation(Base):
    def test_same_subject_rejected(self):
        with self.assertRaises(access.SegregationOfDutiesError):
            access.assert_distinct("u-treasurer", "u-treasurer", item="PMT-1")

    def test_distinct_subjects_ok(self):
        access.assert_distinct("u-treasurer", "u-controller", item="PMT-1")  # no raise


class TestAuthenticatedSignoff(Base):
    def _audit_path(self):
        d = tempfile.mkdtemp()
        return os.path.join(d, "trail.jsonl")

    def test_signoff_records_identity(self):
        path = self._audit_path()
        tok = self.local.mint_token("u-controller")
        rec = signoff.record_signoff("payment", "PMT-100", "Controller", tok,
                                     decision="approved", reason="within limits",
                                     proposer_subject="u-treasurer",
                                     provider=self.local, audit_path=path)
        self.assertEqual(rec["subject"], "u-controller")
        self.assertEqual(rec["name"], "Bruno Diaz")
        entries = audit.entries_where(path, action="payment.signoff.approved")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["subject"], "u-controller")
        self.assertIn("Bruno Diaz", entries[0]["actor"])   # subject id + display name

    def test_signoff_wrong_role_blocks_and_is_not_recorded_as_approved(self):
        path = self._audit_path()
        tok = self.local.mint_token("u-tax-mgr")           # not a Controller
        with self.assertRaises(access.Unauthorized):
            signoff.record_signoff("payment", "PMT-101", "Controller", tok,
                                   provider=self.local, audit_path=path)
        self.assertEqual(audit.entries_where(path, action="payment.signoff.approved"), [])

    def test_signoff_self_approval_blocked(self):
        path = self._audit_path()
        tok = self.local.mint_token("u-treasurer")         # holds Treasurer
        with self.assertRaises(access.SegregationOfDutiesError):
            signoff.record_signoff("payment", "PMT-102", "Treasurer", tok,
                                   proposer_subject="u-treasurer",  # same subject proposed
                                   provider=self.local, audit_path=path)


class TestOidcStub(Base):
    def test_authenticate_raises_until_wired(self):
        os.environ["IDENTITY_PROVIDER"] = "oidc"
        providers.reset_provider()
        p = providers.get_provider()
        self.assertIsInstance(p, providers.OidcProvider)
        with self.assertRaises(NotImplementedError):
            p.authenticate("any.jwt.token")

    def test_claim_mapping_and_validation(self):
        os.environ["OIDC_ISSUER"] = "https://acme.auth0.com/"
        os.environ["OIDC_CLIENT_ID"] = "client-123"
        os.environ["OIDC_ROLES_CLAIM"] = "https://acme/roles"
        appsecrets.reset_provider()
        p = providers.OidcProvider()
        claims = {"iss": "https://acme.auth0.com/", "aud": "client-123",
                  "sub": "auth0|abc", "name": "Real Person",
                  "https://acme/roles": ["Controller"], "exp": 9999999999}
        ident = p.authenticate_claims(claims)
        self.assertEqual(ident.subject, "auth0|abc")
        self.assertIn("Controller", ident.roles)
        # wrong issuer rejected
        with self.assertRaises(access.Unauthenticated):
            p.authenticate_claims({**claims, "iss": "https://evil/"})


class TestReviewIdentityBinding(Base):
    def test_review_with_token_carries_identity(self):
        sys.path.insert(0, os.path.join(ROOT, "cfo-office"))
        sys.path.insert(0, os.path.join(ROOT, "orchestration"))
        from shared_state import CFOContext
        import review
        ctx = CFOContext()
        tok = self.local.mint_token("u-tax-mgr")           # holds "Tax Manager"
        rec = review.review(ctx, "Tax", token=tok, provider=self.local)
        self.assertEqual(rec["decision"], "approved")
        self.assertEqual(rec["mode"], "human")
        self.assertEqual(rec["subject"], "u-tax-mgr")
        self.assertEqual(rec["name"], "Nadia Vega")

    def test_review_wrong_role_token_rejected(self):
        sys.path.insert(0, os.path.join(ROOT, "cfo-office"))
        sys.path.insert(0, os.path.join(ROOT, "orchestration"))
        from shared_state import CFOContext
        import review
        ctx = CFOContext()
        tok = self.local.mint_token("u-treasurer")         # NOT a Tax Manager
        with self.assertRaises(access.Unauthorized):
            review.review(ctx, "Tax", token=tok, provider=self.local)


if __name__ == "__main__":
    unittest.main(verbosity=2)
