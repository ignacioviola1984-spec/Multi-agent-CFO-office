"""
sources/events/signing.py - Per-source HMAC signature verification for webhooks.

Wallet / payment providers sign each webhook with a shared secret (HMAC-SHA256
over the raw request body); the receiver must reject anything unsigned or
mis-signed. The secret is per source and is read through the SecretsProvider
(config/secrets.py), never hardcoded:

    WEBHOOK_HMAC_<SOURCE>          the current secret
    WEBHOOK_HMAC_<SOURCE>_NEXT     the next secret, present only during rotation

Verification accepts a signature valid under EITHER configured secret, which is
the dual-secret acceptance window that lets an HMAC secret rotate with zero missed
events (config/ROTATION.md). Every comparison is constant-time
(hmac.compare_digest), so rotation opens no timing side channel.
"""

import hashlib
import hmac
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets  # noqa: E402

SIG_PREFIX = "sha256="


def _env_name(source):
    return "WEBHOOK_HMAC_" + "".join(
        c.upper() if (c.isalnum()) else "_" for c in source)


def source_secrets(source):
    """The active secrets for a source: the current one plus an optional _NEXT
    rotation partner. Read through the SecretsProvider."""
    name = _env_name(source)
    out = []
    cur = appsecrets.get_secret(name)
    nxt = appsecrets.get_secret(name + "_NEXT")
    if cur:
        out.append(cur)
    if nxt:
        out.append(nxt)
    return out


def compute_signature(secret, body):
    """The signature a sender computes: sha256=<hex hmac of the raw body>."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return SIG_PREFIX + mac


def verify_signature(source, body, signature_header):
    """True iff `signature_header` matches the body under ANY configured secret for
    `source`. False if no secret is configured, the header is missing/empty, or no
    secret matches. Constant-time throughout."""
    if not signature_header:
        return False
    secrets_list = source_secrets(source)
    if not secrets_list:
        return False  # fail closed: an unconfigured source cannot be verified
    for secret in secrets_list:
        expected = compute_signature(secret, body)
        if hmac.compare_digest(signature_header.strip(), expected):
            return True
    return False
