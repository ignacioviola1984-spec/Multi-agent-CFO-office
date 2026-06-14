# CFO Office — multi-agent finance team over shared state

A "CFO office": specialized agents that each own a piece of the month-end
close and **communicate through a shared state book**, coordinated by a CFO
orchestrator that consolidates their work, reconciles their numbers, and asks
for a single human sign-off before anything is fixed.

It builds on the same principle as the rest of the repo: **every number is
computed in code** (`finance_core.py`, deterministic); the model only reasons
and writes prose. It never invents a figure.

## The agents

| Agent | File | What it owns |
|-------|------|--------------|
| **Controller** | `controller_agent.py` | Close review: P&L internal consistency, margins, risk flags |
| **Treasury** | `treasury_agent.py` | Liquidity: cash, monthly burn, runway |
| **Administration** | `administration_agent.py` | Supervisor: coordinates AR, AP & Tax and consolidates their working-capital + compliance flags |
| ↳ Accounts Receivable | `ar_agent.py` | Receivables, overdue, DSO, collections risk |
| ↳ Accounts Payable | `ap_agent.py` | Payables, overdue & upcoming, DPO, supplier risk |
| ↳ Tax | `tax_agent.py` | Tax obligations, overdue & upcoming by jurisdiction, compliance risk |
| **FP&A** | `fpa_agent.py` | Forecast (next period), MoM variance, **budget-vs-actual** variance, anomalies |
| **Strategic Finance** | `strategic_finance_agent.py` | Run-rate, Rule of 40, burn multiple, magic number, growth scenarios, path to breakeven |
| **Internal Controls** | `internal_controls_agent.py` | Assurance (SOX-style): trial balance ties, FX completeness, posting cutoff, duplicate payments, disbursement authorization |
| **CFO** | `cfo_orchestrator.py` | Runs the others, reconciles them, consolidates escalations, single HITL, board pack |

## The shared state (`shared_state.py`)

`CFOContext` is the common "book": every agent writes its structured result
and flags with `ctx.put(agent, payload)`, reads peers with `ctx.get(...)`, and
every step is appended to an audit trail. Persisted to `cfo_state.json`.
Communication goes *through the book*, not a free-form mesh — that is what
makes the system auditable: you can see who wrote what, and when.

## How the office runs

```
CFO orchestrator
  ├─ 1) Controller            → close, margins                  + flags
  ├─ 2) Treasury              → cash, burn, runway              + flags
  ├─ 3) Administration        → sub-orchestrates AR / AP / Tax  + flags
  │        ├─ Accounts Receivable → overdue, DSO
  │        ├─ Accounts Payable    → overdue, DPO
  │        └─ Tax                 → overdue, compliance
  ├─ 4) FP&A                  → forecast, variances, anomalies  + flags
  ├─ 5) Strategic             → run-rate, efficiency, breakeven + flags
  ├─ 6) Internal Controls     → assurance: integrity + authorization checks + flags
  ├─ 7) cross_checks          → agents must agree on shared numbers
  ├─ 8) gather_escalations    → consolidate flags by severity
  ├─ 9) hitl_gate             → ONE human approval if serious flags
  └─ 10) board pack + actions → consolidated, fixed only on approval
```

Run the whole office (needs `ANTHROPIC_API_KEY` in the repo-root `.env`):

```bash
python cfo_orchestrator.py
```

Each agent also runs standalone (`python fpa_agent.py`, `python administration_agent.py`,
etc.) — in that mode it produces its own output and saves. Under the orchestrator
those are suppressed so there is exactly **one** CFO gate, not one per agent.

## Design decisions (the "why")

- **Single source of numbers.** All agents import `finance_core`, so they
  cannot disagree on a figure by construction. The orchestrator still runs
  `cross_checks` (e.g. Controller's operating income must equal FP&A's actual,
  Treasury's burn must equal −operating income) — a reliability control that
  catches future drift before it reaches the board, not after.
- **One human gate, not many.** When orchestrated, sub-agents contribute
  analysis and flags only; the CFO assembles the consolidated board pack and
  owns the single human-in-the-loop approval. Standalone runs keep their own
  gate for solo use.
- **Hierarchical orchestration.** Administration is itself a sub-orchestrator:
  the CFO delegates the administrative function to it, and it coordinates
  AR/AP/Tax and rolls their flags into one report. The CFO sees six reports,
  not nine — the hierarchy mirrors a real org and keeps the top level clean.
- **Escalations don't double-count.** Each risk has exactly one owner:
  Controller → operating loss; Treasury → runway and 13-week cash; Administration
  (via its sub-agents) → overdue receivables, overdue payables, overdue tax;
  FP&A → material *unfavorable* budget variances; Strategic Finance → capital
  efficiency and whether growth alone reaches breakeven; Internal Controls →
  *control failures only* (books don't tie, missing FX, future-dated postings,
  duplicate or unauthorized disbursements) — never a business risk another agent
  already owns. The overdue-AR flag, for instance, was deliberately moved off the
  Controller onto the AR agent so it has a single owner.
- **Assurance is a distinct lens.** Internal Controls does not measure business
  performance; it tests the *integrity of the data and the process*. Its checks
  are deterministic and **proven with a tamper test**: corrupt the trial balance
  or drop an FX rate and the relevant control flips to FAIL, so it is a real
  control with teeth, not a no-op that always passes.
- **Two variance lenses in FP&A.** MoM ("how did we move vs last month") and
  budget-vs-actual ("did we hit the plan") answer different questions; the
  office reports both. Budget-vs-actual reuses the verified `finance_core`
  engine (favorable/unfavorable by line type, 5% / USD 20k materiality).
- **Numbers by code, prose by model.** No LLM call is asked to compute or
  recompute a figure; the model receives the numbers and explains them.

## Relationship to `orchestration/`

`orchestration/` holds the earlier fixed-sequence operating model (close →
cash → reporting). The CFO office is the same idea evolved into a
**shared-state, multi-agent team** with a coordinating CFO. Both reuse
`finance_core` as the single deterministic source of numbers.
