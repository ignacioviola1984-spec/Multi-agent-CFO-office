"""
identity/tokens.py - LocalDevIdentity token minting and verification (offline).

A minimal, dependency-free JWS in the JWT shape (HS256): three base64url segments
`header.payload.signature`, signed with an HMAC-SHA256 key. This is ONLY the
local-dev / test / demo identity provider, so the whole system authenticates
offline with no external IdP and no API key. In a real deployment the OIDC
provider (identity/providers.py: OidcProvider) verifies RS256 tokens from Auth0 /
Entra ID / Cognito instead; the claims shape (sub, name, roles, email) is the
same, so nothing downstream changes.

The signing key comes from the SecretsProvider (LOCAL_IDENTITY_SIGNING_KEY). A
deterministic fallback is used only when no key is configured, so tests and the
demo run offline; that fallback is clearly not a production secret.

Verification accepts a LIST of keys so a signing key can rotate with a dual-key
window, the same pattern as the webhook HMAC secret (config/ROTATION.md).
Constant-time comparison (hmac.compare_digest) guards every candidate.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets  # noqa: E402

ALG = "HS256"
ISSUER = "local-dev-identity"
# Offline fallback ONLY. Never a production secret; a real deployment sets
# LOCAL_IDENTITY_SIGNING_KEY (local dev) or uses the OIDC provider (production).
_DEV_FALLBACK_KEY = "local-dev-identity-signing-key-not-secret"


class TokenError(Exception):
    """Malformed, mis-signed, or expired token."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def signing_keys():
    """The keys used to sign/verify local tokens: the current signing key plus an
    optional _NEXT rotation partner. Falls back to a clearly-non-secret dev key
    when none is configured, so offline runs work."""
    keys = []
    cur = appsecrets.get_secret("LOCAL_IDENTITY_SIGNING_KEY")
    nxt = appsecrets.get_secret("LOCAL_IDENTITY_SIGNING_KEY_NEXT")
    if cur:
        keys.append(cur)
    if nxt:
        keys.append(nxt)
    if not keys:
        keys.append(_DEV_FALLBACK_KEY)
    return keys


def _sign(signing_input: bytes, key: str) -> str:
    mac = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return _b64url(mac)


def mint(sub, name, roles, email=None, ttl_seconds=3600, key=None, extra=None, now=None):
    """Mint a signed local token for a subject. Used by the demo and tests to
    stand in for an IdP-issued token."""
    now = int(now if now is not None else time.time())
    header = {"alg": ALG, "typ": "JWT"}
    payload = {
        "iss": ISSUER,
        "sub": sub,
        "name": name,
        "roles": list(roles),
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    if email:
        payload["email"] = email
    if extra:
        payload.update(extra)
    key = key or signing_keys()[0]
    seg = _b64url(json.dumps(header, sort_keys=True).encode("utf-8")).encode() + b"." + \
        _b64url(json.dumps(payload, sort_keys=True).encode("utf-8")).encode()
    return seg.decode("ascii") + "." + _sign(seg, key)


def verify(token, keys=None, now=None, leeway=0):
    """Verify a local token and return its claims dict. Raises TokenError on any
    structural, signature, or expiry problem. Tries every configured key so a
    signing-key rotation does not reject valid tokens."""
    if not token or token.count(".") != 2:
        raise TokenError("malformed token")
    h_seg, p_seg, sig = token.split(".")
    signing_input = (h_seg + "." + p_seg).encode("ascii")
    candidate_keys = keys or signing_keys()
    if not any(hmac.compare_digest(sig, _sign(signing_input, k)) for k in candidate_keys):
        raise TokenError("bad signature")
    try:
        header = json.loads(_b64url_decode(h_seg))
        claims = json.loads(_b64url_decode(p_seg))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("undecodable token segments") from exc
    if header.get("alg") != ALG:
        raise TokenError(f"unexpected alg {header.get('alg')!r}")
    now = int(now if now is not None else time.time())
    if "exp" in claims and now > int(claims["exp"]) + int(leeway):
        raise TokenError("token expired")
    return claims
