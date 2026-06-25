"""
oauth.py - QuickBooks Online OAuth2 (authorization-code) for the sandbox.

What this handles, per Intuit's current rules:
  - Endpoints come from Intuit's OpenID discovery document, not hardcoded URLs:
    production .well-known/openid_configuration, sandbox
    openid_sandbox_configuration. We read authorization_endpoint and
    token_endpoint from there.
  - The access token lasts ~60 minutes and is auto-refreshed.
  - The REFRESH token ROTATES (roughly every 24h): every token response may carry
    a NEW refresh_token, so we always persist the latest one, plus the new
    x_refresh_token_expires_in Intuit returns (validity up to ~5 years now, not
    the old 100-day rule).
  - The token store is a local JSON file OUTSIDE the repo and gitignored.
  - client_id / client_secret / refresh_token / realm_id come from .env.
  - QBO_ENV switches the Accounting API base (sandbox vs production); same code.

No business logic here; this only obtains and persists a valid bearer token.
"""

import base64
import json
import os
import time
import urllib.parse
import urllib.request

# Intuit OpenID discovery documents (the documented entry points; the actual
# auth/token endpoints are READ from these, not hardcoded).
DISCOVERY_URLS = {
    "production": "https://developer.api.intuit.com/.well-known/openid_configuration/",
    "sandbox": "https://developer.api.intuit.com/.well-known/openid_sandbox_configuration/",
}

# Accounting API base by environment (this is NOT part of the OpenID document).
API_BASE = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}

# QuickBooks Accounting scope. NOTE: this scope grants WRITE too; the adapter
# enforces read-only in code (Intuit has no read-only accounting scope).
SCOPE_ACCOUNTING = "com.intuit.quickbooks.accounting"

ACCESS_TOKEN_SKEW_SECONDS = 60   # refresh a little early to avoid edge expiries


class QBOAuthError(Exception):
    """OAuth / token problem (missing config, refused refresh, etc.)."""


class Config:
    """QBO connection config, entirely from environment (.env)."""

    def __init__(self, env=None):
        self.env = (env or os.environ.get("QBO_ENV", "sandbox")).lower()
        if self.env not in API_BASE:
            raise QBOAuthError(f"QBO_ENV must be one of {sorted(API_BASE)}, got '{self.env}'")
        self.client_id = os.environ.get("QBO_CLIENT_ID", "")
        self.client_secret = os.environ.get("QBO_CLIENT_SECRET", "")
        self.refresh_token = os.environ.get("QBO_REFRESH_TOKEN", "")
        self.realm_id = os.environ.get("QBO_REALM_ID", "")
        self.redirect_uri = os.environ.get("QBO_REDIRECT_URI", "http://localhost:8000/callback")
        self.token_store_path = os.environ.get("QBO_TOKEN_STORE") or default_token_store_path()

    @property
    def api_base(self):
        return API_BASE[self.env]

    @property
    def discovery_url(self):
        return DISCOVERY_URLS[self.env]

    def require_app_credentials(self):
        if not self.client_id or not self.client_secret:
            raise QBOAuthError("QBO_CLIENT_ID and QBO_CLIENT_SECRET must be set in .env")


def default_token_store_path():
    """A path OUTSIDE the repo so the token store is never near the working tree."""
    return os.path.join(os.path.expanduser("~"), ".ai-finance", "qbo_tokens.json")


# --------------------------------------------------------------------------
# HTTP (kept tiny and injectable so tests never hit the network)
# --------------------------------------------------------------------------
def _http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_post_form(url, data, headers, timeout=20):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def discovery(config, http_get=_http_get_json):
    """Return the OpenID discovery document for the configured environment."""
    doc = http_get(config.discovery_url)
    for key in ("authorization_endpoint", "token_endpoint"):
        if key not in doc:
            raise QBOAuthError(f"discovery document missing '{key}'")
    return doc


# --------------------------------------------------------------------------
# Token store (the rotating refresh token + the access token + both expiries)
# --------------------------------------------------------------------------
class TokenStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, tokens):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)   # best-effort: not world-readable
        except OSError:
            pass


def _basic_auth_header(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _store_response(store, resp, now):
    """Persist a token response, ALWAYS keeping the latest (rotated) refresh
    token and recording absolute expiry timestamps."""
    existing = store.load()
    refresh = resp.get("refresh_token", existing.get("refresh_token"))
    tokens = {
        "access_token": resp["access_token"],
        "refresh_token": refresh,
        # absolute expiries (epoch seconds) so we never trust a stale relative value
        "access_token_expires_at": now + int(resp.get("expires_in", 3600)),
        "refresh_token_expires_at": now + int(resp.get("x_refresh_token_expires_in", 8640000)),
        "token_type": resp.get("token_type", "bearer"),
        "obtained_at": now,
    }
    store.save(tokens)
    return tokens


# --------------------------------------------------------------------------
# Authorization-code flow (initial consent) + refresh
# --------------------------------------------------------------------------
def authorize_url(config, state, http_get=_http_get_json):
    """Build the consent URL the user opens once to authorize the sandbox realm."""
    auth_endpoint = discovery(config, http_get)["authorization_endpoint"]
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "scope": SCOPE_ACCOUNTING,
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    return f"{auth_endpoint}?{urllib.parse.urlencode(params)}"


def exchange_code(config, code, realm_id, now=None, http_get=_http_get_json, http_post=_http_post_form):
    """Exchange an authorization code for the first token pair and persist it."""
    config.require_app_credentials()
    now = int(time.time()) if now is None else now
    token_endpoint = discovery(config, http_get)["token_endpoint"]
    resp = http_post(
        token_endpoint,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": config.redirect_uri},
        {"Authorization": _basic_auth_header(config.client_id, config.client_secret),
         "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    if "access_token" not in resp:
        raise QBOAuthError(f"authorization_code exchange failed: {resp}")
    store = TokenStore(config.token_store_path)
    tokens = _store_response(store, resp, now)
    tokens["realm_id"] = realm_id
    return tokens


def refresh_tokens(config, store=None, now=None, http_get=_http_get_json, http_post=_http_post_form):
    """Refresh the access token. Persists the rotated refresh token + new expiry."""
    config.require_app_credentials()
    now = int(time.time()) if now is None else now
    store = store or TokenStore(config.token_store_path)
    refresh_token = store.load().get("refresh_token") or config.refresh_token
    if not refresh_token:
        raise QBOAuthError(
            "no refresh_token in the token store or QBO_REFRESH_TOKEN; run the "
            "authorization-code flow once (see sources/README.md)")
    token_endpoint = discovery(config, http_get)["token_endpoint"]
    resp = http_post(
        token_endpoint,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        {"Authorization": _basic_auth_header(config.client_id, config.client_secret),
         "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    if "access_token" not in resp:
        raise QBOAuthError(f"refresh failed: {resp}")
    return _store_response(store, resp, now)


def valid_access_token(config, store=None, now=None, http_get=_http_get_json, http_post=_http_post_form):
    """Return a non-expired access token, refreshing if needed."""
    now = int(time.time()) if now is None else now
    store = store or TokenStore(config.token_store_path)
    tokens = store.load()
    if tokens.get("access_token") and now < tokens.get("access_token_expires_at", 0) - ACCESS_TOKEN_SKEW_SECONDS:
        return tokens["access_token"]
    return refresh_tokens(config, store, now=now, http_get=http_get, http_post=http_post)["access_token"]
