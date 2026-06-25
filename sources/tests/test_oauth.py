"""test_oauth.py - token store, rotating refresh token, and expiry logic (offline, mocked HTTP)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fixture
import oauth

NOW = 1_700_000_000


class FakeHTTP:
    def __init__(self, post_response):
        self.post_response = post_response
        self.post_calls = 0

    def get(self, url, timeout=15):
        return {"authorization_endpoint": "https://appcenter.intuit.com/connect/oauth2",
                "token_endpoint": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"}

    def post(self, url, data, headers, timeout=20):
        self.post_calls += 1
        return dict(self.post_response)


class OAuthTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        self.tmp = tempfile.mkdtemp(prefix="qbo_tok_")
        os.environ.update({
            "QBO_ENV": "sandbox", "QBO_CLIENT_ID": "cid", "QBO_CLIENT_SECRET": "secret",
            "QBO_REALM_ID": "9999", "QBO_TOKEN_STORE": os.path.join(self.tmp, "tokens.json"),
        })
        self.config = oauth.Config()
        self.store = oauth.TokenStore(self.config.token_store_path)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_refresh_rotates_token_and_sets_absolute_expiries(self):
        self.store.save({"refresh_token": "RT1"})
        http = FakeHTTP({"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600,
                         "x_refresh_token_expires_in": 8726400, "token_type": "bearer"})
        tokens = oauth.refresh_tokens(self.config, self.store, now=NOW,
                                      http_get=http.get, http_post=http.post)
        self.assertEqual(tokens["access_token"], "AT2")
        self.assertEqual(tokens["refresh_token"], "RT2")            # rotated
        self.assertEqual(tokens["access_token_expires_at"], NOW + 3600)
        self.assertEqual(tokens["refresh_token_expires_at"], NOW + 8726400)
        # persisted to disk
        self.assertEqual(self.store.load()["refresh_token"], "RT2")

    def test_refresh_keeps_existing_token_when_response_omits_one(self):
        self.store.save({"refresh_token": "RT1"})
        http = FakeHTTP({"access_token": "AT2", "expires_in": 3600})  # no refresh_token in response
        tokens = oauth.refresh_tokens(self.config, self.store, now=NOW,
                                      http_get=http.get, http_post=http.post)
        self.assertEqual(tokens["refresh_token"], "RT1")            # kept

    def test_valid_token_uses_cache_until_expiry(self):
        self.store.save({"access_token": "AT1", "refresh_token": "RT1",
                         "access_token_expires_at": NOW + 3600})
        http = FakeHTTP({"access_token": "AT2"})
        tok = oauth.valid_access_token(self.config, self.store, now=NOW,
                                       http_get=http.get, http_post=http.post)
        self.assertEqual(tok, "AT1")
        self.assertEqual(http.post_calls, 0)                       # no refresh needed

    def test_valid_token_refreshes_when_expired(self):
        self.store.save({"access_token": "AT1", "refresh_token": "RT1",
                         "access_token_expires_at": NOW + 3600})
        http = FakeHTTP({"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600})
        tok = oauth.valid_access_token(self.config, self.store, now=NOW + 4000,
                                       http_get=http.get, http_post=http.post)
        self.assertEqual(tok, "AT2")
        self.assertEqual(http.post_calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
