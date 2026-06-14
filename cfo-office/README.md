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
| **Controller** | `controller_agent.py` | Close review: P&L internal consistency, margins, AR overdue, risk flags |
| **Treasury** | `treasury_agent.py` | Liquidity: cash, monthly burn, runway |
| **FP&A** | `fpa_agent.py` | Forecast (next period), MoM variance, **budget-vs-actual** variance, anomalies |
| **CFO** | `cfo_orchestrator.py` | Runs the three, reconciles them, consolidates escalations, single HITL, board pack |

## The shared state (`shared_state.py`)

`CFOContext` is the common "book": every agent writes its structured result
and flags with `ctx.put(agent, payload)`, reads peers with `ctx.get(...)`, and
every step is appended to an audit trail. Persisted to `cfo_state.json`.
Communication goes *through the book*, not a free-form mesh — that is what
makes the system auditable: you can see who wrote what, and when.

## How the office runs

```
CFO orchestrator
  ├─ 1) Controller.run(ctx)   → close, margins, AR        + flags
  ├─ 2) Treasury.run(ctx)     → cash, burn, runway        + flags
  ├─ 3) FP&A.run(ctx)         → forecast, variances, anomalies + flags
  ├─ 4) cross_checks(ctx)     → agents must agree on shared numbers
  ├─ 5) gather_escalations    → consolidate flags by severity
  ├─ 6) hitl_gate             → ONE human approval if serious flags
  └─ 7) board pack + actions  → consolidated, fixed only on approval
```

Run the whole office (needs `ANTHROPIC_API_KEY` in the repo-root `.env`):

```bash
python cfo_orchestrator.py
```

Each agent also runs standalone (`python fpa_agent.py`, etc.) — in that mode it
produces its own board pack and its own gate. Under the orchestrator those are
suppressed so there is exactly **one** CFO gate, not four.

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
- **Escalations don't double-count.** Controller escalates the operating loss
  and overdue AR; Treasury escalates runway; FP&A escalates only *unfavorable*
  material variances vs budget. Each risk has one owner.
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
