# The operating model — stages, controls & human-in-the-loop

This is the canonical description of *how the close actually runs*. It is not "a
pipeline with some reviews bolted on": the month-end close is modelled as an
explicit, end-to-end sequence of **stages**, and every stage is a first-class
object with its own controls. The engine is [`cfo-office/stages.py`](cfo-office/stages.py);
the maker-checker layer is [`cfo-office/review.py`](cfo-office/review.py); the
final consolidation is [`cfo-office/cfo_orchestrator.py`](cfo-office/cfo_orchestrator.py).

## Why a staged model (the design principle)

A single generalist CFO **cannot** competently approve the entire operational
flow of a finance function. A CFO plays by ear on technical accounting, on tax,
on revenue recognition, on controls. Delegating eight agents to do all the work
and then asking one human to rubber-stamp it is a *vision of the future*, not
something that survives contact with a real company or a real auditor.

So the model that actually works is **two-tier human-in-the-loop (maker-checker)**:

1. **First line — one domain expert per function.** The agent is the *maker*; the
   person with deep expertise in that area is the *checker* who validates with
   real judgment and signs. The Tax Manager signs tax. The Treasurer signs
   treasury. Technical Accounting signs the financial statements. Nobody signs
   something they don't understand.
2. **Second line — the CFO's final sign-off.** Once every function is signed off
   *and* the numbers reconcile across agents, the CFO signs off on the
   **consolidated** board pack and the **material / cross-cutting** items — not a
   pseudo-review of every line (that's what the first line is for).

Underneath both human tiers sits a **deterministic control layer in code**: hard
gates that do not depend on anyone's opinion (the books reconcile, the statements
articulate and foot, there are zero integrity failures, the audit opinion is not
adverse). The model cannot pass a stage just because someone clicked "approve".

This deterministic-numbers-in-code property has now been checked against reality,
not just asserted. A separate harness regenerates 17 statement-level figures from a
real public company's filings (dLocal, NASDAQ: DLO) and an independent, read-only
auditor diffs them against an SEC-derived answer key: 17 PASS, 0 FAIL. The figures
are statement-level (P&L subtotals, Adjusted EBITDA, balance-sheet section totals,
closing cash, margins, year-over-year growth) and tie to dLocal's reported
FY2024/FY2025 consolidated numbers (IFRS, USD). It runs in two commands with pure
Python standard library, no LLM and no API keys, deterministic on re-run. This
validates the close (stage 4) statement-level math; it does **not** validate the
transaction-level agents in stages 1-3 (AR, AP, Tax) or the multi-entity /
multi-currency consolidation, because no public company discloses transaction-level
subledgers. Public data only: dLocal is not affiliated with this project and did not
endorse, sponsor, or review it; no non-public data was used; the exercise is
illustrative. See [`test-dlocal/AUDIT_EVIDENCE.md`](test-dlocal/AUDIT_EVIDENCE.md)
for the full evidence and boundaries.

## Each stage has four parts

| Part | What it is | Where |
|---|---|---|
| **Maker** | The agent(s) that do the work | the function's `*_agent.py` |
| **Deterministic control** | A code-level gate that must hold (not the model) | `_ctrl_*` in `stages.py` |
| **Checker (HITL)** | Sign-off by the domain expert for that function | `review.review()` |
| **On reject** | Control fail → **block now**; sign-off reject → **rework** (capped) → **block** | `run_stage()` |

The two failure modes are treated differently, on purpose:

- A **deterministic control failure** blocks **immediately**. The controls read
  static, code-computed inputs, so re-running the same stage is guaranteed to fail
  the same way — a rework cycle there would only burn an LLM call and (interactively)
  re-prompt the expert before blocking anyway.
- A **sign-off rejection** is the only failure a re-run can plausibly resolve (the
  expert asked for a correction), so it gets a rework cycle. `MAX_ATTEMPTS = 2`: one
  run plus one rework; if it still isn't signed off, the stage is **BLOCKED**.

A blocked stage halts the **whole close**. You do not build a board pack on top of
an un-controlled, un-reviewed stage. The CFO's final gate is contingent on every
stage having passed.

## The stages, end to end

| # | Stage | Maker (agent) | Deterministic control (code) | Checker — first-line sign-off |
|---|---|---|---|---|
| 1 | Controllership review | Controller | — (no code gate; numbers re-checked downstream) | Accounting Manager |
| 2 | Treasury & liquidity | Treasury | — | Treasurer |
| 3 | Working capital & tax | Accounts Receivable, Accounts Payable, Tax | — | Collections / AR Manager · AP Manager · Tax Manager |
| 4 | Close & financial statements | Accounting & Close, Financial Reporting | **subledgers tie to the GL, the balance sheet balances (\|A−L−E\| ≤ 1), and the cash-flow statement foots to ΔCash** | Accounting Manager · Technical Accounting / Reporting Manager |
| 5 | Planning & analysis (FP&A) | FP&A | — | FP&A Director |
| 6 | Strategic finance | Strategic Finance | — | VP Finance / Head of Strategic Finance |
| 7 | Internal controls | Internal Controls | **zero integrity-control failures** (trial balance, FX, cutoff, authorizations) | Internal Controls Manager |
| 8 | Independent audit | Audit | **audit opinion is not adverse** | Internal Audit Lead |

Stages 1–3, 5 and 6 have no code-level gate of their own: their numbers are
re-derived and cross-checked by the close (stage 4), the controls testing (stage
7) and the independent audit (stage 8), so a bad number there cannot reach the
board without tripping a later hard gate. Every stage, with or without a code
gate, still requires its domain-expert sign-off.

## After the stages: consolidation and the CFO gate

Once all eight stages pass, the orchestrator runs the second tier:

1. **Cross-checks (deterministic, global).** Six checks prove the agents agree on
   the shared numbers — they all derive from `finance_core`, so they *must*
   match, and this catches future drift before it reaches the board:
   - Controller operating income = FP&A's actual operating income
   - Treasury burn = −operating income (when there's an operating loss)
   - Strategic Finance run-rate ÷ 12 = Controller revenue
   - Administration's AR = Controller's AR
   - Financial Reporting net income = Controller operating income
   - Financial Reporting balance-sheet cash = Treasury cash
2. **Escalations** are consolidated from all eight top-level agents and ordered by
   severity (CRITICAL → HIGH). Each risk has a **single owner** — no
   double-counting (e.g. the operating loss is owned by the Controller, not also
   re-escalated by FP&A).
3. **CFO final sign-off.** Precondition: the first line must be 100% signed off.
   The CFO does **not** re-review each operational detail; the CFO confirms the
   first line cleared, reviews the material / cross-cutting items, and signs the
   consolidated board pack. Only then are the board pack and actions written.

## What is recorded (governance / audit trail)

Every stage transition (`running`, `stage REWORK`, `stage PASS`, `stage BLOCKED`)
and every sign-off (reviewer, decision, free-text correction note, timestamp,
and **mode**) is written to the shared state and the audit trail. Each review
records `mode = "human"` or `mode = "auto"`: when there is no reviewer at the
console (snapshot generation, CI, piped input) the workflow auto-approves so it
never hangs, **but the record is explicitly marked `auto`** and is never passed
off as a real human sign-off. The block-on-reject behaviour is the same in both
modes — only the keystroke is simulated, not the control.

## Order-to-Cash sub-orchestrator (Revenue Operations)

The close is one operating loop; Order-to-Cash is another, with different owners
and a continuous cadence. It runs as its own sub-orchestrator
([`cfo-office/o2c/`](cfo-office/o2c/README.md)) using the same governance pattern:
deterministic numbers, maker/checker sign-off, an audit trail, and a hard gate.

It connects the full chain — CRM, customer master, contracts, sales orders,
billing schedules, invoices, revenue recognition, AR, collections, disputes, cash
application, bank receipts, and credit limits — across multiple entities, regions,
and currencies. Ten deterministic maker agents (Order Intake, Customer Master,
Contract, Billing, Revenue Recognition, Collections, Cash Application,
Disputes/Credit, RevOps Analytics, and an independent O2C Audit) each report to a
domain-expert checker. Twenty-five controls (15 hard, 10 soft) tie CRM to billing
to revenue to cash to AR to deferred revenue; a hard failure blocks the release of
O2C reporting, the same way a failed close control blocks the board pack.

The O2C datasets are synthetic and illustrative, generated deterministically with a
known seeded-exception ground truth, so the controls and the test suite have a
verifiable answer key. As with the close, the agents narrate and prioritize but the
numbers are computed in code.

## What this is and isn't

- **Is:** a realistic, production-shaped operating model — staged, with
  deterministic controls and a domain expert accountable for each function, and a
  CFO accountable for the consolidated result. Its statement-level math is checked
  three ways: adversarial synthetic traps (detection), a real public-company
  reconciliation (accuracy), and an independent second-model review (dual-model).
- **Isn't (yet):** fully wired to production data. The statement-level numbers now
  reconcile 17 of 17 to a real public company's reported financials (dLocal, see
  above), but the day-to-day run figures are synthetic, the transaction-level
  agents (AR, AP, Tax) and multi-entity / multi-currency consolidation are not
  validated on real data, and the public demo auto-approves the human steps.
  Materiality-based routing of the HITL, regulatory compliance, payroll and
  AgentOps/CI are deliberately out of scope for this build.

The synthetic side is not "clean" data either. The model was run cold against four
synthetic month-end datasets with roughly 30 seeded errors each. Detection is
strong: the large majority of seeded traps are caught via planted-ID and
flag-column scans. The recurring gap is quantifying and classifying the adjustments
(amounts, P&L-vs-balance-sheet, where credit losses sit), which still needed
correction against ground truth. That gap is precisely why the domain-expert
checker stays in the loop rather than being optional. (The local eval harness passes
33/33 locally; that is a local result, not a third-party or external verification.)
