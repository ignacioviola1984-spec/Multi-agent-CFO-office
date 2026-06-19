"""
audit.py - Append-only audit trail for the self-improvement system.

Mirrors the governance principle of the CFO office (cfo-office/shared_state.py,
CFOContext.audit): every proposal and every decision is recorded with who, what,
and when. The file is opened in APPEND mode only; this module never rewrites or
deletes an existing entry, so the trail cannot be quietly edited through here.
"""

import datetime
import json
import os

import registry


def _path(sd=None):
    sd = registry.state_dir(sd)
    os.makedirs(sd, exist_ok=True)
    return os.path.join(sd, "audit_trail.jsonl")


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def record(action, detail, sd=None, **fields):
    """Append one event. Returns the event dict that was written."""
    evt = {"ts": now_iso(), "action": action, "detail": detail}
    evt.update(fields)
    with open(_path(sd), "a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    return evt


def read_all(sd=None):
    p = _path(sd)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def entries_for(proposal_id, sd=None):
    return [e for e in read_all(sd) if e.get("proposal_id") == proposal_id]
