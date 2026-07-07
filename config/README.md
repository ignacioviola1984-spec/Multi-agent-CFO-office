# config/ - secrets management

Once a governed **write** path exists ([`payments/`](../payments/README.md)),
secrets sitting in a plaintext `.env` are no longer acceptable. This module puts a
single interface - `SecretsProvider` - in front of every named secret the system
reads, so the storage backend is **configuration, not code**.

```
call site ──get_secret("ANTHROPIC_API_KEY")──► SecretsProvider ──► EnvFileProvider (.env, default)
                                                              └──► VaultProvider  (cloud KMS, stub)
                                        selected by SECRETS_PROVIDER
```

## The interface

`config/secrets.py` (imported as `from config import secrets as appsecrets`, so it
never shadows Python's stdlib `secrets`):

| Symbol | Role |
|---|---|
| `SecretsProvider` | ABC: `get_secret(name, default=None)` and `get_required(name)`. `__repr__` never carries a value. |
| `EnvFileProvider` | **Default.** Process environment + the repo-root `.env` (loaded once, never overriding the real env). Identical to the prior behaviour - nothing to configure for local dev / demo / CI. |
| `VaultProvider` | **Stub** with the exact shape a cloud secret manager implements. Talks to no real backend; `_fetch` is the one network seam and raises until a client is injected. |
| `get_provider()` / `get_secret()` / `get_required()` | Module-level convenience over the selected provider. |
| `load_env(path=None)` | Bootstrap the process env from `.env` **through the provider** (replaces scattered `load_dotenv(...)`). |
| `redact()` / `redact_obj()` | Mask known secret values in strings / nested structures before anything is written. |

## Selecting a provider (no code change)

```bash
# Local dev / demo / CI (default): .env + process env
SECRETS_PROVIDER=env        # or unset

# Cloud secret manager (stub interface today)
SECRETS_PROVIDER=vault
```

`env`, `envfile`, `dotenv`, `local` → `EnvFileProvider`.
`vault`, `kms`, `secretsmanager`, `aws`, `gcp` → `VaultProvider`.

## Wiring a real secret manager (what the stub leaves out)

`VaultProvider` is deliberately **not** a working integration - the same honest
boundary as `SandboxRail` vs a real bank rail in `payments/`. Its `_fetch` raises
`NotImplementedError` until a real client is provided. To make it live you inject
any object exposing `get_secret_value(name) -> str`:

```python
import boto3
from config import secrets as appsecrets

class AwsSecretsManager:
    def __init__(self):
        self._c = boto3.client("secretsmanager")
    def get_secret_value(self, name):
        return self._c.get_secret_value(SecretId=name)["SecretString"]

# In production wiring (not committed here):
appsecrets._provider = appsecrets.VaultProvider(backend=AwsSecretsManager())
```

GCP Secret Manager (`google-cloud-secret-manager`) and HashiCorp Vault (`hvac`)
plug in the same way. No call site changes - they only ever see `get_secret(...)`.

## What now goes through the provider

Every place that reads a **named secret value** was migrated off raw `os.environ`:

- **Anthropic API key** - the CFO-office agents and `webapp/` bootstrap the env via
  `appsecrets.load_env(...)`, so the key is surfaced by the provider.
- **QuickBooks OAuth** - `QBO_CLIENT_ID / QBO_CLIENT_SECRET / QBO_REFRESH_TOKEN`
  (`sources/quickbooks/oauth.py`).
- **ERPNext auth** - `ERPNEXT_API_KEY / ERPNEXT_API_SECRET`
  (`sources/erpnext/auth.py`).
- **OIDC client config** - `OIDC_CLIENT_ID / OIDC_CLIENT_SECRET` and the local-dev
  signing key (`identity/`).
- **Webhook HMAC secrets** - `WEBHOOK_HMAC_<SOURCE>` and the rotation partner
  `WEBHOOK_HMAC_<SOURCE>_NEXT` (`sources/events/`).

## Secrets never appear in artifacts

`redact()` masks any known secret value inside a string; `redact_obj()` recurses
through dicts/lists. The **governance audit trail** ([`governance/audit.py`](../governance/audit.py))
runs every entry through `redact_obj` before writing, so a secret cannot reach the
trail even if a caller passes one by mistake. The event store
([`sources/events/`](../sources/events/README.md)) does the same for stored
payloads. `config/tests/test_secrets.py` proves a generated artifact never
contains a known test-secret value.

## Rotation

Webhook HMAC secrets rotate with a **dual-secret acceptance window** so no event
is missed mid-rotation. Full runbook: [`ROTATION.md`](ROTATION.md).

## Tests

```bash
python config/tests/run_tests.py     # offline, deterministic, no real secret manager
```
