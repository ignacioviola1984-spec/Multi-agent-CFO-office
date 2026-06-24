# CFO Office — multi-agent finance department over shared state

A "CFO office": specialized agents that each own a piece of the month-end close
and **communicate through a shared state book**, coordinated by a CFO
orchestrator. Each function is **signed off by its own domain expert**
(maker-checker), and the CFO then reconciles the numbers and gives a single
**final** sign-off on the consolidated board pack.

It builds on the same principle as the rest of the repo: **every number is
computed in code** (`finance_core.py`, deterministic); the model only reasons
and writes prose. It never invents a figure.

## The agents

| Agent | File | What it owns | First-line reviewer (checker) |
|-------|------|--------------|-------------------------------|
| **Controller** | `controller_agent.py` | Close review: P&L consistency, margins, risk flags | Accounting Manager |
| **Treasury** | `treasury_agent.py` | Liquidity: cash, burn, runway, 13-week cash forecast | Treasurer |
| **Administration** | `administration_agent.py` | Supervisor: coordinates AR / AP / Tax | *(rolls up its subs)* |
| ↳ Accounts Receivable | `ar_agent.py` | Receivables, overdue, DSO, collections risk | Collections / AR Manager |
| ↳ Accounts Payable | `ap_agent.py` | Payables, overdue & upcoming, DPO, supplier risk | AP Manager |
| ↳ Tax | `tax_agent.py` | Tax obligations by jurisdiction, compliance risk | Tax Manager |
| **Accounting & Reporting** | `accounting_reporting_agent.py` | Supervisor: the close and the financial statements | *(rolls up its subs)* |
| ↳ Accounting & Close | `accounting_close_agent.py` | Reconcile AR/AP subledgers to the GL, equity roll-forward | Accounting Manager |
| ↳ Financial Reporting | `financial_reporting_agent.py` | The three financial statements (P&L, balance sheet, cash flow) | Technical Accounting / Reporting Manager |
| **FP&A** | `fpa_agent.py` | Forecast, MoM variance, **budget-vs-actual** variance, anomalies | FP&A Director |
| **Strategic Finance** | `strategic_finance_agent.py` | Run-rate, Rule of 40, burn multiple, magic number, path to breakeven | VP Finance / Head of Strategic Finance |
| **Internal Controls** | `internal_controls_agent.py` | Assurance (SOX-style): trial balance, FX completeness, posting cutoff, duplicate/unauthorized disbursements | Internal Controls Manager |
| **Audit** | `audit_agent.py` | Independent third line: re-derives the figures from source and issues an opinion | Internal Audit Lead |
| **CFO** | `cfo_orchestrator.py` | Runs the others, reconciles them, consolidates escalations, **final** consolidated sign-off, board pack | *(the CFO is the final gate)* |

## The shared state (`shared_state.py`) and review (`review.py`)

`CFOContext` is the common "book": every agent writes its structured result and
flags with `ctx.put(agent, payload)`, reads peers with `ctx.get(...)`, and every
step — including each review decision — is appended to an audit trail. Persisted
to `cfo_state.json`. `review.py` holds the maker-checker: it maps each function
to the domain expert who must sign off, records the decision (who / what / when /
any correction note), and rolls up the first-line status.

## How the office runs

```
CFO orchestrator
  ├─ 1) Controller            → close, margins            → ✔ Accounting Manager
  ├─ 2) Treasury              → cash, burn, runway, 13-wk  → ✔ Treasurer
  ├─ 3) Administration        → sub-orchestrates AR/AP/Tax
  │        ├─ Accounts Receivable → overdue, DSO           → ✔ Collections / AR Manager
  │        ├─ Accounts Payable    → overdue, DPO           → ✔ AP Manager
  │        └─ Tax                 → overdue, compliance    → ✔ Tax Manager
  ├─ 4) Accounting & Reporting → sub-orchestrates close + statements
  │        ├─ Accounting & Close  → reconciliations        → ✔ Accounting Manager
  │        └─ Financial Reporting → 3 financial statements → ✔ Reporting Manager
  ├─ 5) FP&A                  → forecast, variances        → ✔ FP&A Director
  ├─ 6) Strategic Finance     → efficiency, breakeven      → ✔ VP Finance
  ├─ 7) Internal Controls     → assurance checks           → ✔ Internal Controls Manager
  ├─ 8) Audit                 → independent re-performance → ✔ Internal Audit Lead
  ├─ —  first line must be complete (every function signed off by its expert)
  ├─ 9) cross_checks          → agents must agree on shared numbers
  ├─ 10) gather_escalations   → consolidate flags by severity
  ├─ 11) CFO final gate       → sign off the consolidated pack + material items
  └─ 12) board pack + actions → fixed only on CFO approval
```

Run the whole office (needs `ANTHROPIC_API_KEY` in the repo-root `.env`):

```bash
python cfo_orchestrator.py            # prompts each reviewer, then the CFO
CFO_AUTO_REVIEW=1 python cfo_orchestrator.py   # auto-approve (replay / CI)
```

Each agent also runs standalone (`python tax_agent.py`, etc.) for solo testing.

## Design decisions (the "why")

- **Two-tier human control (maker-checker), not one gate.** This is how finance
  actually works, and it is the correction to a tempting but wrong simplification.
  A generalist CFO cannot competently approve the entire operational flow — they
  "play by ear" on accounting, tax, planning. So **each function is signed off by
  the domain expert with real depth in that area** (the first line; the Tax
  Manager signs tax, the Treasurer signs treasury, …), and the **CFO gate is the
  final sign-off** on the consolidated board pack and the material/cross-cutting
  items only. If any function is not signed off by its reviewer, the close is
  **blocked before the CFO** — you don't fabricate a board pack over un-reviewed
  work. This makes the AI a force-multiplier on each expert, not a replacement.
- **Single source of numbers.** All agents import `finance_core`, so they cannot
  disagree on a figure by construction. The orchestrator still runs `cross_checks`
  (Controller's operating income = FP&A's actual; Treasury's burn = −operating
  income; Reporting's net income = Controller's; Reporting's cash = Treasury's) —
  a reliability control that catches drift before the board.
- **Hierarchical orchestration.** Two of the agents are themselves
  sub-orchestrators (Administration → AR/AP/Tax; Accounting & Reporting → Close +
  Financial Reporting). The CFO sees eight reports, not thirteen — the hierarchy
  mirrors a real org and keeps the top level clean.
- **Escalations don't double-count.** Each risk has exactly one owner:
  Controller → operating loss; Treasury → runway and 13-week cash; the
  Administration sub-agents → overdue receivables / payables / tax; FP&A →
  material *unfavorable* budget variances; Strategic Finance → capital efficiency
  and whether growth alone reaches breakeven; Internal Controls → *control
  failures only*; Audit → the *opinion* only (it re-performs but does not re-raise
  items the close already owns).
- **Assurance is a distinct lens, and audit is genuinely independent.** Internal
  Controls tests the integrity of the data/process; Audit **re-derives** the
  tie-outs, footing and articulation from the raw ledger and subledger (not via
  the close/reporting functions), so a bug there surfaces as an audit exception
  instead of being mirrored. Both are **proven with tamper tests**: corrupt a
  control account and the close flags an open item and the audit opinion flips to
  adverse.
- **Numbers by code, prose by model.** No LLM call is asked to compute or
  recompute a figure; the model receives the numbers and explains them.

## Order-to-Cash sub-orchestration (`o2c/`)

The CFO orchestrator also runs the **Order-to-Cash control tower**
([`o2c/`](o2c/README.md)) as a sub-orchestration: `cfo_orchestrator.py` calls
[`cfo_o2c_bridge.py`](cfo_o2c_bridge.py) during the close, the O2C tower runs its
10 maker agents and 25 controls deterministically, and its status, metrics, and
escalations land in the same `CFOContext`. So a single CFO run covers the
month-end close and Order-to-Cash (Billing, Collections, Revenue Recognition, Cash
Application, RevOps) as agents and sub-agents, with O2C in the consolidated board
pack. The bridge is deterministic and needs no API key.

## Relationship to `orchestration/`

`orchestration/` holds the earlier fixed-sequence operating model (close → cash →
reporting). The CFO office is the same idea evolved into a **shared-state,
multi-agent department** with maker-checker review and a coordinating CFO. Both
reuse `finance_core` as the single deterministic source of numbers.
