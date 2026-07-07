"""
config/secrets.py - Secrets management: a provider interface with a local-dev
default and a cloud secret-manager stub, selected by environment variable so
switching backends is configuration, not a code change.

Once a governed WRITE path exists (payments/), secrets sitting in a plaintext
`.env` are no longer acceptable. Every named secret the system needs -- the
Anthropic API key, the QuickBooks / ERPNext OAuth client secrets, the OIDC client
config (identity/), and the per-source webhook HMAC secrets (sources/events/) --
is retrieved through `SecretsProvider`, never by reaching into `os.environ` at the
call site.

Two implementations ship:

  * EnvFileProvider  - the CURRENT behaviour (process environment + the repo-root
                       `.env`). This is the default, for local dev / demo / CI:
                       nothing to configure, identical to before.
  * VaultProvider    - a STUB with the exact shape a cloud secret manager (AWS
                       Secrets Manager, GCP Secret Manager, HashiCorp Vault) would
                       implement. It talks to NO real backend; the network fetch is
                       a single seam (`_fetch`) that raises until a real client is
                       injected. This mirrors payments/ `SandboxRail` vs the
                       real-rail stub: the interface is real, the backend is
                       explicitly not implemented here.

Selection is by `SECRETS_PROVIDER` ("env" default, "vault"). No code changes to
switch; the call sites only ever see `SecretsProvider`.

Redaction. Secrets must never appear in logs, audit entries, error messages, or
snapshots. `redact()` masks any known secret value inside a string before it is
written anywhere; the governance audit trail (governance/audit.py) runs every
entry through it. Providers never print or log a secret value, and `__repr__`
carries only the provider name, never a value.

Rotation. Webhook HMAC secrets rotate with a dual-secret acceptance window (a
current secret plus an optional `_NEXT`), so no event is missed mid-rotation. See
config/ROTATION.md.
"""

import abc
import os
import threading

DEFAULT_PROVIDER = "env"
PROVIDER_ENV = "SECRETS_PROVIDER"

# The named secrets the system knows about. Used to (a) drive redaction and (b)
# let callers assert a secret is present. Per-source webhook HMAC secrets are
# dynamic (WEBHOOK_HMAC_<SOURCE> and its _NEXT rotation partner) and are picked up
# by prefix, so they are not enumerated here.
KNOWN_SECRET_NAMES = (
    "ANTHROPIC_API_KEY",
    "QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REFRESH_TOKEN",
    "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET",
    "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
    "LOCAL_IDENTITY_SIGNING_KEY",
)

# Dynamic secret names are matched by these prefixes (webhook HMAC per source).
SECRET_NAME_PREFIXES = ("WEBHOOK_HMAC_",)

MASK = "***REDACTED***"


class SecretNotFound(Exception):
    """Raised when a required secret is absent."""


class SecretsProvider(abc.ABC):
    """The single interface every secret read goes through."""

    name = "abstract"

    @abc.abstractmethod
    def get_secret(self, name, default=None):
        """Return the secret value for `name`, or `default` if not set.

        MUST NOT log, print, or raise the value. Return `default` (not raise) when
        a secret is simply absent, so optional secrets (offline demo has no
        Anthropic key) do not blow up import."""

    def get_required(self, name):
        """Return the secret or raise SecretNotFound. The exception message names
        the secret, never its value."""
        v = self.get_secret(name)
        if not v:
            raise SecretNotFound(
                f"required secret {name!r} is not set (provider={self.name!r})")
        return v

    def __repr__(self):  # never leak a value through repr
        return f"<{type(self).__name__} name={self.name!r}>"


# --------------------------------------------------------------------------
# EnvFileProvider - the default. Process env + repo-root .env, exactly as today.
# --------------------------------------------------------------------------
def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class EnvFileProvider(SecretsProvider):
    """Reads secrets from the process environment, after loading the repo-root
    `.env` once (the pre-existing behaviour). This is intentionally the same
    mechanism the code used before; the change is that reads flow through this
    interface instead of scattered `os.environ.get` calls."""

    name = "env"

    def __init__(self, dotenv_path=None, autoload=True):
        self._dotenv_path = dotenv_path or os.path.join(_repo_root(), ".env")
        self._loaded = False
        if autoload:
            self.load_env()

    def load_env(self, path=None):
        """Load a `.env` file into the process environment (idempotent; never
        overrides a value already set in the real environment). Uses python-dotenv
        when available, else a minimal stdlib parser so this works offline."""
        path = path or self._dotenv_path
        if not path or not os.path.exists(path):
            self._loaded = True
            return self
        try:
            from dotenv import load_dotenv
            load_dotenv(path, override=False)
        except Exception:
            self._parse_env_file(path)
        self._loaded = True
        return self

    @staticmethod
    def _parse_env_file(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)  # never override the real env

    def get_secret(self, name, default=None):
        return os.environ.get(name, default)


# --------------------------------------------------------------------------
# VaultProvider - the cloud secret-manager STUB. Real interface, no real backend.
# --------------------------------------------------------------------------
class VaultProvider(SecretsProvider):
    """The exact shape a cloud KMS / secret manager would implement (AWS Secrets
    Manager, GCP Secret Manager, HashiCorp Vault). It does NOT ship a real
    backend: `_fetch` is the single network seam and raises NotImplementedError
    until a real client is wired in.

    A client can be injected (`backend=`) for testing the interface end to end
    without a real vault -- any object exposing `get_secret_value(name) -> str`.
    That is how the tests exercise the contract; production would inject a boto3 /
    google-cloud-secret-manager / hvac client instead. The seam, not a mock, is
    the deliverable, exactly like SandboxRail vs the real-rail stub in payments/."""

    name = "vault"

    def __init__(self, backend=None, prefix=None, cache=True):
        self._backend = backend
        self._prefix = prefix if prefix is not None else os.environ.get("SECRETS_VAULT_PREFIX", "")
        self._cache = {} if cache else None

    def _fetch(self, name):
        """The single seam a real secret manager plugs into. Left unimplemented on
        purpose so this cannot be mistaken for a working vault integration."""
        if self._backend is None:
            raise NotImplementedError(
                "VaultProvider is a stub: it does not talk to a real secret "
                "manager. Wire a client (AWS Secrets Manager, GCP Secret Manager, "
                "or HashiCorp Vault) by passing backend=<client exposing "
                "get_secret_value(name) -> str>, or replace this method. See "
                "config/README.md.")
        return self._backend.get_secret_value(self._prefix + name)

    def get_secret(self, name, default=None):
        if self._cache is not None and name in self._cache:
            return self._cache[name]
        try:
            value = self._fetch(name)
        except NotImplementedError:
            raise  # a stub misconfiguration must be loud, not swallowed
        except Exception:
            return default  # a genuine "not found" from the backend
        if value is None:
            return default
        if self._cache is not None:
            self._cache[name] = value
        return value


# --------------------------------------------------------------------------
# Selection + module-level convenience API.
# --------------------------------------------------------------------------
_PROVIDERS = {
    "env": EnvFileProvider, "envfile": EnvFileProvider, "dotenv": EnvFileProvider,
    "local": EnvFileProvider,
    "vault": VaultProvider, "kms": VaultProvider, "secretsmanager": VaultProvider,
    "aws": VaultProvider, "gcp": VaultProvider,
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
    """Return the process-wide provider selected by SECRETS_PROVIDER (cached)."""
    global _provider
    with _lock:
        if _provider is None:
            _provider = _build(os.environ.get(PROVIDER_ENV, DEFAULT_PROVIDER))
        return _provider


def reset_provider():
    """Drop the cached provider (tests toggle SECRETS_PROVIDER between cases)."""
    global _provider
    with _lock:
        _provider = None


def get_secret(name, default=None):
    return get_provider().get_secret(name, default)


def get_required(name):
    return get_provider().get_required(name)


def load_env(path=None):
    """Bootstrap the process environment from the repo-root `.env` THROUGH the
    provider (replaces scattered `load_dotenv(...)` calls). A no-op for providers
    that do not use a dotenv file."""
    p = get_provider()
    if isinstance(p, EnvFileProvider):
        p.load_env(path)
    return p


# --------------------------------------------------------------------------
# Redaction - secrets must never reach a log, audit entry, error, or snapshot.
# --------------------------------------------------------------------------
def _secret_values(provider=None):
    """The set of live secret values to scrub. Cheap and defensive: pulls the
    known names plus any dynamic (prefixed) secret found in the environment."""
    provider = provider or get_provider()
    values = set()
    for n in KNOWN_SECRET_NAMES:
        try:
            v = provider.get_secret(n)
        except Exception:
            v = None
        if v:
            values.add(str(v))
    for k, v in os.environ.items():
        if v and any(k.startswith(pfx) for pfx in SECRET_NAME_PREFIXES):
            values.add(str(v))
    # Never treat a trivially short value as a secret to scrub (avoids masking
    # incidental substrings and does not weaken real secrets, which are long).
    return {v for v in values if len(v) >= 8}


def redact(text, provider=None, mask=MASK):
    """Return `text` with every known secret value replaced by `mask`. Longest
    values first, so a secret that contains another is fully masked."""
    s = text if isinstance(text, str) else str(text)
    for v in sorted(_secret_values(provider), key=len, reverse=True):
        if v in s:
            s = s.replace(v, mask)
    return s


def redact_obj(obj, provider=None, mask=MASK):
    """Recursively redact secret values inside dict/list/tuple/str structures
    (used before an event payload or audit detail is persisted)."""
    if isinstance(obj, str):
        return redact(obj, provider, mask)
    if isinstance(obj, dict):
        return {k: redact_obj(v, provider, mask) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_obj(v, provider, mask) for v in obj)
    return obj
