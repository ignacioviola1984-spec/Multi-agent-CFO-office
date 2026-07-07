"""
governance/audit.py - The append-only, timestamped governance audit trail shared
by the governed write-path modules (payments/, identity/, sources/events/).

It follows the same principle as the rest of the repo (cfo-office/shared_state.py
CFOContext.audit, self-improvement/audit.py): every state transition and every
sign-off is recorded with WHO (actor), WHEN (timestamp), WHAT (action) and WHY
(reason). Two properties make it trustworthy:

  * Append-only. The file is opened in "a" mode only. This module exposes no
    update and no delete; there is no code path that rewrites or removes an entry,
    so the trail cannot be quietly edited through here.
  * Redaction-aware. Every entry is passed through config.secrets.redact_obj
    before it is written, so a secret value (an HMAC key, an OAuth client secret,
    an API key) can never land in the trail even if a caller accidentally includes
    one. This is the audit half of "secrets never appear in audit entries".

The default trail lives at governance/audit_trail.jsonl (gitignored). The path is
overridable per call or via GOVERNANCE_AUDIT_LOG, so tests and the end-to-end
demo can direct it at an isolated file.
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import secrets as appsecrets  # noqa: E402

DEFAULT_PATH = os.path.join(HERE, "audit_trail.jsonl")


def audit_path(path=None):
    return path or os.environ.get("GOVERNANCE_AUDIT_LOG") or DEFAULT_PATH


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def record(action, actor, reason, path=None, **fields):
    """Append one governance event. Returns the event dict that was written.

    action  - the state transition or decision (e.g. "payment.proposed",
              "payment.executed", "signoff.approved", "webhook.rejected").
    actor   - who caused it: an authenticated identity subject, an agent name, or
              a system component. Never a role name alone once identity/ is wired.
    reason  - human-readable why / detail.
    fields  - any structured context (ids, amounts, hashes). Redacted before write.
    """
    evt = {"ts": now_iso(), "action": action, "actor": actor, "reason": reason}
    evt.update(fields)
    evt = appsecrets.redact_obj(evt)  # scrub any secret value before persisting
    p = audit_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False, sort_keys=True) + "\n")
    return evt


def read_all(path=None):
    p = audit_path(path)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def entries_where(path=None, **match):
    """Return entries whose fields equal every key=value in `match`."""
    return [e for e in read_all(path) if all(e.get(k) == v for k, v in match.items())]
