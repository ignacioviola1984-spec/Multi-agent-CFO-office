"""
review.py - Maker-checker per function (first-line domain-expert HITL).

The realistic governance model. In a real finance org you do NOT let one
generalist CFO approve the entire operational flow — a CFO "plays by ear" on
accounting, tax, planning. Each function is signed off by the human with DEEP
DOMAIN EXPERTISE in that area (maker-checker): the agent does the heavy lifting
(the maker), the domain expert validates with real judgment and signs (the
checker). The CFO's gate is a FINAL sign-off on the consolidated board pack and
the material/cross-cutting items — not a pseudo-review of every detail.

Every review is recorded in the shared state and the audit trail (who reviewed,
what they decided, when, and any correction note) — the maker-checker evidence
auditors expect, and the segregation of duties that makes the system trustworthy.

Each function's agent is the MAKER; the role below is the CHECKER who must sign.
"""

import datetime
import os
import sys

# Each function -> the domain expert who must sign off (deep knowledge in the area).
REVIEWERS = {
    "Controller": "Accounting Manager",
    "Treasury": "Treasurer",
    "Accounts Receivable": "Collections / AR Manager",
    "Accounts Payable": "AP Manager",
    "Tax": "Tax Manager",
    "Accounting & Close": "Accounting Manager",
    "Financial Reporting": "Technical Accounting / Reporting Manager",
    "FP&A": "FP&A Director",
    "Strategic Finance": "VP Finance / Head of Strategic Finance",
    "Internal Controls": "Internal Controls Manager",
    "Audit": "Internal Audit Lead",
}

# The functions that need a first-line domain-expert sign-off before the CFO gate.
FUNCTIONS = list(REVIEWERS.keys())


def _auto():
    """Auto-approve when there is no reviewer at the console (pipes, CI, snapshot
    generation) or when explicitly enabled, so the pipeline never hangs. The
    record is marked 'auto' so it is never passed off as a real human sign-off."""
    if os.environ.get("CFO_AUTO_REVIEW"):
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def _authenticated_review(function, role, token, provider=None):
    """When a token is supplied, bind the sign-off to an AUTHENTICATED identity:
    verify the token and confirm the identity holds this function's reviewer role.
    Returns (subject, name). Raises (Unauthenticated/Unauthorized) if the caller
    presented a token but it is invalid or lacks the role -- an identity-enforced
    review must not silently fall back to role-only. Lazy-imported so the offline
    path never depends on identity/."""
    import os as _os
    import sys as _sys
    _root = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from identity import access
    identity = access.authenticate(token, provider)
    access.require_role(identity, role)
    return identity.subject, identity.name


def review(ctx, function, summary="", token=None, provider=None):
    """First-line review of one function by its domain expert (maker-checker).

    Interactive: prompts the reviewer to approve, reject, or type a correction
    note (free text = rejected with feedback). Non-interactive: auto-approves and
    records it as such. Stores the decision in shared state + audit trail.

    If `token` is supplied, the sign-off is bound to an AUTHENTICATED identity
    (identity/): the token is verified and must hold this function's reviewer role,
    and the recorded decision carries the subject id + display name, not just the
    role. Without a token the behaviour is unchanged (role-only, auto in CI).
    """
    role = REVIEWERS.get(function, "Domain reviewer")
    subject = name = None
    if token is not None:
        subject, name = _authenticated_review(function, role, token, provider)
        decision, note, mode = "approved", "", "human"
    elif _auto():
        decision, note, mode = "approved", "", "auto"
    else:
        print(f"\n  [first-line review · {role}] {function} submitted for sign-off:")
        if summary:
            print("   " + summary)
        try:
            ans = input(f"  {role} — approve {function}? [y]es / [n]o / type a correction: ").strip()
        except EOFError:
            ans = ""
        low = ans.lower()
        if low in ("y", "yes"):
            decision, note, mode = "approved", "", "human"
        elif low in ("", "n", "no"):
            decision, note, mode = "rejected", "", "human"
        else:
            decision, note, mode = "rejected", ans, "human"   # free text = correction

    rec = {"reviewer": role, "decision": decision, "note": note, "mode": mode,
           "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    if subject is not None:
        # Bind the sign-off to the authenticated human, not just the role name.
        rec["subject"], rec["name"] = subject, name
    ctx.put(function, {"review": rec})
    who = f"{role} [{subject} ({name})]" if subject is not None else role
    detail = f"{function} {decision}" + (" (auto)" if mode == "auto" else "") + (f": {note}" if note else "")
    ctx.audit(who, decision.upper(), detail)
    return rec


def first_line_status(ctx, functions=None):
    """Roll up the first-line reviews: which functions are signed off vs not."""
    functions = functions or FUNCTIONS
    approved, rejected = [], []
    for fn in functions:
        r = ctx.get(fn, "review", None)
        (approved if (r and r["decision"] == "approved") else rejected).append(fn)
    return {"approved": approved, "rejected": rejected, "total": len(functions),
            "all_approved": not rejected}
