"""
rollback.py - Reversibility. Restore any prior champion in one step.

Every champion value ever promoted stays in the registry's append-only version
history. Rollback re-promotes the value from a chosen prior version as a new
version (so the history itself is never rewritten) and logs the rollback to the
audit trail.
"""

import registry
import audit


def rollback(param, target_version, by, sd=None):
    registry.require_registered(param)
    hist = registry.history(param, sd)
    match = next((h for h in hist if h["version"] == target_version), None)
    if match is None:
        return {"ok": False, "reason": f"no version {target_version} for {param}"}

    value = match["value"]
    ts = audit.now_iso()
    new_version = registry.set_champion(
        param, value, by=by, reason=f"rollback to version {target_version}",
        ts=ts, proposal_id=None, evidence={"rolled_back_to": target_version},
        kind="rollback", sd=sd,
    )
    registry.bump_cycle(sd)
    audit.record("rollback",
                 f"{param} restored to v{target_version} value {value} (now v{new_version}) by {by}",
                 sd=sd, param=param, restored_value=value,
                 from_version=target_version, new_version=new_version, by=by)
    return {"ok": True, "value": value, "new_version": new_version}
