"""
shared_state.py - CarteraContext: the shared "libro" of the Nexo run.

Mirrors cfo-office/shared_state.py (CFOContext) for the broker domain. Every
agent put()s its structured result + flags; peers and the orchestrator get() it.
Every step is appended to an audit trail (in memory and to nexo/audit_log.jsonl),
and the whole state persists to nexo_state.json. This is what makes the run
auditable and replayable: who wrote what, and when.

The proposed-action inbox lives in this state (state["inbox"]) but is managed by
review.py - same split as the finance model (shared_state vs review).
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths

# The Windows console defaults to cp1252 and cannot print the accented Spanish /
# punctuation the agents emit. Force UTF-8 on stdout/stderr (shared_state is
# imported by every agent and entrypoint), so the inbox prints without breaking.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class CarteraContext:
    """Shared, persisted state for one Nexo run."""

    def __init__(self, state_path=None, audit_path=None, fresh_audit=False):
        self.state_path = state_path or paths.STATE_PATH
        self.audit_path = audit_path or paths.AUDIT_PATH
        self.state = {
            "meta": {"started": now_iso()},
            "agents": {},     # agent -> structured result payload
            "flags": [],      # [{agent, severity, message, ts}]
            "audit": [],      # [{ts, agent, status, detail}]
            "inbox": [],      # [Action.to_dict()]  (managed by review.py)
            "metrics": {},     # portfolio metrics snapshot (analisis_cartera_agent)
        }
        # Start a fresh audit log for this run when asked (CI/replay); otherwise
        # append to the trail across runs.
        if fresh_audit and os.path.exists(self.audit_path):
            os.remove(self.audit_path)

    # -- lateral communication ----------------------------------------
    def put(self, agent, payload):
        """An agent leaves its structured result in the shared book."""
        self.state["agents"].setdefault(agent, {}).update(payload)

    def get(self, agent, key=None, default=None):
        """Read what an agent left (lateral communication between agents)."""
        a = self.state["agents"].get(agent, {})
        return a if key is None else a.get(key, default)

    # -- flags + audit ------------------------------------------------
    def flag(self, agent, severity, message):
        """Record a flag (a risk / item of note) raised by an agent."""
        self.state["flags"].append(
            {"agent": agent, "severity": severity, "message": message, "ts": now_iso()})
        self.audit(agent, f"FLAG/{severity}", message)

    def audit(self, agent, status, detail):
        """Append a step to the audit trail: in memory + append-only jsonl."""
        evt = {"ts": now_iso(), "agent": agent, "status": status, "detail": detail}
        self.state["audit"].append(evt)
        paths.ensure_dirs()
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        print(f"  [audit] {agent:18} {status:14} {detail}")

    # -- persistence --------------------------------------------------
    def save(self):
        # allow_nan=False: fail loudly here if an inf/NaN ever slips into a number
        # rather than writing an invalid JSON a strict parser would reject later.
        self.state["meta"]["saved"] = now_iso()
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2, allow_nan=False)
        return self.state_path

    @classmethod
    def load(cls, state_path=None):
        """Reload a persisted state (used by the app / replay)."""
        ctx = cls(state_path=state_path)
        with open(ctx.state_path, encoding="utf-8") as f:
            ctx.state = json.load(f)
        return ctx
