"""
registry.py - The BOUND. The allowlist of parameters that may self-calibrate.

This registry is the security boundary of the whole self-improvement system.
It lists the ONLY values that the proposer/gate are ever allowed to change, and
for each one the hard limits: an absolute [min, max] range, a per-change step
cap, a cooldown, the metric it is calibrated against, and the human owner who
must approve a change.

Invariants enforced here:
  - A name not in REGISTRY is FROZEN: the system has no way to write it.
  - A champion value is always clamped to [min, max] and to current +/- max_step.
  - The champion store keeps an append-only version history, so any prior value
    can be restored.

Nothing in this file changes any formula in finance_core. It only governs which
scalar VALUES finance_core is allowed to read from the champion store.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# THE REGISTRY (the allowlist). Seeded from the model's current values.
# --------------------------------------------------------------------------
REGISTRY = {
    "ar_collection_rate": {
        "name": "ar_collection_rate",
        "current": 0.90,
        "min": 0.80,
        "max": 0.98,
        "max_step": 0.03,
        "cooldown": 1,
        "metric": "realized collections / forecast collections",
        "owner": "Treasurer",
    },
    "materiality_pct_threshold": {
        "name": "materiality_pct_threshold",
        "current": 5.0,
        "min": 2.0,
        "max": 10.0,
        "max_step": 1.0,
        "cooldown": 1,
        "metric": "escalation precision/recall on labeled outcomes",
        "owner": "Controller",
    },
    "materiality_usd_threshold": {
        "name": "materiality_usd_threshold",
        "current": 20000.0,
        "min": 5000.0,
        "max": 50000.0,
        "max_step": 5000.0,
        "cooldown": 1,
        "metric": "escalation precision/recall on labeled outcomes",
        "owner": "Controller",
    },
    "approval_threshold_usd": {
        "name": "approval_threshold_usd",
        "current": 25000.0,
        "min": 10000.0,
        "max": 50000.0,
        "max_step": 5000.0,
        "cooldown": 1,
        "metric": "share of flagged disbursements that needed review",
        "owner": "Internal Controls",
    },
}


class FrozenParameterError(Exception):
    """Raised when the system is asked to touch a value not in the registry."""


# --------------------------------------------------------------------------
# State location. finance_core reads the SAME champions.json (honoring the same
# SELFIMPROVE_STATE_DIR env var), so the gate can apply a candidate value and
# re-run the evals against it.
# --------------------------------------------------------------------------
def state_dir(override=None):
    if override:
        return override
    return os.environ.get("SELFIMPROVE_STATE_DIR") or os.path.join(HERE, "state")


def _champions_path(sd):
    return os.path.join(sd, "champions.json")


def is_registered(name):
    return name in REGISTRY


def require_registered(name):
    if name not in REGISTRY:
        raise FrozenParameterError(
            f"'{name}' is not in the registry. It is frozen and cannot be changed "
            f"by the self-improvement system."
        )
    return REGISTRY[name]


def meta(name):
    return dict(require_registered(name))


def param_names():
    return list(REGISTRY.keys())


# --------------------------------------------------------------------------
# Champion store (the mutable, versioned current values + history).
# --------------------------------------------------------------------------
def _empty_store():
    champions = {}
    history = {}
    for n, m in REGISTRY.items():
        champions[n] = {"value": m["current"], "version": 1}
        history[n] = [{
            "version": 1, "value": m["current"], "by": "seed",
            "reason": "registry seed (original model value)", "ts": None,
            "proposal_id": None,
        }]
    return {"cycle": 0, "champions": champions, "history": history}


def load_store(sd=None):
    sd = state_dir(sd)
    path = _champions_path(sd)
    if not os.path.exists(path):
        return _empty_store()
    with open(path, encoding="utf-8") as f:
        store = json.load(f)
    # Backfill any registry param missing from an older store (never drop history).
    base = _empty_store()
    for n in REGISTRY:
        store.setdefault("champions", {}).setdefault(n, base["champions"][n])
        store.setdefault("history", {}).setdefault(n, base["history"][n])
    store.setdefault("cycle", 0)
    return store


def save_store(store, sd=None):
    sd = state_dir(sd)
    os.makedirs(sd, exist_ok=True)
    with open(_champions_path(sd), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def ensure_init(sd=None):
    """Create the champion store at seed values if it does not exist yet."""
    sd = state_dir(sd)
    if not os.path.exists(_champions_path(sd)):
        save_store(_empty_store(), sd)
    return load_store(sd)


def champion_value(name, sd=None):
    require_registered(name)
    return load_store(sd)["champions"][name]["value"]


def champion_version(name, sd=None):
    require_registered(name)
    return load_store(sd)["champions"][name]["version"]


def history(name, sd=None):
    require_registered(name)
    return load_store(sd)["history"][name]


# --------------------------------------------------------------------------
# Bound helpers (pure functions on the registry metadata).
# --------------------------------------------------------------------------
def within_bounds(name, value):
    m = require_registered(name)
    return m["min"] <= value <= m["max"]


def within_step(name, value, sd=None):
    m = require_registered(name)
    return abs(value - champion_value(name, sd)) <= m["max_step"] + 1e-12


def clamp(name, value, sd=None):
    """Clamp a raw candidate to [min, max] AND to current +/- max_step."""
    m = require_registered(name)
    cur = champion_value(name, sd)
    lo = max(m["min"], cur - m["max_step"])
    hi = min(m["max"], cur + m["max_step"])
    return min(hi, max(lo, value))


def cooldown_remaining(name, sd=None):
    """Cycles still to wait before this param may change again (0 = free)."""
    m = require_registered(name)
    store = load_store(sd)
    last_change_cycle = None
    for h in store["history"][name]:
        if h.get("kind") in ("promote", "rollback") or h["version"] > 1:
            last_change_cycle = h.get("cycle", last_change_cycle)
    if last_change_cycle is None:
        return 0
    waited = store["cycle"] - last_change_cycle
    return max(0, m["cooldown"] - waited)


def cooldown_ok(name, sd=None):
    return cooldown_remaining(name, sd) == 0


def bump_cycle(sd=None):
    store = load_store(sd)
    store["cycle"] += 1
    save_store(store, sd)
    return store["cycle"]


def set_champion(name, value, by, reason, ts, proposal_id=None, evidence=None,
                 kind="promote", sd=None):
    """Promote a NEW champion value. Refuses anything frozen or out of bounds.

    This is the ONLY way a champion value changes, and it is append-only on the
    history (every prior value is retained for rollback).
    """
    require_registered(name)
    if not within_bounds(name, value):
        m = REGISTRY[name]
        raise ValueError(
            f"refused: {name}={value} is outside bounds [{m['min']}, {m['max']}]"
        )
    store = load_store(sd)
    new_version = store["champions"][name]["version"] + 1
    store["champions"][name] = {"value": value, "version": new_version}
    store["history"][name].append({
        "version": new_version, "value": value, "by": by, "reason": reason,
        "ts": ts, "proposal_id": proposal_id, "evidence": evidence,
        "kind": kind, "cycle": store["cycle"],
    })
    save_store(store, sd)
    return new_version
