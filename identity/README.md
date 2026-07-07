# identity/ - authenticated identity, RBAC, segregation of duties

Maker-checker roles used to be logical names in code ("Tax Manager signs tax").
This module binds them to **authenticated human identities**, so an approval is
attributable to a person, enforced by role, and can never be self-approved.

```
token ──► IdentityProvider.authenticate() ──► Identity(subject, name, roles)
              (LocalDevIdentity | OidcProvider)          │
                                                         ├─ require_role()      RBAC
                                                         └─ assert_distinct()   segregation of duties
```

## Modules

| File | Role |
|---|---|
| `providers.py` | `IdentityProvider` interface + `LocalDevIdentity` (offline, default) + `OidcProvider` (Auth0 / Entra ID / Cognito - config, not code; RS256/JWKS is a stub seam). Selected by `IDENTITY_PROVIDER`. |
| `tokens.py` | LocalDevIdentity token mint/verify - a minimal HS256 JWS so the whole system authenticates offline. Verifies a list of keys (signing-key rotation window). |
| `access.py` | `Identity`, `authenticate`, `require_role`, `authorize`, `assert_distinct` (SoD), and the exceptions (`Unauthenticated`, `Unauthorized`, `SegregationOfDutiesError`). |
| `directory.py` | The registered human identities for local dev / demo (subject → name, email, roles). Roles use the exact reviewer-role strings from `cfo-office/review.py`. |
| `signoff.py` | `record_signoff(...)` - the one place approvals go through: authenticate → RBAC → SoD → write **subject id + display name** to the governance audit trail. |

## Provider-agnostic OIDC (Auth0 / Entra ID / Cognito are configuration)

`OidcProvider` reads everything provider-specific from the `SecretsProvider`
(`config/secrets.py`), so switching IdP is config, not code:

| Config (via SecretsProvider) | Meaning |
|---|---|
| `OIDC_ISSUER` | the IdP issuer (`https://acme.auth0.com/`, an Entra tenant, a Cognito pool) |
| `OIDC_CLIENT_ID` | the expected audience |
| `OIDC_JWKS_URI` | where the signing keys are published |
| `OIDC_ROLES_CLAIM` | which claim carries roles/groups (default `roles`) |

`authenticate_claims()` - issuer / audience / expiry validation and role-claim
mapping - **is** implemented and unit-tested. The signature-verification seam
(`_verify_signature`: fetch JWKS, verify RS256) raises `NotImplementedError` until
a real JWKS/RS256 verifier (e.g. PyJWT) is wired in. **Honest boundary:** offline
dev/demo/tests use `IDENTITY_PROVIDER=local`; production points at a real IdP.

## What it enforces (proved by tests)

- **Unauthenticated** approval calls fail (`Unauthenticated`).
- **Wrong-role** approvals fail (`Unauthorized`); the wrong-role call is **not**
  recorded as an approval.
- **Maker == checker / proposer == approver** is rejected (`SegregationOfDutiesError`).
- Every sign-off in the audit trail carries the **authenticated identity**
  (`subject (name)`), not just the role - see `record_signoff` and the
  identity-bound path in `cfo-office/review.py` (`review(..., token=...)`).

## Where it plugs in

- **Payment approval gate** (`payments/`) calls `record_signoff("payment", ...)`
  with the payment's required approver role and the proposer subject (SoD).
- **First-line close sign-off** (`cfo-office/review.py`) accepts an optional
  `token=`; when supplied, the sign-off is identity-bound. Without a token the
  behaviour is unchanged (role-only, auto in CI) - so the existing offline
  suites and the demo are unaffected.

## Tests

```bash
python identity/tests/run_tests.py     # offline, LocalDevIdentity, no external IdP
```
