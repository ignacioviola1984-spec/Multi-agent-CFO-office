"""
identity/providers.py - Provider-agnostic OIDC client.

`IdentityProvider.authenticate(token) -> Identity` is the whole contract the rest
of the system codes against. Two implementations ship:

  * LocalDevIdentity - DEFAULT. Verifies the local HS256 tokens (identity/tokens.py)
    so tests and the demo authenticate offline with no external IdP. It can also
    mint tokens for registered directory subjects (what the demo uses to "log in"
    a treasurer / controller).

  * OidcProvider - the real path, provider-AGNOSTIC: Auth0, Entra ID and Cognito
    are all standard OIDC, so they are CONFIG (issuer, client/audience, JWKS URI,
    roles claim), not code. Signature verification against the IdP's JWKS (RS256)
    is a single seam (`_verify_signature`) left unimplemented here on purpose -- it
    needs a live IdP and a JWKS/RS256 verifier (e.g. PyJWT). The claim VALIDATION
    and role-mapping logic (issuer, audience, expiry, roles claim) IS implemented
    and unit-tested via `authenticate_claims`, so the RBAC contract is proven even
    though the crypto/network half is stubbed. This mirrors payments/ SandboxRail
    vs the real-rail stub.

Selection is by IDENTITY_PROVIDER ("local" default; "oidc"/"auth0"/"entra"/
"cognito" -> OidcProvider). Provider config (issuer, client id/secret) is read
through the SecretsProvider (config/secrets.py), so it is never hardcoded.
"""

import abc
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets  # noqa: E402
from identity import tokens  # noqa: E402
from identity.access import Identity, Unauthenticated, Unauthorized  # noqa: E402

DEFAULT_PROVIDER = "local"
PROVIDER_ENV = "IDENTITY_PROVIDER"


class IdentityProvider(abc.ABC):
    name = "abstract"

    @abc.abstractmethod
    def authenticate(self, token):
        """Verify a token and return an Identity, or raise Unauthenticated."""


class LocalDevIdentity(IdentityProvider):
    """Offline provider: verifies locally-signed HS256 tokens."""

    name = "local"

    def authenticate(self, token):
        try:
            claims = tokens.verify(token)
        except tokens.TokenError as exc:
            raise Unauthenticated(f"invalid local token: {exc}") from exc
        return Identity.from_claims(claims)

    def mint_token(self, subject, roles=None, name=None, email=None, ttl_seconds=3600):
        """Mint a token for a registered directory subject (demo/test 'login').

        Roles/name/email default to the directory entry, so the caller cannot
        silently grant itself a role the directory does not record."""
        from identity import directory
        person = directory.get(subject)
        if person is None and roles is None:
            raise Unauthenticated(f"unknown subject {subject!r} and no roles supplied")
        roles = roles if roles is not None else person["roles"]
        name = name or (person["name"] if person else subject)
        email = email or (person["email"] if person else "")
        return tokens.mint(subject, name, roles, email=email, ttl_seconds=ttl_seconds)


class OidcProvider(IdentityProvider):
    """Real OIDC path (Auth0 / Entra ID / Cognito). Config-driven; signature
    verification against the IdP JWKS is a stub seam."""

    name = "oidc"

    def __init__(self):
        # All provider-specific values are CONFIG, read via the SecretsProvider.
        self.issuer = appsecrets.get_secret("OIDC_ISSUER", "")
        self.audience = appsecrets.get_secret("OIDC_CLIENT_ID", "")  # aud == client id
        self.jwks_uri = appsecrets.get_secret("OIDC_JWKS_URI", "")
        self.roles_claim = appsecrets.get_secret("OIDC_ROLES_CLAIM", "roles")

    def _verify_signature(self, token):
        """The one network/crypto seam: fetch the IdP JWKS and verify the RS256
        signature. Left unimplemented so this is never mistaken for a working IdP
        integration; wire a JWKS client + RS256 verifier (e.g. PyJWT) here."""
        raise NotImplementedError(
            "OidcProvider is a stub: it does not verify RS256 tokens against a live "
            "IdP JWKS. Wire a JWKS client + RS256 verifier (e.g. PyJWT) into "
            "_verify_signature, or run with IDENTITY_PROVIDER=local for offline "
            "dev/demo/tests. See identity/README.md.")

    def authenticate(self, token):
        # In production: claims = self._verify_signature(token) then validate.
        claims = self._verify_signature(token)  # raises until wired to a real IdP
        return self.authenticate_claims(claims)

    def authenticate_claims(self, claims, now=None):
        """Validate ALREADY-verified claims and map them to an Identity. This is
        the deterministic, testable half: issuer, audience, expiry, and the
        configurable roles claim. (Signature verification happens before this.)"""
        if self.issuer and claims.get("iss") != self.issuer:
            raise Unauthenticated(f"issuer mismatch: {claims.get('iss')!r}")
        if self.audience:
            aud = claims.get("aud")
            aud_ok = (aud == self.audience) or (isinstance(aud, (list, tuple)) and self.audience in aud)
            if not aud_ok:
                raise Unauthenticated("audience mismatch")
        now = int(now if now is not None else time.time())
        if "exp" in claims and now > int(claims["exp"]):
            raise Unauthenticated("token expired")
        # Normalize the configurable roles claim into the standard `roles` field.
        mapped = dict(claims)
        if self.roles_claim != "roles" and self.roles_claim in claims:
            mapped["roles"] = claims[self.roles_claim]
        return Identity.from_claims(mapped)


_PROVIDERS = {
    "local": LocalDevIdentity, "localdev": LocalDevIdentity, "dev": LocalDevIdentity,
    "oidc": OidcProvider, "auth0": OidcProvider, "entra": OidcProvider,
    "azuread": OidcProvider, "cognito": OidcProvider,
}

_lock = threading.Lock()
_provider = None


def _build(kind):
    kind = (kind or DEFAULT_PROVIDER).strip().lower()
    cls = _PROVIDERS.get(kind)
    if cls is None:
        raise ValueError(
            f"unknown {PROVIDER_ENV}={kind!r}; expected one of {sorted(set(_PROVIDERS))}")
    return cls()


def get_provider():
    global _provider
    with _lock:
        if _provider is None:
            _provider = _build(os.environ.get(PROVIDER_ENV, DEFAULT_PROVIDER))
        return _provider


def reset_provider():
    global _provider
    with _lock:
        _provider = None
