"""
identity/signoff.py - Authenticated sign-off recording.

One function, `record_signoff`, turns "a role approved X" into "an authenticated
person, who holds that role and is distinct from the maker/proposer, approved X",
and writes that to the governance audit trail with the subject id and display
name. It is the single place both the close first-line sign-offs and the payment
approval gate go through, so the identity guarantees are enforced once:

  * authentication  (a valid token, via the configured provider)
  * RBAC            (holds the role registered as owner for the item)
  * segregation     (not the same subject as the maker/proposer)

The audit entry carries subject + name, satisfying "every sign-off in the trail
includes the authenticated identity, not just the role".
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from governance import audit  # noqa: E402
from identity import access  # noqa: E402


def record_signoff(item_type, item_id, owner_role, token, decision="approved",
                   reason="", proposer_subject=None, provider=None,
                   audit_path=None, extra=None):
    """Authenticate, authorize, enforce SoD, and record a sign-off.

    item_type       - "function" (a close stage) or "payment" (the write path).
    item_id         - the item's identifier (function name, payment/proposal id).
    owner_role      - the role registered as owner (e.g. review.REVIEWERS[fn], or a
                      payment's required approver role).
    token           - the approver's authenticated token (or an Identity).
    decision        - "approved" | "rejected".
    proposer_subject- the maker/proposer subject, for the SoD check (optional).

    Returns a record dict (subject, name, role, decision, reason). Raises
    Unauthenticated / Unauthorized / SegregationOfDutiesError on failure -- the
    caller must treat any of those as "not signed off".
    """
    identity = access.authenticate(token, provider)
    access.require_role(identity, owner_role)
    if proposer_subject is not None:
        access.assert_distinct(proposer_subject, identity.subject, item=item_id,
                               relation="proposer/approver" if item_type == "payment"
                               else "maker/checker")

    rec = {
        "subject": identity.subject,
        "name": identity.name,
        "role": owner_role,
        "decision": decision,
        "reason": reason,
    }
    fields = {"item_id": item_id, "role": owner_role, "subject": identity.subject,
              "name": identity.name, "decision": decision}
    if proposer_subject is not None:
        fields["proposer"] = proposer_subject
    if extra:
        fields.update(extra)
    audit.record(f"{item_type}.signoff.{decision}", actor=identity.audit_actor(),
                 reason=reason or f"{item_type} {item_id} {decision}",
                 path=audit_path, **fields)
    return rec
