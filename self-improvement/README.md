# Bounded self-improvement

A system that lets the AI **propose better parameter values and nothing else.**
It can never change the math, never adopt a change without passing the evals and
a human gate, and never touch anything outside an explicit registry. The bounds
do the work, and the tests prove the bounds.

This is deliberately narrow. It is parameter calibration under hard limits, not
an agent that rewrites itself.

## The bound is in code, not in the system's reach

The bounds (`min`, `max`, `max_step`, `cooldown`) and the auto-adopt flag live in
**code** (the `REGISTRY` in `registry.py` and `gate.AUTO_ADOPT_ENABLED`), not in
the mutable champion store. The loop can only propose a new **value** for a
registered parameter, within those bounds. It cannot widen a bound, change a step
or cooldown, or enable auto-adopt.

The gate reads the bounds from the code registry, never from the store, so even a
tampered store cannot widen them. Auto-adopt is off by default; only a human
editing config (the code) can enable it.

Proven by tests: `test_system_cannot_change_its_own_bounds` and
`test_system_cannot_flip_auto_adopt` in `self-improvement/tests/test_bounds.py`.

## The invariant (what can and cannot self-modify)

CAN change, only within the registry's limits:

| Parameter | Seed | Bounds | Max step | Owner | Metric |
|---|---|---|---|---|---|
| `ar_collection_rate` | 0.90 | [0.80, 0.98] | 0.03 | Treasurer | realized collections / forecast collections |
| `materiality_pct_threshold` | 5.0 | [2.0, 10.0] | 1.0 | Controller | escalation precision/recall on labeled outcomes |
| `materiality_usd_threshold` | 20000 | [5000, 50000] | 5000 | Controller | escalation precision/recall on labeled outcomes |
| `approval_threshold_usd` | 25000 | [10000, 50000] | 5000 | Internal Controls | share of flagged disbursements that needed review |

CANNOT change, ever, through this system:

- Any calculation in `orchestration/finance_core.py`. Only four scalar VALUES are
  read from the registry; every formula is untouched.
- Any value not listed in `registry.py`. It is frozen by definition: the system
  has no code path to write it.
- The eval ground-truth (`evals/eval_set.py`), the audit trail mechanism, the
  read-only posture, the human gates, or the cross-checks.

## The champion / challenger loop

```
outcomes ──> propose ──> [gate] ──> human approval ──> champion (new version)
             (challenger)   │                              │
                            │                              └─> append-only audit
                            └─ reject (any gate fails)          + version history ──> rollback
```

1. **Propose (challenger).** `propose.py` computes a candidate value
   **deterministically** by statistical calibration over an outcomes window
   (e.g. the realized collection rate), then clamps it to the bounds and the
   step cap. The LLM is never in the path that picks the number; it may only
   write the human-readable `rationale`.
2. **Gate.** A challenger is promoted ONLY if ALL hold:
   - (a) within bounds, step cap, and cooldown;
   - (b) the deterministic eval suite passes with **no regression** vs the
     current champion;
   - (c) a backtest over the outcomes window shows the metric does not get worse;
   - (d) the parameter's registered **owner** (a human, not the proposer) approves.
3. **Promote.** On approval the new champion is written as a new version, and the
   full decision (old, new, evidence, eval result, approver, timestamp) is
   appended to the append-only audit trail.
4. **Rollback.** Every prior champion is kept; `rollback.py` restores any prior
   version in one step and logs it.

Default posture is **propose-only**: nothing is adopted automatically. Auto-adopt
is disabled behind `gate.AUTO_ADOPT_ENABLED`, which is off by default.

## How the bounds are enforced

- `registry.py` is the allowlist. `clamp()` enforces [min, max] and the step cap;
  `set_champion()` refuses anything frozen or out of bounds; history is
  append-only.
- `gate.py` re-runs every check at approval time, so human approval is necessary
  but not sufficient. It applies the candidate to the same champion store
  `finance_core` reads, reloads `finance_core`, and runs the deterministic
  `Numbers` eval suite. If the candidate moves any tested number past tolerance,
  it is rejected.
- `finance_core.py` reads the four values through `_registry_param(name, default)`,
  which returns the default whenever the store is absent or the name is missing,
  so behavior is identical by default.

Note on scope: the four parameters can only move **deterministic** numbers, so the
gate runs the deterministic `Numbers` suite (the binding check). The LLM-based
`Extraction` and `Grounding` suites do not depend on these parameters and are
unaffected by construction.

## Trust boundary and honest caveats

These were surfaced by an adversarial review of the code and are stated plainly:

- The champion store (`state/champions.json`) and the audit trail are the trust
  boundary. Bounds are enforced on every write path the system exposes. Editing
  those files by hand is out-of-band tampering, the same class as editing
  `finance_core.py` directly, and is outside the "through this system" threat
  model.
- Approval is matched to the registry `owner` (maker-checker by role). This is an
  identity/role gate, not an authentication system. A production deployment would
  wire `approver` to a real authenticated identity (SSO).
- Rollback restores a previously-validated champion (or the seed) and re-checks
  bounds, but it does not re-run the evals or the step cap. That is by design: it
  is the undo/reversibility path, and every value in the history was either the
  seed or already passed the full gate.
- The audit trail is append-only by file mode; it is not cryptographically
  chained. Hash-chaining would make it tamper-evident against out-of-band edits
  (a possible enhancement).

## How the bounds are proven (`tests/test_bounds.py`)

Each test runs against an isolated temp champion store, so nothing touches the
real state. They prove:

1. a proposal outside [min, max] is rejected;
2. a proposal exceeding max_step is clamped (proposer) or rejected (gate);
3. a parameter not in the registry cannot be changed;
4. a challenger that regresses the eval set is rejected **even if a human approves it**;
5. nothing is adopted without explicit approval (propose-only by default);
6. rollback restores the exact prior value;
7. every proposal and decision appears in the audit trail;
8. tamper test: changing the eval ground-truth or a frozen parameter through this
   system fails, and `evals/eval_set.py` is left byte-for-byte unchanged;
9. cooldown is respected;
10. the system cannot change its OWN bounds (min/max/max_step/cooldown): they live
    in `REGISTRY` (code), the champion store holds no bounds, a tampered store
    cannot widen them, and no package code path mutates them;
11. the system cannot flip the auto-adopt flag: it stays off through every
    operation and is assigned in exactly one place (the module default), so only
    a human editing config can enable it.

## Run it

```
python self-improvement/demo.py                 # end-to-end: accepted + rejected + rollback
python self-improvement/tests/test_bounds.py    # the bound tests
python self-improvement/cli.py status           # registry, champions, pending proposals
python self-improvement/cli.py propose ar_collection_rate --outcomes self-improvement/demo_data/ar_outcomes.json
python self-improvement/cli.py show P1
python self-improvement/cli.py approve P1 --by "Treasurer"
python self-improvement/cli.py rollback ar_collection_rate 1 --by "Treasurer"
```

## Files

- `registry.py` : the allowlist and the versioned champion store (the bound).
- `propose.py` : deterministic calibrators + proposal emission (LLM writes only the rationale).
- `gate.py` : bounds/step/cooldown + eval no-regression + backtest + human approval.
- `rollback.py` : restore any prior champion in one step.
- `cli.py` : propose / show / approve / reject / rollback / status.
- `audit.py` : append-only audit trail.
- `demo.py` : the end-to-end demonstration.
- `demo_data/` : clearly labeled synthetic outcomes (demonstration only, not real data).
- `tests/test_bounds.py` : the proofs.
- `state/` : runtime artifacts (champions, proposals, audit trail). Generated by the
  demo/CLI and git-ignored; absent on a fresh checkout, so `finance_core` uses the
  documented defaults.
