"""
propose.py - The challenger. Computes a candidate value DETERMINISTICALLY.

The number is produced by statistical calibration over an outcomes window and
then clamped to the registry bounds and step cap. The LLM is NEVER in the path
that picks the number: it may only write the human-readable `rationale` string.
This is enforced structurally: `proposed` is computed here and the rationale
function receives it as a finished input, it cannot change it.

Emits a structured proposal: {id, param, old, proposed, evidence, rationale}.
"""

import json
import os

import registry
import audit


# --------------------------------------------------------------------------
# Deterministic calibrators (pure statistics, no model).
# --------------------------------------------------------------------------
def _calibrate_ratio(outcomes):
    """AR collection rate = realized collections / forecast collections."""
    fc_sum = sum(float(o["forecast_collectible"]) for o in outcomes)
    ac_sum = sum(float(o["actual_collected"]) for o in outcomes)
    rate = (ac_sum / fc_sum) if fc_sum else 0.0
    return rate, {
        "n_periods": len(outcomes),
        "forecast_collectible_total": round(fc_sum, 2),
        "actual_collected_total": round(ac_sum, 2),
        "realized_rate": round(rate, 4),
    }


def _f1(threshold, outcomes):
    tp = fp = fn = tn = 0
    for o in outcomes:
        flagged = float(o["magnitude"]) >= threshold
        mattered = bool(o["mattered"])
        if flagged and mattered:
            tp += 1
        elif flagged and not mattered:
            fp += 1
        elif (not flagged) and mattered:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return f1, prec, rec


def _calibrate_threshold(outcomes):
    """Pick the magnitude threshold that maximizes F1 of flag-vs-mattered.

    Candidate cut points are the observed magnitudes; ties break to the SMALLEST
    threshold (favor recall, the conservative choice for a control). Fully
    deterministic given the outcomes.
    """
    mags = sorted({float(o["magnitude"]) for o in outcomes})
    best_t, best_f1, best_pr = mags[0], -1.0, (0.0, 0.0)
    for t in mags:
        f1, prec, rec = _f1(t, outcomes)
        if f1 > best_f1 + 1e-12:
            best_t, best_f1, best_pr = t, f1, (prec, rec)
    f1, prec, rec = _f1(best_t, outcomes)
    return best_t, {
        "n_outcomes": len(outcomes),
        "n_mattered": sum(1 for o in outcomes if o["mattered"]),
        "chosen_threshold": best_t,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
    }


def _calibrate(param, outcomes):
    if param == "ar_collection_rate":
        return _calibrate_ratio(outcomes)
    return _calibrate_threshold(outcomes)


# --------------------------------------------------------------------------
# Rationale (the ONLY thing an LLM may produce). Deterministic default so the
# demo and tests run with no model; in a deployment an LLM narrator plugs in
# here, but it still only receives the already-decided number.
# --------------------------------------------------------------------------
def default_rationale(param, old, proposed, evidence):
    m = registry.meta(param)
    direction = "raise" if proposed > old else ("lower" if proposed < old else "hold")
    clamp_note = " (clamped to the step cap / bounds)" if evidence.get("clamped") else ""
    return (
        f"Statistical calibration of '{param}' over the outcomes window proposes to "
        f"{direction} it from {old} to {proposed}{clamp_note}. Metric: {m['metric']}. "
        f"Evidence: {evidence}. The number is computed deterministically from the "
        f"data; this text only explains it."
    )


# --------------------------------------------------------------------------
# Proposal store (pending proposals awaiting a human decision).
# --------------------------------------------------------------------------
def _proposals_path(sd=None):
    sd = registry.state_dir(sd)
    os.makedirs(sd, exist_ok=True)
    return os.path.join(sd, "proposals.json")


def load_proposals(sd=None):
    p = _proposals_path(sd)
    if not os.path.exists(p):
        return {"seq": 0, "items": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_proposals(store, sd=None):
    with open(_proposals_path(sd), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def get_proposal(pid, sd=None):
    for it in load_proposals(sd)["items"]:
        if it["id"] == pid:
            return it
    return None


def update_proposal(pid, sd=None, **changes):
    store = load_proposals(sd)
    for it in store["items"]:
        if it["id"] == pid:
            it.update(changes)
            save_proposals(store, sd)
            return it
    return None


# --------------------------------------------------------------------------
# propose()
# --------------------------------------------------------------------------
def propose(param, outcomes, by="proposer", rationale_fn=None, sd=None):
    # Frozen-parameter guard: a name not in the registry can never be proposed.
    registry.require_registered(param)
    registry.ensure_init(sd)

    old = registry.champion_value(param, sd)

    # Respect cooldown: do not emit a change while the param is cooling down.
    remaining = registry.cooldown_remaining(param, sd)
    store = load_proposals(sd)
    store["seq"] += 1
    pid = f"P{store['seq']}"

    if remaining > 0:
        proposal = {
            "id": pid, "param": param, "old": old, "proposed": old,
            "raw_candidate": old, "evidence": {"cooldown_remaining": remaining},
            "rationale": f"Blocked: '{param}' is in cooldown for {remaining} more cycle(s).",
            "status": "blocked_cooldown", "by": by,
        }
        store["items"].append(proposal)
        save_proposals(store, sd)
        audit.record("proposal_blocked", f"{param} in cooldown ({remaining})",
                     sd=sd, proposal_id=pid, param=param)
        return proposal

    # Deterministic number: calibrate, then clamp to bounds and step.
    raw, evidence = _calibrate(param, outcomes)
    proposed = registry.clamp(param, raw, sd)
    evidence = dict(evidence)
    evidence.update({
        "raw_candidate": round(raw, 6),
        "clamped": abs(proposed - raw) > 1e-12,
        "bounds": [registry.REGISTRY[param]["min"], registry.REGISTRY[param]["max"]],
        "max_step": registry.REGISTRY[param]["max_step"],
    })

    rationale = (rationale_fn or default_rationale)(param, old, proposed, evidence)

    proposal = {
        "id": pid, "param": param, "old": old, "proposed": proposed,
        "raw_candidate": raw, "evidence": evidence, "rationale": rationale,
        "status": "pending", "by": by, "outcomes": outcomes,
    }
    store["items"].append(proposal)
    save_proposals(store, sd)
    audit.record("proposed", f"{param}: {old} -> {proposed}", sd=sd,
                 proposal_id=pid, param=param, old=old, proposed=proposed,
                 evidence=evidence)
    return proposal
