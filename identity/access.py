"""
identity/access.py - Authenticated identity, RBAC, and segregation of duties.

This is the layer that turns the maker-checker roles (which were logical names in
code) into checks bound to an AUTHENTICATED human identity. Three guarantees:

  1. Authentication. An approval action must present a valid token; an
     unauthenticated call fails (Unauthenticated). Tokens are verified by the
     configured identity provider (identity/providers.py): LocalDevIdentity offline,
     a real OIDC provider (Auth0 / Entra ID / Cognito) in production.

  2. RBAC. The authenticated identity must hold the role registered as owner for
     the item being approved (a close function's checker role, a payment's approver
     role). Wrong role => Unauthorized.

  3. Segregation of duties, in code. The same identity cannot be maker and checker
     on the same item, and cannot approve a payment it proposed
     (SegregationOfDutiesError).

`Identity` carries the subject id and display name, which every downstream
sign-off records in the audit trail -- no longer just a role name.
"""

from dataclasses import dataclass, field


class IdentityError(Exception):
    """Base for identity/authorization failures."""


class Unauthenticated(IdentityError):
    """No valid authenticated identity was presented."""


class Unauthorized(IdentityError):
    """Authenticated, but the identity lacks the required role."""


class SegregationOfDutiesError(IdentityError):
    """The same identity would occupy two incompatible duties on one item."""


@dataclass(frozen=True)
class Identity:
    """An authenticated principal. `roles` is a tuple so the identity is immutable
    and hashable."""
    subject: str
    name: str
    roles: tuple = field(default_factory=tuple)
    email: str = ""
    issuer: str = ""

    @classmethod
    def from_claims(cls, claims):
        sub = claims.get("sub")
        if not sub:
            raise Unauthenticated("token has no subject (sub) claim")
        roles = claims.get("roles") or claims.get("groups") or []
        if isinstance(roles, str):
            roles = [roles]
        return cls(subject=sub, name=claims.get("name") or sub,
                   roles=tuple(roles), email=claims.get("email", ""),
                   issuer=claims.get("iss", ""))

    def has_role(self, role):
        return role in self.roles

    def audit_actor(self):
        """The actor string recorded in the audit trail: subject id + display
        name, so a sign-off is attributable to a person, not just a role."""
        return f"{self.subject} ({self.name})"


def authenticate(token, provider=None):
    """Verify a token via the identity provider and return an Identity.

    Accepts a token string, an already-authenticated Identity (passthrough), or
    None/empty (-> Unauthenticated). Never trusts a raw claims dict from an
    untrusted caller; claims only come from a provider-verified token."""
    if isinstance(token, Identity):
        return token
    if not token:
        raise Unauthenticated("no token presented")
    if provider is None:
        from identity import providers
        provider = providers.get_provider()
    return provider.authenticate(token)


def require_role(identity, roles):
    """Raise Unauthorized unless `identity` holds one of `roles` (str or iterable)."""
    if isinstance(roles, str):
        roles = (roles,)
    if not any(identity.has_role(r) for r in roles):
        raise Unauthorized(
            f"identity {identity.subject!r} holds {list(identity.roles)}, "
            f"needs one of {list(roles)}")
    return identity


def authorize(token, roles, provider=None):
    """Authenticate then check role in one step. Returns the Identity."""
    return require_role(authenticate(token, provider), roles)


def assert_distinct(proposer_subject, approver_subject, item="item", relation="maker/checker"):
    """Segregation of duties: the two subjects on one item must differ. Used for
    maker-vs-checker on a close item and proposer-vs-approver on a payment."""
    if proposer_subject and approver_subject and proposer_subject == approver_subject:
        raise SegregationOfDutiesError(
            f"segregation of duties: {proposer_subject!r} cannot be both sides of "
            f"the {relation} control on {item!r}")
